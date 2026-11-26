"""ATLAS voice controls: status, diagnostics, TTS queue management, volume.

Works on ATLAS's existing TTS pipeline (the queues and worker in bot.py); nothing about Piper or the
voice model changes. Skip/stop/pause/resume act on ATLAS's own voice client only, so they can never
touch NOVA's session (NOVA is a separate bot account and process).
"""
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from common import checks, ui
from common.config import Module, Option
from common.voice import library_report, missing_voice_perms


def fmt_age(ts):
    return f"<t:{int(ts)}:R>" if ts else "—"


class AtlasVoice(commands.Cog, name="Voice"):
    def __init__(self, bot):
        self.bot = bot
        self.k = bot.atlas
        bot.registry.register(Module("voice", "Voice & TTS", "🔊", "ATLAS's TTS playback settings.", [
            Option("volume", "TTS volume (%)", "int", 100, "10–200, default 100", 10, 200),
            Option("dj_role", "Queue control role", "role", None, "Who besides moderators may skip/stop/pause (optional)"),
        ], default_enabled=True))

    def can_control(self, member):
        if checks.is_mod(member) or checks.has_perm(member, "move_members"):
            return True
        role_id = self.bot.registry.get(member.guild.id, "voice").get("dj_role")
        if role_id and any(r.id == int(role_id) for r in member.roles):
            return True
        voice = member.guild.voice_client
        # anyone sharing the voice channel with ATLAS may control playback
        return bool(voice and member.voice and member.voice.channel and member.voice.channel.id == voice.channel.id)

    def status_embed(self, guild):
        voice = guild.voice_client
        health = self.k.voice_health
        session = self.k.config.get("voice_sessions", {}).get(str(guild.id), {})
        e = discord.Embed(title="🔊 ATLAS voice status", colour=ui.GOOD if voice and voice.is_connected() else ui.WARN)
        if voice and voice.is_connected():
            humans = [m for m in voice.channel.members if not m.bot]
            e.add_field(name="Channel", value=voice.channel.mention, inline=True)
            e.add_field(name="Connected", value=fmt_age(health.connected_since.get(guild.id)), inline=True)
            e.add_field(name="Voice latency", value=f"{round(voice.latency * 1000)} ms" if voice.latency != float("inf") else "measuring…", inline=True)
            e.add_field(name=f"Listeners ({len(humans)})", value=ui.clip(", ".join(m.display_name for m in humans) or "nobody yet", 1000), inline=False)
        else:
            e.description = "Not connected." + (" Reconnecting…" if voice else "")
        queue = self.k.tts_queues.get(guild.id)
        meta = self.k.voice_meta.get(guild.id)
        playing = "⏸️ paused" if voice and voice.is_paused() else ("▶️ speaking" if voice and voice.is_playing() else "idle")
        e.add_field(name="Playback", value=playing + (f"\n“{ui.clip(meta['now'], 100)}”" if meta else ""), inline=True)
        e.add_field(name="Queue", value=f"{queue.qsize() if queue else 0} waiting", inline=True)
        e.add_field(name="Volume", value=f"{self.bot.registry.get(guild.id, 'voice').get('volume') or 100}%", inline=True)
        mode = "paused (use `!join`)" if session.get("paused") else ("24/7 auto-rejoin" if session else "not set up")
        e.add_field(name="24/7 mode", value=mode, inline=True)
        last = health.last_disconnect.get(guild.id)
        if last:
            e.add_field(name="Last disconnect", value=f"{fmt_age(last[0])} · {last[1]}", inline=True)
        return e

    @commands.command(name="vstatus", aliases=["voicestatus"])
    @commands.guild_only()
    async def vstatus(self, ctx):
        """ATLAS's voice connection, listeners, playback and queue."""
        await ui.reply(ctx, embed=self.status_embed(ctx.guild))

    @commands.command(name="vdiag", aliases=["voicediag"])
    @commands.guild_only()
    async def vdiag(self, ctx):
        """Detailed voice diagnostics: permissions, libraries, recent errors."""
        guild, me = ctx.guild, ctx.guild.me
        e = self.status_embed(guild)
        e.title = "🩺 ATLAS voice diagnostics"
        session = self.k.config.get("voice_sessions", {}).get(str(guild.id), {})
        channel = guild.get_channel(int(session["channel_id"])) if session.get("channel_id") else None
        if channel is not None:
            missing = missing_voice_perms(channel, me)
            e.add_field(name=f"Permissions in {channel.name}", value="✅ all good" if not missing else "❌ missing: " + ", ".join(missing), inline=False)
        if ctx.author.voice and ctx.author.voice.channel:
            missing = missing_voice_perms(ctx.author.voice.channel, me)
            e.add_field(name=f"Permissions in your channel ({ctx.author.voice.channel.name})", value="✅ all good" if not missing else "❌ missing: " + ", ".join(missing), inline=False)
        e.add_field(name="Libraries", value="\n".join(f"{k}: **{v}**" for k, v in library_report().items()), inline=False)
        err = self.k.voice_health.last_error.get(guild.id)
        fails = self.k.voice_health.failures.get(guild.id, 0)
        e.add_field(name="Recent connection problem", value=(f"{err[1]} ({fmt_age(err[0])}), {fails} consecutive failure(s)" if err else "none"), inline=False)
        e.add_field(name="Audit log access", value="✅ (moderator disconnects are confirmed)" if me.guild_permissions.view_audit_log
                    else "❌ give **View Audit Log** so ATLAS can tell a moderator disconnect from a network drop", inline=False)
        await ui.reply(ctx, embed=e)

    @commands.command(name="queue", aliases=["ttsqueue"])
    @commands.guild_only()
    async def queue(self, ctx):
        """Show the TTS queue."""
        q = self.k.tts_queues.get(ctx.guild.id)
        items = list(getattr(q, "_queue", []))[:15] if q else []
        lines = [f"`{i}.` {ui.clip(item.get('text', ''), 80)}" for i, item in enumerate(items, 1)]
        meta = self.k.voice_meta.get(ctx.guild.id)
        e = discord.Embed(title="🗒️ TTS queue", colour=ui.INFO, description="\n".join(lines) or "Queue is empty.")
        if meta:
            e.add_field(name="Now speaking", value=ui.clip(meta["now"], 200))
        await ui.reply(ctx, embed=e)

    async def _control(self, ctx, action):
        voice = ctx.guild.voice_client
        if not voice or not voice.is_connected():
            return await ui.reply(ctx, embed=ui.err("I'm not in voice."))
        if not self.can_control(ctx.author):
            return await ui.reply(ctx, embed=ui.err("Join my voice channel (or be a moderator / have the queue-control role) to do that."))
        if action == "skip":
            if voice.is_playing() or voice.is_paused():
                voice.stop()
                return await ui.reply(ctx, embed=ui.ok("Skipped the current line."))
            return await ui.reply(ctx, embed=ui.err("Nothing is playing."))
        if action == "stop":
            q = self.k.tts_queues.get(ctx.guild.id)
            cleared = 0
            while q and not q.empty():
                q.get_nowait()
                q.task_done()
                cleared += 1
            if voice.is_playing() or voice.is_paused():
                voice.stop()
            return await ui.reply(ctx, embed=ui.ok(f"Stopped and cleared {cleared} queued line(s). I'm staying in voice."))
        if action == "pause":
            if voice.is_playing():
                voice.pause()
                return await ui.reply(ctx, embed=ui.ok("Paused."))
            return await ui.reply(ctx, embed=ui.err("Nothing is playing."))
        if action == "resume":
            if voice.is_paused():
                voice.resume()
                return await ui.reply(ctx, embed=ui.ok("Resumed."))
            return await ui.reply(ctx, embed=ui.err("Nothing is paused."))

    @commands.command(name="skip")
    @commands.guild_only()
    async def skip(self, ctx):
        """Skip the line being spoken."""
        await self._control(ctx, "skip")

    @commands.command(name="stop", aliases=["ttsstop"])
    @commands.guild_only()
    async def stop(self, ctx):
        """Stop speaking and clear the queue (ATLAS stays in voice)."""
        await self._control(ctx, "stop")

    @commands.command(name="pause")
    @commands.guild_only()
    async def pause(self, ctx):
        await self._control(ctx, "pause")

    @commands.command(name="resume")
    @commands.guild_only()
    async def resume(self, ctx):
        await self._control(ctx, "resume")

    @commands.command(name="volume", aliases=["vol"])
    @commands.guild_only()
    async def volume(self, ctx, percent: int = None):
        """Show or set TTS volume (10–200%). Applies from the next line."""
        current = self.bot.registry.get(ctx.guild.id, "voice").get("volume") or 100
        if percent is None:
            return await ui.reply(ctx, embed=ui.embed(description=f"🔊 TTS volume is **{current}%**."))
        if not self.can_control(ctx.author):
            return await ui.reply(ctx, embed=ui.err("Join my voice channel (or be a moderator) to change the volume."))
        if not 10 <= percent <= 200:
            return await ui.reply(ctx, embed=ui.err("Volume must be between 10 and 200 percent."))
        self.bot.registry.set(ctx.guild, ctx.author, "voice", "volume", percent)
        await ui.reply(ctx, embed=ui.ok(f"TTS volume set to **{percent}%** (applies from the next line)."))


async def setup(bot):
    await bot.add_cog(AtlasVoice(bot))
