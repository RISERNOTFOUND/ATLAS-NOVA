"""ATLAS stats extension: /stats commands, join/leave tracking, trends and scheduled reports.

Built ON TOP of the existing atlas_stats system: it reads the same atlas_stats.json store through the
running StatsCog and never rewrites or migrates it. New data (joins/leaves) lives in atlas.db.
Every report states what was actually tracked, what was recovered from Discord history (messages
only, via !kavstatbackfill) and what cannot be recovered.
"""
import asyncio
import io
import secrets
import time
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

import atlas_stats_core as core
from common import checks, ui
from common.config import Module, Option
from common.timeparse import get_tz

PERIOD_FOR = {"daily": "24h", "weekly": "7d", "monthly": "30d", "alltime": "all"}
PERIOD_CHOICES = [app_commands.Choice(name=n, value=v) for n, v in (("24 hours", "24h"), ("7 days", "7d"), ("15 days", "15d"), ("30 days", "30d"), ("all time", "all"))]


def window_sum(users, start_key, end_key=None):
    """Sum hourly buckets with start_key <= hour < end_key across users. Returns (bucket, active_user_ids)."""
    total, active = core.new_bucket(), set()
    for uid, u in users.items():
        hit = False
        for hk, b in u["h"].items():
            if hk >= start_key and (end_key is None or hk < end_key):
                core.merge(total, b)
                if b.get("m") or b.get("v"):
                    hit = True
        if hit:
            active.add(uid)
    return total, active


def trend(cur, prev):
    if prev == 0:
        return "new" if cur else "—"
    change = round(100 * (cur - prev) / prev)
    return f"{'▲' if change > 0 else '▼' if change < 0 else '■'} {abs(change)}%"


def flow_chart(labels, joins, leaves):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    with core._chart_lock:
        fig = Figure(figsize=(8.4, 3.0), dpi=150, facecolor=core.BG)
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        core._style(ax)
        x = list(range(len(labels)))
        ax.bar([i - 0.2 for i in x], joins, width=0.4, color=core.CYAN, label="Joins")
        ax.bar([i + 0.2 for i in x], leaves, width=0.4, color=core.PINK, label="Leaves")
        ax.set_xticks(x)
        step = max(1, len(labels) // 12)
        ax.set_xticklabels([l if i % step == 0 else "" for i, l in enumerate(labels)])
        ax.set_ylim(0, max(1, max(joins + leaves, default=0)) * 1.3)
        leg = ax.legend(loc="upper left", frameon=False, fontsize=8, ncol=2)
        for t in leg.get_texts():
            t.set_color(core.TEXT)
        fig.subplots_adjust(left=0.05, right=0.98, top=0.93, bottom=0.12)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=core.BG)
        return buf.getvalue()


class StatsUnavailable(Exception):
    pass


MEDALS = ("🥇", "🥈", "🥉", "4.", "5.")


class StatsExtension(commands.Cog, name="StatsExtension"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("statsx_001", """
            CREATE TABLE IF NOT EXISTS member_flow (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL, kind TEXT NOT NULL, ts REAL NOT NULL, is_bot INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_flow_guild_ts ON member_flow(guild_id, ts);
            CREATE TABLE IF NOT EXISTS stats_reports_sent (guild_id INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL,
                sent_at TEXT NOT NULL, PRIMARY KEY (guild_id, kind, period))""")
        bot.registry.register(Module("statistics", "Statistics reports", "📈",
                                     "Join/leave tracking and scheduled weekly/monthly activity reports. (Message/voice tracking by `!kavstat` is always on.)", [
            Option("track_joins", "Track joins/leaves", "bool", True),
            Option("report_channel", "Report channel", "channel", None, "Where weekly/monthly reports are posted"),
            Option("weekly_report", "Weekly report (Mon 10:00)", "bool", False),
            Option("monthly_report", "Monthly report (1st, 10:00)", "bool", False),
            Option("timezone", "Report timezone", "tz", "Asia/Kolkata"),
        ], default_enabled=True))

    async def cog_load(self):
        self.report_loop.start()

    async def cog_unload(self):
        self.report_loop.cancel()

    @property
    def stats(self):
        return self.bot.get_cog("StatsCog")

    # ----------------------------------------------------------- tracking
    @commands.Cog.listener()
    async def on_member_join(self, member):
        if self.bot.registry.get(member.guild.id, "statistics").get("track_joins", True):
            self.db.insert("INSERT INTO member_flow(guild_id, user_id, kind, ts, is_bot) VALUES (?,?,?,?,?)",
                           (member.guild.id, member.id, "join", time.time(), int(member.bot)))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if self.bot.registry.get(member.guild.id, "statistics").get("track_joins", True):
            self.db.insert("INSERT INTO member_flow(guild_id, user_id, kind, ts, is_bot) VALUES (?,?,?,?,?)",
                           (member.guild.id, member.id, "leave", time.time(), int(member.bot)))

    def flow_counts(self, guild_id, since_ts, until_ts=None):
        rows = self.db.fetchall("SELECT kind, count(*) c FROM member_flow WHERE guild_id=? AND ts>=? AND ts<? AND is_bot=0 GROUP BY kind",
                                (guild_id, since_ts, until_ts or time.time() + 1))
        out = {"join": 0, "leave": 0}
        for r in rows:
            out[r["kind"]] = r["c"]
        return out

    def flow_tracked_since(self, guild_id):
        return self.db.scalar("SELECT min(ts) FROM member_flow WHERE guild_id=?", (guild_id,))

    # ----------------------------------------------------------- helpers
    def accuracy_note(self, guild):
        st = self.stats
        if st is None:
            return "Stats engine not running yet."
        g = st.store.guild(guild.id)
        since = datetime.fromtimestamp(g["since"], core.TZ)
        note = f"Tracked since {since:%d %b %Y}."
        if g.get("backfilled"):
            note += " Older messages (≤30 days) were recovered from Discord history."
        note += " Voice, reactions, games and joins from before tracking can't be recovered."
        return note

    def need_stats(self, target):
        if self.stats is None:
            raise StatsUnavailable("The stats engine hasn't started yet. Try again in a minute.")
        return self.stats

    async def chart_file(self, func, *args):
        st = self.stats
        if st is None or not st.charts_ok:
            return None, None
        return await st.make_chart(func, *args)

    async def overview_embed(self, guild, period):
        st = self.stats
        store = st.store
        users = store.guild(guild.id)["users"]
        hours = core.PERIODS[period][1]
        label = core.PERIODS[period][0]
        e = discord.Embed(title=f"📈 {guild.name} · {label}", colour=ui.PINK, timestamp=discord.utils.utcnow())
        if hours is None:
            totals = store.all_totals(guild.id, "all")
            agg = core.new_bucket()
            for t in totals.values():
                core.merge(agg, t)
            active = set(totals)
            prev_line = ""
        else:
            start = core.hour_key(time.time() - hours * 3600)
            prev_start = core.hour_key(time.time() - 2 * hours * 3600)
            agg, active = window_sum(users, start)
            prev, prev_active = window_sum(users, prev_start, start)
            prev_line = f"vs previous {label.lower().replace('last ', '')}: messages {trend(agg['m'], prev['m'])} · voice {trend(agg['v'], prev['v'])} · active members {trend(len(active), len(prev_active))}"
        e.add_field(name="💬 Messages", value=f"**{agg['m']:,}** ({agg['r']:,} replies)", inline=True)
        e.add_field(name="🎙️ Voice time", value=f"**{core.fmt_dur(agg['v'])}**", inline=True)
        e.add_field(name="👥 Active members", value=f"**{len(active)}**", inline=True)
        e.add_field(name="❤️ Reactions", value=f"{agg['x']:,}", inline=True)
        e.add_field(name="📎 Attachments", value=f"{agg['a']:,}", inline=True)
        if hours is not None:
            flow = self.flow_counts(guild.id, time.time() - hours * 3600)
            tracked = self.flow_tracked_since(guild.id)
            e.add_field(name="🚪 Joins / leaves", value=f"+{flow['join']} / −{flow['leave']}" + ("" if tracked else "\n-# not tracked yet"), inline=True)
        top_ch = core.top(agg["mc"], 3)
        if top_ch:
            e.add_field(name="📍 Busiest channels", value="\n".join(f"<#{c}> · {v:,}" for c, v in top_ch), inline=False)
        if prev_line:
            e.add_field(name="Trend", value=prev_line, inline=False)
        e.set_footer(text=self.accuracy_note(guild))
        labels, msgs, vh = store.series(guild.id, None, period)
        file, name = await self.chart_file(core.user_chart, labels, msgs, vh)
        if file:
            e.set_image(url=f"attachment://{name}")
        return e, file

    async def send(self, ctx, embed, file=None):
        kwargs = {"embed": embed}
        if file:
            kwargs["file"] = file
        if ctx.interaction is None:
            await ctx.reply(mention_author=False, **kwargs)
        else:
            await ctx.send(**kwargs)

    # ----------------------------------------------------------- commands
    @commands.hybrid_group(name="stats", fallback="overview", description="Server activity statistics")
    @commands.guild_only()
    @app_commands.choices(period=PERIOD_CHOICES)
    async def stats_group(self, ctx, period: str = "7d"):
        """Server activity overview with trend vs the previous period."""
        self.need_stats(ctx)
        if ctx.interaction:
            await ctx.defer()
        embed, file = await self.overview_embed(ctx.guild, core.parse_period(period, "7d"))
        await self.send(ctx, embed, file)

    async def _period(self, ctx, key):
        self.need_stats(ctx)
        if ctx.interaction:
            await ctx.defer()
        embed, file = await self.overview_embed(ctx.guild, PERIOD_FOR[key])
        await self.send(ctx, embed, file)

    @stats_group.command(name="daily", description="Last 24 hours")
    async def daily(self, ctx):
        await self._period(ctx, "daily")

    @stats_group.command(name="weekly", description="Last 7 days")
    async def weekly(self, ctx):
        await self._period(ctx, "weekly")

    @stats_group.command(name="monthly", description="Last 30 days")
    async def monthly(self, ctx):
        await self._period(ctx, "monthly")

    @stats_group.command(name="alltime", description="Everything tracked")
    async def alltime(self, ctx):
        await self._period(ctx, "alltime")

    @stats_group.command(name="member", description="One member's activity report")
    @app_commands.choices(period=PERIOD_CHOICES)
    async def member(self, ctx, member: discord.Member = None, period: str = "30d"):
        st = self.need_stats(ctx)
        if ctx.interaction:
            await ctx.defer()
        embed, file = await st.render_user(ctx.guild, member or ctx.author, core.parse_period(period))
        await self.send(ctx, embed, file)

    @stats_group.command(name="channel", description="Channel activity ranking, or one channel's numbers")
    @app_commands.choices(period=PERIOD_CHOICES)
    async def channel(self, ctx, channel: discord.TextChannel = None, period: str = "30d"):
        st = self.need_stats(ctx)
        period = core.parse_period(period)
        totals = st.store.all_totals(ctx.guild.id, period)
        per_channel, per_user = {}, {}
        for uid, t in totals.items():
            for cid, v in t["mc"].items():
                per_channel[cid] = per_channel.get(cid, 0) + v
                if channel and cid == str(channel.id):
                    per_user[uid] = per_user.get(uid, 0) + v
        label = core.PERIODS[period][0]
        if channel:
            rows = core.top(per_user, 10)
            e = discord.Embed(title=f"📍 #{channel.name} · {label}", colour=ui.PINK,
                              description=f"**{per_channel.get(str(channel.id), 0):,}** messages from **{len(per_user)}** members")
            if rows:
                e.add_field(name="Top members", value="\n".join(f"`{i}.` {st.who(ctx.guild, u)} · {v:,}" for i, (u, v) in enumerate(rows, 1)), inline=False)
        else:
            rows = core.top(per_channel, 15)
            e = discord.Embed(title=f"📍 Channel ranking · {label}", colour=ui.PINK,
                              description="\n".join(f"`{i}.` `{core.bar(v, rows[0][1])}` {st.chan(ctx.guild, c)} · **{v:,}**" for i, (c, v) in enumerate(rows, 1)) or "No messages tracked in this period.")
        e.set_footer(text=self.accuracy_note(ctx.guild))
        await self.send(ctx, e)

    @stats_group.command(name="voice", description="Voice participation ranking")
    @app_commands.choices(period=PERIOD_CHOICES)
    async def voice(self, ctx, period: str = "30d"):
        st = self.need_stats(ctx)
        period = core.parse_period(period)
        totals = st.store.all_totals(ctx.guild.id, period)
        users = sorted(((u, t["v"], t["u"]) for u, t in totals.items() if t["v"]), key=lambda r: r[1], reverse=True)
        channels = {}
        for t in totals.values():
            for cid, v in t["vc"].items():
                channels[cid] = channels.get(cid, 0) + v
        label = core.PERIODS[period][0]
        e = discord.Embed(title=f"🎙️ Voice participation · {label}", colour=ui.PINK)
        e.add_field(name="Members", value="\n".join(f"`{i}.` {st.who(ctx.guild, u)} · **{core.fmt_dur(v)}** ({round(100 * un / v) if v else 0}% unmuted)" for i, (u, v, un) in enumerate(users[:12], 1)) or "No voice time tracked.", inline=False)
        rows = core.top(channels, 6)
        if rows:
            e.add_field(name="Channels", value="\n".join(f"{st.chan(ctx.guild, c)} · {core.fmt_dur(v)}" for c, v in rows), inline=False)
        e.add_field(name="Totals", value=f"{len(users)} members · {core.fmt_dur(sum(v for _, v, _ in users))} combined", inline=False)
        e.set_footer(text=self.accuracy_note(ctx.guild))
        chart = [(st.who_plain(ctx.guild, u), round(v / 3600, 1)) for u, v, _ in users[:8]]
        file, name = await self.chart_file(core.server_chart, [], chart)
        if file:
            e.set_image(url=f"attachment://{name}")
        await self.send(ctx, e, file)

    @stats_group.command(name="joins", description="Join and leave trends")
    async def joins(self, ctx, days: app_commands.Range[int, 1, 90] = 30):
        tz = core.TZ
        now = datetime.now(tz)
        dates = [(now - timedelta(days=i)).date() for i in range(days - 1, -1, -1)]
        start = datetime.combine(dates[0], datetime.min.time(), tz).timestamp()
        rows = self.db.fetchall("SELECT kind, ts FROM member_flow WHERE guild_id=? AND ts>=? AND is_bot=0", (ctx.guild.id, start))
        joins, leaves = {d: 0 for d in dates}, {d: 0 for d in dates}
        for r in rows:
            d = datetime.fromtimestamp(r["ts"], tz).date()
            (joins if r["kind"] == "join" else leaves)[d] = (joins if r["kind"] == "join" else leaves).get(d, 0) + 1
        tracked = self.flow_tracked_since(ctx.guild.id)
        e = discord.Embed(title=f"🚪 Joins & leaves · last {days} days", colour=ui.PINK,
                          description=f"Joined **{sum(joins.values())}** · Left **{sum(leaves.values())}** · Net **{sum(joins.values()) - sum(leaves.values()):+d}**")
        e.set_footer(text=(f"Joins/leaves tracked since {datetime.fromtimestamp(tracked, tz):%d %b %Y}. " if tracked else "Joins/leaves start being tracked now. ")
                       + "Earlier joins can't be recovered (member join dates exist, but departures don't).")
        file, name = await self.chart_file(flow_chart, [str(d.day) for d in dates], [joins[d] for d in dates], [leaves[d] for d in dates])
        if file:
            e.set_image(url=f"attachment://{name}")
        await self.send(ctx, e, file)

    @stats_group.command(name="report", description="Full community report (admins)")
    @app_commands.choices(period=[app_commands.Choice(name="weekly", value="7d"), app_commands.Choice(name="monthly", value="30d")])
    async def report(self, ctx, period: str = "7d"):
        if not checks.is_admin(ctx.author):
            return await ui.reply(ctx, embed=ui.err("Reports are for admins (Manage Server)."))
        self.need_stats(ctx)
        if ctx.interaction:
            await ctx.defer()
        embeds, file = await self.build_report(ctx.guild, core.parse_period(period, "7d"))
        kwargs = {"embeds": embeds}
        if file:
            kwargs["file"] = file
        await ctx.send(**kwargs)

    async def build_report(self, guild, period):
        main, file = await self.overview_embed(guild, period)
        main.title = f"🗞️ Community report · {core.PERIODS[period][0]}"
        st = self.stats
        totals = st.store.all_totals(guild.id, period)
        chat = sorted(((u, t["m"]) for u, t in totals.items() if t["m"]), key=lambda r: r[1], reverse=True)[:5]
        voice = sorted(((u, t["v"]) for u, t in totals.items() if t["v"]), key=lambda r: r[1], reverse=True)[:5]
        extra = discord.Embed(colour=ui.PINK)
        extra.add_field(name="💬 Most active chatters", value="\n".join(f"{MEDALS[i]} {st.who(guild, u)} · {m:,}" for i, (u, m) in enumerate(chat)) or "—", inline=True)
        extra.add_field(name="🎙️ Voice regulars", value="\n".join(f"{st.who(guild, u)} · {core.fmt_dur(v)}" for u, v in voice) or "—", inline=True)
        hours = core.PERIODS[period][1] or 24 * 30
        since = time.time() - hours * 3600
        newcomers = self.db.fetchall("SELECT DISTINCT user_id FROM member_flow WHERE guild_id=? AND kind='join' AND ts>=? AND is_bot=0", (guild.id, since))
        engaged = [r["user_id"] for r in newcomers if str(r["user_id"]) in totals]
        extra.add_field(name="🌱 New-member engagement", value=f"{len(engaged)} of {len(newcomers)} newcomers have chatted or joined voice", inline=False)
        for name, sql in (("📊 Polls", "SELECT count(*) FROM polls WHERE guild_id=? AND created_ts>=?"),
                          ("📅 Events", "SELECT count(*) FROM events WHERE guild_id=? AND start_ts>=?"),
                          ("🎫 Tickets", "SELECT count(*) FROM tickets WHERE guild_id=? AND created_ts>=?"),
                          ("💡 Suggestions", "SELECT count(*) FROM suggestions WHERE guild_id=? AND created_ts>=?")):
            try:
                count = self.db.scalar(sql, (guild.id, since), 0)
                extra.add_field(name=name, value=str(count), inline=True)
            except Exception:
                pass
        try:
            votes = self.db.scalar("SELECT count(DISTINCT v.user_id) FROM poll_votes v JOIN polls p ON p.id=v.poll_id WHERE p.guild_id=? AND p.created_ts>=?", (guild.id, since), 0)
            rsvps = self.db.scalar("SELECT count(*) FROM event_rsvps r JOIN events e ON e.id=r.event_id WHERE e.guild_id=? AND e.start_ts>=? AND r.status='going'", (guild.id, since), 0)
            extra.add_field(name="🙋 Participation", value=f"{votes} poll voters · {rsvps} event RSVPs (going)", inline=False)
        except Exception:
            pass
        return [main, extra], file

    # ----------------------------------------------------------- scheduled reports
    @tasks.loop(minutes=5)
    async def report_loop(self):
        if self.stats is None:
            return
        for guild in self.bot.guilds:
            settings = self.bot.registry.get(guild.id, "statistics")
            if not settings.get("enabled") or not settings.get("report_channel"):
                continue
            channel = guild.get_channel(int(settings["report_channel"]))
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                tz = get_tz(settings.get("timezone") or "Asia/Kolkata")
            except ValueError:
                tz = core.TZ
            now = datetime.now(tz)
            if now.hour < 10:
                continue
            jobs = []
            if settings.get("weekly_report") and now.weekday() == 0:
                jobs.append(("weekly", now.strftime("%G-W%V"), "7d"))
            if settings.get("monthly_report") and now.day == 1:
                jobs.append(("monthly", now.strftime("%Y-%m"), "30d"))
            for kind, period_key, period in jobs:
                try:
                    self.db.execute("INSERT INTO stats_reports_sent(guild_id, kind, period, sent_at) VALUES (?,?,?,?)",
                                    (guild.id, kind, period_key, now.isoformat()))
                except Exception:
                    continue  # already sent (primary key) -> duplicate prevention
                try:
                    embeds, file = await self.build_report(guild, period)
                    kwargs = {"embeds": embeds}
                    if file:
                        kwargs["file"] = file
                    await channel.send(**kwargs)
                except discord.HTTPException:
                    self.db.execute("DELETE FROM stats_reports_sent WHERE guild_id=? AND kind=? AND period=?", (guild.id, kind, period_key))

    @report_loop.before_loop
    async def before_report(self):
        await self.bot.wait_until_ready()

    async def cog_command_error(self, ctx, error):
        from common.errors import unwrap
        original = unwrap(error)
        if isinstance(original, StatsUnavailable):
            ctx._atlas_error_handled = True
            await ui.reply(ctx, embed=ui.err(str(original)))


async def setup(bot):
    await bot.add_cog(StatsExtension(bot))
