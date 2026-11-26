"""ATLAS's stat bot: tracks messages, replies, voice time, reactions and games, and shows them as elegant embeds with charts.

Commands:  !kavstat [@member] [24h|7d|15d|30d|all]     personal report (with their profile picture)
           !kavstatall [period]                         server leaderboard overview (server icon)
           !kavstatvc / !kavstatmsg / !kavstatchannels / !kavstatgames [period]
           !kavstatbackfill                             (Manage Server) import the last 30 days of messages
"""
import asyncio
import io
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks

import atlas_stats_core as core

LIBS_DIR = Path(__file__).resolve().parent / "atlas_libs"
if LIBS_DIR.exists() and str(LIBS_DIR) not in sys.path:
    sys.path.insert(0, str(LIBS_DIR))

PINK = discord.Colour.from_rgb(255, 126, 182)
MEDALS = ("🥇", "🥈", "🥉")
CATEGORIES = {
    "all": "🏆 Overview", "chat": "💬 Top chatters", "voice": "🎙️ Top VC sitters",
    "channels": "📍 Busiest text channels", "vcs": "🔊 Busiest voice channels", "games": "🎮 Most played games",
}


def esc(text):
    return discord.utils.escape_markdown(str(text))


# ------------------------------------------------------------------ interactive view
class PeriodSelect(discord.ui.Select):
    def __init__(self, current):
        super().__init__(placeholder="📅 Time range", row=0, options=[
            discord.SelectOption(label=label, value=key, default=(key == current)) for key, (label, _) in core.PERIODS.items()])

    async def callback(self, interaction: discord.Interaction):
        self.view.period = self.values[0]
        await self.view.refresh(interaction)


class CategorySelect(discord.ui.Select):
    def __init__(self, current):
        super().__init__(placeholder="📊 Category", row=1, options=[
            discord.SelectOption(label=label, value=key, default=(key == current)) for key, label in CATEGORIES.items()])

    async def callback(self, interaction: discord.Interaction):
        self.view.category = self.values[0]
        await self.view.refresh(interaction)


class StatsView(discord.ui.View):
    def __init__(self, author_id, renderer, period, category=None):
        super().__init__(timeout=300)
        self.author_id, self.renderer, self.period, self.category, self.message = author_id, renderer, period, category, None
        self.period_select = PeriodSelect(period)
        self.add_item(self.period_select)
        self.category_select = None
        if category is not None:
            self.category_select = CategorySelect(category)
            self.add_item(self.category_select)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Run `!kavstat` yourself to get your own panel 💗", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, file = await self.renderer(self.period, self.category)
        for opt in self.period_select.options:
            opt.default = opt.value == self.period
        if self.category_select:
            for opt in self.category_select.options:
                opt.default = opt.value == self.category
        await interaction.edit_original_response(embed=embed, attachments=[file] if file else [], view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ the cog
class StatsCog(commands.Cog):
    def __init__(self, bot, path, owner_ids):
        self.bot = bot
        self.store = core.StatStore(path)
        self.owner_ids = set(owner_ids)
        self.voice_open, self.game_open = {}, {}
        self.backfilling = set()
        self.last_use = {}
        self.ticks = 0
        self.charts_ok = True

    async def cog_load(self):
        self.tick.start()
        asyncio.create_task(self._prefetch_charts())

    async def _prefetch_charts(self):
        self.charts_ok = await asyncio.to_thread(core.ensure_matplotlib, LIBS_DIR)

    async def cog_unload(self):
        self.tick.cancel()
        now = time.time()
        for key, s in list(self.voice_open.items()):
            self.credit_voice(key, s, now)
        for key, s in list(self.game_open.items()):
            self.credit_game(key, s, now)
        try:
            self.store.write(self.store.dumps())
        except OSError as exc:
            print(f"[Stats] final save failed: {exc}")

    # -------- bootstrap (called from ATLAS's on_ready; safe to call repeatedly)
    async def bootstrap(self):
        for guild in self.bot.guilds:
            self.store.guild(guild.id)
            afk = guild.afk_channel
            for vc in guild.voice_channels:
                if afk and vc.id == afk.id:
                    continue
                for m in vc.members:
                    key = (guild.id, m.id)
                    if not m.bot and key not in self.voice_open and m.voice:
                        self.voice_open[key] = {"cid": vc.id, "last": time.time(), "muted": self._muted(m.voice)}
            if self.bot.intents.presences:
                for m in guild.members:
                    game = self.game_of(m)
                    if game and not m.bot and (guild.id, m.id) not in self.game_open:
                        self.game_open[(guild.id, m.id)] = {"game": game, "last": time.time()}
        print(f"[Stats] tracking {len(self.bot.guilds)} server(s). Games tracking: {'on' if self.bot.intents.presences else 'OFF (enable Presence Intent)'}")

    @staticmethod
    def _muted(state):
        return bool(state.self_mute or state.self_deaf or state.mute or state.deaf)

    @staticmethod
    def game_of(member):
        for activity in getattr(member, "activities", ()) or ():
            if activity.type == discord.ActivityType.playing and getattr(activity, "name", None):
                return activity.name[:40]
        return None

    def credit_voice(self, key, s, now):
        dt = max(0.0, min(now - s["last"], 7200))
        s["last"] = now
        if dt >= 1:
            self.store.add_voice(key[0], key[1], s["cid"], dt, not s["muted"])

    def credit_game(self, key, s, now):
        dt = max(0.0, min(now - s["last"], 7200))
        s["last"] = now
        if dt >= 1:
            self.store.add_game(key[0], key[1], s["game"], dt)

    # -------- tracking listeners
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot or message.webhook_id:
            return
        is_reply = message.reference is not None and message.type in (discord.MessageType.default, discord.MessageType.reply)
        channel_id = getattr(message.channel, "parent_id", None) or message.channel.id
        self.store.add_message(message.guild.id, message.author.id, channel_id, is_reply, len(message.attachments))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.guild_id and payload.member and not payload.member.bot:
            self.store.add_reaction(payload.guild_id, payload.user_id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        key, now = (member.guild.id, member.id), time.time()
        session = self.voice_open.get(key)
        if session:
            self.credit_voice(key, session, now)
        afk = member.guild.afk_channel
        if after.channel is not None and (afk is None or after.channel.id != afk.id):
            self.voice_open[key] = {"cid": after.channel.id, "last": now, "muted": self._muted(after)}
        else:
            self.voice_open.pop(key, None)

    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        if after.bot:
            return
        key, now = (after.guild.id, after.id), time.time()
        game, session = self.game_of(after), self.game_open.get(key)
        if session and session["game"] == game:
            return
        if session:
            self.credit_game(key, session, now)
            self.game_open.pop(key, None)
        if game:
            self.game_open[key] = {"game": game, "last": now}

    @tasks.loop(seconds=60)
    async def tick(self):
        now = time.time()
        for key, s in list(self.voice_open.items()):
            self.credit_voice(key, s, now)
        for key, s in list(self.game_open.items()):
            self.credit_game(key, s, now)
        self.ticks += 1
        if self.ticks % 60 == 0:
            self.store.rollup()
        if self.store.dirty:
            payload = self.store.dumps()
            try:
                await asyncio.to_thread(self.store.write, payload)
            except OSError as exc:
                self.store.dirty = True
                print(f"[Stats] save failed: {exc}")

    @tick.before_loop
    async def before_tick(self):
        await self.bot.wait_until_ready()

    # -------- helpers
    def throttled(self, user_id):
        now = time.monotonic()
        if now - self.last_use.get(user_id, 0) < 3:
            return True
        self.last_use[user_id] = now
        return False

    def who(self, guild, uid):
        m = guild.get_member(int(uid))
        return esc(m.display_name) if m else f"<@{uid}>"

    @staticmethod
    def chan(guild, cid):
        return f"<#{cid}>" if guild.get_channel(int(cid)) else "*deleted channel*"

    async def make_chart(self, func, *args):
        if not self.charts_ok:
            return None, None
        try:
            png = await asyncio.to_thread(func, *args)
        except Exception as exc:
            print(f"[Stats] chart failed: {type(exc).__name__}: {exc}")
            return None, None
        name = f"kavstat-{secrets.token_hex(3)}.png"
        return discord.File(io.BytesIO(png), filename=name), name

    # -------- renderers
    async def render_user(self, guild, member, period):
        store, gid = self.store, guild.id
        t = store.user_total(gid, member.id, period)
        totals = store.all_totals(gid, period)
        mrank, mn = core.rank_of(totals, member.id, "m")
        vrank, vn = core.rank_of(totals, member.id, "v")
        label = core.PERIODS[period][0]
        since = datetime.fromtimestamp(store.guild(gid)["since"], core.TZ)
        peak, active_days = store.peak_hour_and_active_days(gid, member.id, period)

        embed = discord.Embed(title=f"✨ {esc(member.display_name)}'s Activity", colour=member.colour if member.colour.value else PINK,
                              timestamp=discord.utils.utcnow())
        embed.set_author(name=f"{guild.name} • ATLAS Stats", icon_url=guild.icon.url if guild.icon else None)
        embed.set_thumbnail(url=member.display_avatar.url)  # their profile picture
        embed.set_footer(text=f"Tracking since {since:%d %b %Y} • ATLAS")
        bits = [f"**{label}**"]
        if peak is not None:
            bits.append(f"⏰ peak hour **{core.fmt_hour(peak)}**")
        if active_days is not None:
            bits.append(f"🔥 active **{active_days}** day{'s' if active_days != 1 else ''}")
        embed.description = "  ·  ".join(bits)

        if not (t["m"] or t["v"] or t["x"] or t["g"]):
            embed.add_field(name="Nothing here yet", value="No activity recorded for this period. Chat, join a voice channel or play something and check back! 💗", inline=False)
            return embed, None

        embed.add_field(name="💬 Messages", value=f"**{t['m']:,}**\n`{'rank #%d of %d' % (mrank, mn) if mrank else 'unranked'}`", inline=True)
        embed.add_field(name="↩️ Replies", value=f"**{t['r']:,}**\n`{round(100 * t['r'] / t['m']) if t['m'] else 0}% of messages`", inline=True)
        embed.add_field(name="📎 Attachments", value=f"**{t['a']:,}**\n`images & files`", inline=True)
        embed.add_field(name="🎙️ Voice time", value=f"**{core.fmt_dur(t['v'])}**\n`{'rank #%d of %d' % (vrank, vn) if vrank else 'unranked'}`", inline=True)
        embed.add_field(name="🔊 Unmuted time", value=f"**{core.fmt_dur(t['u'])}**\n`{round(100 * t['u'] / t['v']) if t['v'] else 0}% of voice`", inline=True)
        embed.add_field(name="❤️ Reactions", value=f"**{t['x']:,}**\n`given`", inline=True)

        rows = core.top(t["mc"], 5)
        if rows:
            embed.add_field(name="📍 Top text channels", inline=False, value="\n".join(
                f"`{core.bar(v, rows[0][1])}` {self.chan(guild, c)} · **{v:,}**" for c, v in rows))
        rows = core.top(t["vc"], 5)
        if rows:
            embed.add_field(name="🔊 Top voice channels", inline=False, value="\n".join(
                f"`{core.bar(v, rows[0][1])}` {self.chan(guild, c)} · **{core.fmt_dur(v)}**" for c, v in rows))
        rows = core.top(t["g"], 5)
        if rows:
            embed.add_field(name="🎮 Most played games", inline=False, value="\n".join(
                f"`{core.bar(v, rows[0][1])}` {esc(g)} · **{core.fmt_dur(v)}**" for g, v in rows))
        elif not self.bot.intents.presences:
            embed.add_field(name="🎮 Games", value="Game tracking is off. An admin must enable **Presence Intent** for ATLAS in the Developer Portal.", inline=False)

        labels, msgs, voice_hours = store.series(gid, member.id, period)
        file, name = await self.make_chart(core.user_chart, labels, msgs, voice_hours)
        if file:
            embed.set_image(url=f"attachment://{name}")
        return embed, file

    async def render_board(self, guild, period, category):
        store, gid = self.store, guild.id
        totals = store.all_totals(gid, period)
        label = core.PERIODS[period][0]
        icon = guild.icon.url if guild.icon else self.bot.user.display_avatar.url
        embed = discord.Embed(title=f"🏆 {esc(guild.name)} Leaderboard", colour=PINK, timestamp=discord.utils.utcnow())
        embed.set_thumbnail(url=icon)  # server picture
        embed.set_footer(text=f"{CATEGORIES[category]} • ATLAS Stats")

        agg = core.new_bucket()
        players = {}
        for t in totals.values():
            core.merge(agg, t)
            for game in t["g"]:
                players[game] = players.get(game, 0) + 1
        head = f"**{label}**  ·  💬 **{agg['m']:,}** messages  ·  🎙️ **{core.fmt_dur(agg['v'])}** in voice  ·  👥 **{len(totals)}** active"
        if not totals:
            embed.description = head
            embed.add_field(name="No data yet", value="Stats are collected from now on. Admins can run `!kavstatbackfill` to import the last 30 days of messages.", inline=False)
            return embed, None

        chat = sorted(((u, t["m"], t["r"]) for u, t in totals.items() if t["m"]), key=lambda r: r[1], reverse=True)
        voice = sorted(((u, t["v"], t["u"]) for u, t in totals.items() if t["v"]), key=lambda r: r[1], reverse=True)

        def medal(i):
            return MEDALS[i] if i < 3 else f"`{i + 1}.`"

        def chat_lines(n):
            return "\n".join(f"{medal(i)} {self.who(guild, u)} · **{m:,}** msgs · {r:,} replies" for i, (u, m, r) in enumerate(chat[:n]))

        def voice_lines(n):
            return "\n".join(f"{medal(i)} {self.who(guild, u)} · **{core.fmt_dur(v)}**" for i, (u, v, _) in enumerate(voice[:n]))

        def chan_lines(n):
            return "\n".join(f"{medal(i)} {self.chan(guild, c)} · **{v:,}** msgs" for i, (c, v) in enumerate(core.top(agg["mc"], n)))

        def vc_lines(n):
            return "\n".join(f"{medal(i)} {self.chan(guild, c)} · **{core.fmt_dur(v)}**" for i, (c, v) in enumerate(core.top(agg["vc"], n)))

        def game_lines(n):
            return "\n".join(f"{medal(i)} {esc(g)} · **{core.fmt_dur(v)}** · {players.get(g, 0)} player{'s' if players.get(g, 0) != 1 else ''}"
                             for i, (g, v) in enumerate(core.top(agg["g"], n)))

        empty = "*no data yet*"
        if category == "all":
            embed.description = head
            embed.add_field(name="💬 Top chatters", value=chat_lines(5) or empty, inline=True)
            embed.add_field(name="🎙️ Top VC sitters", value=voice_lines(5) or empty, inline=True)
            embed.add_field(name="\u200b", value="\u200b", inline=False)
            embed.add_field(name="📍 Busiest channels", value=chan_lines(5) or empty, inline=True)
            embed.add_field(name="🔊 Busiest VCs", value=vc_lines(5) or empty, inline=True)
            embed.add_field(name="🎮 Most played games", value=game_lines(5) or (empty if self.bot.intents.presences else "*Presence Intent is off*"), inline=False)
        else:
            body = {"chat": chat_lines, "voice": voice_lines, "channels": chan_lines, "vcs": vc_lines, "games": game_lines}[category](15) or empty
            embed.description = f"{head}\n\n**{CATEGORIES[category]}**\n{body}"
            if category == "voice" and agg["vc"]:
                embed.add_field(name="🔊 Busiest VCs", value=vc_lines(5), inline=False)

        file = None
        if category in ("all", "chat", "voice"):
            chart_chat = [(self.who_plain(guild, u), m) for u, m, _ in chat[:8]]
            chart_voice = [(self.who_plain(guild, u), round(v / 3600, 1)) for u, v, _ in voice[:8]]
            file, name = await self.make_chart(core.server_chart, chart_chat, chart_voice)
            if file:
                embed.set_image(url=f"attachment://{name}")
        return embed, file

    def who_plain(self, guild, uid):
        m = guild.get_member(int(uid))
        return m.display_name if m else "user"

    async def send_panel(self, ctx, renderer, period, category=None):
        async with ctx.typing():
            embed, file = await renderer(period, category)
        view = StatsView(ctx.author.id, renderer, period, category)
        kwargs = {"embed": embed, "view": view, "mention_author": False}
        if file:
            kwargs["file"] = file
        view.message = await ctx.reply(**kwargs)

    # -------- commands
    @commands.command(name="kavstat", aliases=["atlas_stats"])
    @commands.guild_only()
    async def kavstat(self, ctx, member: Optional[discord.Member] = None, period: str = "30d"):
        """Personal activity report with graph."""
        if self.throttled(ctx.author.id):
            return
        member, period = member or ctx.author, core.parse_period(period)

        async def renderer(p, _category):
            return await self.render_user(ctx.guild, member, p)

        await self.send_panel(ctx, renderer, period)

    async def _board(self, ctx, period, category):
        if self.throttled(ctx.author.id):
            return

        async def renderer(p, c):
            return await self.render_board(ctx.guild, p, c or category)

        await self.send_panel(ctx, renderer, core.parse_period(period, "all" if category == "all" else "30d"), category)

    @commands.command(name="kavstatall", aliases=["kavboard", "kavlb"])
    @commands.guild_only()
    async def kavstatall(self, ctx, period: str = "all"):
        """Server leaderboard overview."""
        await self._board(ctx, period, "all")

    @commands.command(name="kavstatvc")
    @commands.guild_only()
    async def kavstatvc(self, ctx, period: str = "30d"):
        await self._board(ctx, period, "voice")

    @commands.command(name="kavstatmsg")
    @commands.guild_only()
    async def kavstatmsg(self, ctx, period: str = "30d"):
        await self._board(ctx, period, "chat")

    @commands.command(name="kavstatchannels")
    @commands.guild_only()
    async def kavstatchannels(self, ctx, period: str = "30d"):
        await self._board(ctx, period, "channels")

    @commands.command(name="kavstatgames")
    @commands.guild_only()
    async def kavstatgames(self, ctx, period: str = "30d"):
        await self._board(ctx, period, "games")

    @commands.command(name="kavstatbackfill")
    @commands.guild_only()
    async def kavstatbackfill(self, ctx):
        """Import the last 30 days of messages from before tracking started (Manage Server)."""
        if not (ctx.author.guild_permissions.manage_guild or ctx.author.id in self.owner_ids):
            return await ctx.reply("You need **Manage Server** for that.", mention_author=False)
        g = self.store.guild(ctx.guild.id)
        if g.get("backfilled") or ctx.guild.id in self.backfilling:
            return await ctx.reply("The backfill already ran (or is running) for this server. 💗", mention_author=False)
        self.backfilling.add(ctx.guild.id)
        await ctx.reply("📥 Importing the last 30 days of messages in the background. I'll tell you when it's done. (Voice time and games can't be recovered, only messages.)", mention_author=False)
        counted = 0
        start = discord.utils.utcnow() - timedelta(days=30)
        until = datetime.fromtimestamp(g["since"], timezone.utc)
        try:
            for channel in ctx.guild.text_channels:
                perms = channel.permissions_for(ctx.guild.me)
                if not (perms.view_channel and perms.read_message_history):
                    continue
                try:
                    async for msg in channel.history(limit=None, after=start, before=until, oldest_first=False):
                        if msg.author.bot:
                            continue
                        self.store.add_message(ctx.guild.id, msg.author.id, channel.id, msg.reference is not None,
                                               len(msg.attachments), ts=msg.created_at.timestamp())
                        counted += 1
                except discord.HTTPException:
                    continue
                await asyncio.sleep(0.5)
            g["backfilled"] = True
            self.store.dirty = True
        finally:
            self.backfilling.discard(ctx.guild.id)
        await ctx.channel.send(f"{ctx.author.mention} ✅ Backfill done: imported **{counted:,}** messages. Try `!kavstat` now!")


async def setup_stats(bot, path, owner_ids):
    cog = bot.get_cog("StatsCog")
    if cog is None:
        cog = StatsCog(bot, path, owner_ids)
        await bot.add_cog(cog)
    await cog.bootstrap()
    return cog
