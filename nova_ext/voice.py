"""NOVA voice controls and per-server voice settings.

Status/diagnostics, TTS queue control, volume, participants with session durations, optional member
join/leave announcements and the server AFK channel. Works on NOVA's own voice client only, so it can
never affect ATLAS's voice session (separate bot account, separate process).
"""
import time

import discord
from discord.ext import commands

from common import checks, ui
from common.config import Module, Option
from common.voice import library_report, missing_voice_perms
from common.timeparse import fmt_delta


class NovaVoice(commands.Cog, name="Voice"):
    def __init__(self, bot):
        self.bot = bot
        self.m = bot.nova
        self.joined_at = {}      # (guild_id, member_id) -> wall time they joined their current voice channel
        self.last_announce = {}
        bot.registry.register(Module("voice", "Voice", "🎧", "NOVA's TTS volume, queue control, and optional voice join/leave announcements.", [
            Option("volume", "TTS volume (%)", "int", 100, "10–200", 10, 200),
            Option("announce", "Announce voice joins/leaves", "bool", False),
            Option("announce_channel", "Announcement channel", "channel", None, "Empty = the voice channel's own chat"),
            Option("dj_role", "Queue control role", "role", None, "Besides moderators and people in NOVA's channel"),
        ], default_enabled=True))

    async def cog_load(self):
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                for m in vc.members:
                    self.joined_at.setdefault((guild.id, m.id), time.time())

    def can_control(self, member):
        if checks.is_mod(member) or checks.has_perm(member, "move_members"):
            return True
        role_id = self.bot.registry.get(member.guild.id, "voice").get("dj_role")
        if role_id and any(r.id == int(role_id) for r in member.roles):
            return True
        voice = member.guild.voice_client
        return bool(voice and member.voice and member.voice.channel and member.voice.channel.id == voice.channel.id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        key = (member.guild.id, member.id)
        if before.channel != after.channel:
            if after.channel:
                self.joined_at[key] = time.time()
            else:
                self.joined_at.pop(key, None)
        if member.bot or before.channel == after.channel:
            return
        s = self.bot.registry.get(member.guild.id, "voice")
        if not (s.get("enabled") and s.get("announce")):
            return
        if time.time() - self.last_announce.get(key, 0) < 30:
            return
        self.last_announce[key] = time.time()
        name = discord.utils.escape_markdown(member.display_name)
        channel = member.guild.get_channel(int(s["announce_channel"])) if s.get("announce_channel") else None
        if after.channel and not before.channel:
            await ui.safe_send(channel or after.channel, f"🔈 **{name}** joined {after.channel.mention}")
        elif before.channel and not after.channel:
            await ui.safe_send(channel or before.channel, f"🔇 **{name}** left {before.channel.mention}")
        else:
            await ui.safe_send(channel or after.channel, f"↪️ **{name}** moved to {after.channel.mention}")

    def status_embed(self, guild):
        voice = guild.voice_client
        health = self.m.voice_health
        entry = self.m.voice_state.get(str(guild.id)) or {}
        e = discord.Embed(title="🎧 NOVA voice status", colour=ui.GOOD if voice and voice.is_connected() else ui.WARN)
        if voice and voice.is_connected():
            since = health.connected_since.get(guild.id)
            e.add_field(name="Channel", value=voice.channel.mention)
            e.add_field(name="Session", value=fmt_delta(time.time() - since) if since else "—")
            e.add_field(name="Voice latency", value=f"{round(voice.latency * 1000)} ms" if voice.latency != float("inf") else "measuring…")
        else:
            e.description = "Not connected." + (" Reconnecting…" if voice else "")
        q = self.m.tts_queues.get(guild.id)
        playing = "⏸️ paused" if voice and voice.is_paused() else ("▶️ speaking" if voice and voice.is_playing() else "idle")
        now = self.m.tts_now.get(guild.id)
        e.add_field(name="Playback", value=playing + (f"\n“{ui.clip(now, 80)}”" if now else ""))
        e.add_field(name="Queue", value=f"{q.qsize() if q else 0} waiting")
        e.add_field(name="Volume", value=f"{self.bot.registry.get(guild.id, 'voice').get('volume') or 100}%")
        e.add_field(name="24/7 mode", value="paused (use `!mjoin`)" if entry.get("paused") else ("auto-rejoin on" if entry else "not set up"))
        last = health.last_disconnect.get(guild.id)
        if last:
            e.add_field(name="Last disconnect", value=f"<t:{int(last[0])}:R> · {last[1]}")
        return e

    @commands.command(name="mvstatus", aliases=["mvoicestatus"])
    @commands.guild_only()
    async def mvstatus(self, ctx):
        """NOVA's voice connection, playback and queue."""
        await ui.reply(ctx, embed=self.status_embed(ctx.guild))

    @commands.command(name="mvdiag", aliases=["mvoicediag"])
    @commands.guild_only()
    async def mvdiag(self, ctx):
        """NOVA's voice diagnostics: permissions, libraries, recent errors."""
        e = self.status_embed(ctx.guild)
        e.title = "🩺 NOVA voice diagnostics"
        me = ctx.guild.me
        entry = self.m.voice_state.get(str(ctx.guild.id)) or {}
        channel = ctx.guild.get_channel(int(entry["channel_id"])) if entry.get("channel_id") else None
        if channel is not None:
            missing = missing_voice_perms(channel, me)
            e.add_field(name=f"Permissions in {channel.name}", value="✅ all good" if not missing else "❌ missing: " + ", ".join(missing), inline=False)
        if ctx.author.voice and ctx.author.voice.channel:
            missing = missing_voice_perms(ctx.author.voice.channel, me)
            e.add_field(name="Permissions in your channel", value="✅ all good" if not missing else "❌ missing: " + ", ".join(missing), inline=False)
        e.add_field(name="Libraries", value="\n".join(f"{k}: **{v}**" for k, v in library_report().items()), inline=False)
        err = self.m.voice_health.last_error.get(ctx.guild.id)
        e.add_field(name="Recent connection problem", value=f"{err[1]} (<t:{int(err[0])}:R>)" if err else "none", inline=False)
        e.add_field(name="Audit log access", value="✅" if me.guild_permissions.view_audit_log else "❌ give **View Audit Log** so NOVA can tell a moderator disconnect from a network drop", inline=False)
        await ui.reply(ctx, embed=e)

    @commands.command(name="mqueue")
    @commands.guild_only()
    async def mqueue(self, ctx):
        """NOVA's TTS queue."""
        q = self.m.tts_queues.get(ctx.guild.id)
        items = list(getattr(q, "_queue", []))[:15] if q else []
        e = discord.Embed(title="🗒️ NOVA's TTS queue", colour=ui.INFO, description="\n".join(f"`{i}.` {ui.clip(t, 80)}" for i, t in enumerate(items, 1)) or "Queue is empty.")
        now = self.m.tts_now.get(ctx.guild.id)
        if now:
            e.add_field(name="Now speaking", value=ui.clip(now, 200))
        await ui.reply(ctx, embed=e)

    async def control(self, ctx, action):
        voice = ctx.guild.voice_client
        if not voice or not voice.is_connected():
            return await ui.reply(ctx, embed=ui.err("I'm not in voice."))
        if not self.can_control(ctx.author):
            return await ui.reply(ctx, embed=ui.err("Join my voice channel (or be a moderator / have the queue-control role) to do that."))
        if action == "skip":
            if voice.is_playing() or voice.is_paused():
                voice.stop()
                return await ui.reply(ctx, embed=ui.ok("Skipped."))
            return await ui.reply(ctx, embed=ui.err("Nothing is playing."))
        if action == "stop":
            q = self.m.tts_queues.get(ctx.guild.id)
            n = 0
            while q and not q.empty():
                q.get_nowait()
                q.task_done()
                n += 1
            if voice.is_playing() or voice.is_paused():
                voice.stop()
            return await ui.reply(ctx, embed=ui.ok(f"Stopped and cleared {n} line(s). Still in voice."))
        if action == "pause":
            if voice.is_playing():
                voice.pause()
                return await ui.reply(ctx, embed=ui.ok("Paused."))
            return await ui.reply(ctx, embed=ui.err("Nothing is playing."))
        if voice.is_paused():
            voice.resume()
            return await ui.reply(ctx, embed=ui.ok("Resumed."))
        await ui.reply(ctx, embed=ui.err("Nothing is paused."))

    @commands.command(name="mskip")
    @commands.guild_only()
    async def mskip(self, ctx):
        await self.control(ctx, "skip")

    @commands.command(name="mstop")
    @commands.guild_only()
    async def mstop(self, ctx):
        await self.control(ctx, "stop")

    @commands.command(name="mpause")
    @commands.guild_only()
    async def mpause(self, ctx):
        await self.control(ctx, "pause")

    @commands.command(name="mresume")
    @commands.guild_only()
    async def mresume(self, ctx):
        await self.control(ctx, "resume")

    @commands.command(name="mvolume", aliases=["mvol"])
    @commands.guild_only()
    async def mvolume(self, ctx, percent: int = None):
        """Show or set NOVA's TTS volume (10–200%)."""
        current = self.bot.registry.get(ctx.guild.id, "voice").get("volume") or 100
        if percent is None:
            return await ui.reply(ctx, embed=ui.embed(description=f"🔊 NOVA's TTS volume is **{current}%**."))
        if not self.can_control(ctx.author):
            return await ui.reply(ctx, embed=ui.err("Join my voice channel (or be a moderator) to change the volume."))
        if not 10 <= percent <= 200:
            return await ui.reply(ctx, embed=ui.err("Volume must be 10–200%."))
        self.bot.registry.set(ctx.guild, ctx.author, "voice", "volume", percent)
        await ui.reply(ctx, embed=ui.ok(f"Volume set to **{percent}%** (from the next line)."))

    @commands.command(name="mvoiceinfo", aliases=["mvcinfo", "mwhoisin"])
    @commands.guild_only()
    async def mvoiceinfo(self, ctx, channel: discord.VoiceChannel = None):
        """Who's in a voice channel and for how long (yours by default)."""
        channel = channel or (ctx.author.voice.channel if ctx.author.voice else None) or (ctx.guild.voice_client.channel if ctx.guild.voice_client else None)
        if channel is None:
            return await ui.reply(ctx, embed=ui.err("Join a voice channel or name one."))
        lines = []
        for m in channel.members:
            since = self.joined_at.get((ctx.guild.id, m.id))
            vs = m.voice
            flags = "".join(x for x, on in (("🔇", vs and (vs.self_mute or vs.mute)), ("🙉", vs and (vs.self_deaf or vs.deaf)), ("📺", vs and vs.self_stream), ("📷", vs and vs.self_video)) if on)
            lines.append(f"{'🤖' if m.bot else '👤'} **{discord.utils.escape_markdown(m.display_name)}** {flags} · " + (fmt_delta(time.time() - since) if since else "since before my restart"))
        e = discord.Embed(title=f"🔊 {channel.name}", colour=ui.INFO, description="\n".join(lines) or "Empty.")
        e.add_field(name="Limit", value=str(channel.user_limit or "none"))
        e.add_field(name="Bitrate", value=f"{channel.bitrate // 1000} kbps")
        await ui.reply(ctx, embed=e)

    @commands.command(name="mafk")
    @commands.guild_only()
    @checks.admin_only()
    async def mafk(self, ctx, channel: discord.VoiceChannel = None, minutes: int = 5):
        """Set (or clear) the server's AFK voice channel and idle timeout (1, 5, 15, 30 or 60 minutes)."""
        if minutes not in (1, 5, 15, 30, 60):
            return await ui.reply(ctx, embed=ui.err("Discord allows an AFK timeout of 1, 5, 15, 30 or 60 minutes."))
        if not ctx.guild.me.guild_permissions.manage_guild:
            return await ui.reply(ctx, embed=ui.err("I need **Manage Server** to change the AFK channel."))
        text = f"Set the AFK channel to {channel.mention} with a {minutes}-minute timeout?" if channel else "Clear the server's AFK channel?"
        if not await ui.confirm(ctx, text, confirm_label="Apply", danger=False):
            return
        await ctx.guild.edit(afk_channel=channel, afk_timeout=minutes * 60, reason=f"AFK channel set by {ctx.author}")
        await ui.reply(ctx, embed=ui.ok("AFK channel updated."))

    @commands.command(name="msettts", aliases=["mttschannel"])
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def msettts(self, ctx, channel: discord.TextChannel = None):
        """Set the text channel NOVA reads TTS messages from (defaults to current channel)."""
        target = channel or ctx.channel
        import nova as _nova
        _nova.tts_channels[str(ctx.guild.id)] = target.id
        _nova.save_state()
        await ui.reply(ctx, embed=ui.ok(f"NOVA TTS channel set to {target.mention}. Any message sent there while NOVA is in voice will be spoken."))

    @commands.command(name="mhelp")
    async def mhelp(self, ctx):
        """Show NOVA voice and TTS commands."""
        e = discord.Embed(title="NOVA Voice & TTS Commands (Prefix: !)", color=0x5865F2)
        e.add_field(
            name="TTS & Channel",
            value="`!msettts [#channel]` — Set the TTS text channel\n`!mdemo` — Test NOVA's TTS pipeline in voice\n`!mqueue` — Show queued TTS lines\n`!mskip` / `!mstop` / `!mpause` / `!mresume` — Playback controls",
            inline=False,
        )
        e.add_field(
            name="Voice Info & Settings",
            value="`!mvstatus` — Voice & TTS status\n`!mvdiag` — Voice diagnostics\n`!mvolume <0-200>` — Set volume\n`!mvoiceinfo` — Show current voice channel members\n`!mafk` — Move/set AFK channel",
            inline=False,
        )
        await ui.reply(ctx, embed=e)

    @commands.command(name="mdemo")
    @commands.guild_only()
    async def mdemo(self, ctx):
        """Test NOVA's complete TTS pipeline in the connected voice channel."""
        voice = ctx.guild.voice_client
        if not voice or not voice.is_connected():
            return await ui.reply(ctx, embed=ui.err("I am not connected to a voice channel. Join a voice channel and have me join first."))
        import asyncio, secrets
        import nova as _nova
        if not await _nova.ensure_voices():
            return await ui.reply(ctx, embed=ui.err("TTS voice model could not be loaded. Run `setup.bat` or `install_assets.py`."))
        _nova.TTS_CACHE_DIR.mkdir(exist_ok=True)
        path = _nova.TTS_CACHE_DIR / f"demo-{ctx.guild.id}-{secrets.token_hex(4)}.wav"
        try:
            await asyncio.to_thread(_nova.synthesize, "en", "Hello! NOVA text to speech is working correctly.", str(path))
            done = asyncio.Event()
            loop = asyncio.get_running_loop()
            err_box = []

            def after(error):
                if error:
                    err_box.append(error)
                path.unlink(missing_ok=True)
                loop.call_soon_threadsafe(done.set)

            voice.play(discord.FFmpegPCMAudio(str(path), executable=str(_nova.FFMPEG_PATH), options="-vn"), after=after)
            await ui.reply(ctx, embed=ui.ok("Playing NOVA TTS demo: *Hello! NOVA text to speech is working correctly.*"))
            await done.wait()
            if err_box:
                await ui.reply(ctx, embed=ui.err(f"Playback error: {err_box[0]}"))
        except Exception as exc:
            path.unlink(missing_ok=True)
            await ui.reply(ctx, embed=ui.err(f"TTS demo failed: {type(exc).__name__}: {exc}"))


async def setup(bot):
    await bot.add_cog(NovaVoice(bot))
