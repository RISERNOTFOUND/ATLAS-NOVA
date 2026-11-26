"""Persistent reminders: personal, server (staff), recurring. Timezone-aware, restart-safe and
claimed atomically before delivery so a reminder is never sent twice.

Replaces the old in-memory `!remind` (which was lost on restart and sent each reminder twice).
`!remind 10 text` still means "in 10 minutes", so existing usage keeps working.
"""
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option
from common.diag import user_prefs, user_tz
from common.timeparse import fmt_delta, parse_duration, parse_when

MIN_RECUR = 600
MAX_PER_USER = 25


def split_when(spec, tz):
    """Find the longest leading phrase (up to 4 words) that parses as a time. Returns (due_datetime, rest)."""
    words = spec.split()
    for n in range(min(4, len(words) - 1), 0, -1):
        try:
            return parse_when(" ".join(words[:n]), tz), " ".join(words[n:])
        except ValueError:
            continue
    raise ValueError("Start with when, e.g. `!remind 10 stretch` (minutes), `!remind 2h30m call mum`, `!remind tomorrow 9am standup`.")


class Reminders(commands.Cog, name="Reminders"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("reminders_001", """
            CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, channel_id INTEGER, user_id INTEGER NOT NULL,
                text TEXT NOT NULL, due_ts REAL NOT NULL, recur_seconds INTEGER, kind TEXT NOT NULL DEFAULT 'personal',
                status TEXT NOT NULL DEFAULT 'pending', created_ts REAL NOT NULL, sent_count INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_ts);
            CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, status)""")
        bot.registry.register(Module("reminders", "Reminders", "⏰", "Personal and server reminders that survive restarts.", [
            Option("server_reminders", "Allow server reminders", "bool", True, "Staff can schedule reminders into channels"),
            Option("default_dm", "Personal reminders by DM", "bool", True, "Otherwise they're posted where they were set"),
        ], default_enabled=True))

    async def cog_load(self):
        # Rows stuck in 'sending' (bot stopped mid-delivery) are retried once.
        self.db.execute("UPDATE reminders SET status='pending' WHERE status='sending'")
        self.loop.start()

    async def cog_unload(self):
        self.loop.cancel()

    def tz(self, user_id):
        return user_tz(self.db, user_id)

    async def add(self, target, user, guild, channel, spec, *, recur=None, kind="personal"):
        if self.db.scalar("SELECT count(*) FROM reminders WHERE user_id=? AND status='pending'", (user.id,), 0) >= MAX_PER_USER and kind == "personal":
            return await ui.reply(target, embed=ui.err(f"You already have {MAX_PER_USER} pending reminders. Cancel some with `!reminders`."))
        if recur is not None:
            due = time.time() + recur
            text = spec
        else:
            try:
                due_dt, text = split_when(spec, self.tz(user.id))
            except ValueError as exc:
                return await ui.reply(target, embed=ui.err(str(exc)))
            due = due_dt.timestamp()
        text = text.strip()
        if not text:
            return await ui.reply(target, embed=ui.err("What should I remind you about?"))
        if due - time.time() > 366 * 86400:
            return await ui.reply(target, embed=ui.err("Reminders can be at most a year ahead."))
        rid = self.db.insert("INSERT INTO reminders(guild_id, channel_id, user_id, text, due_ts, recur_seconds, kind, created_ts) VALUES (?,?,?,?,?,?,?,?)",
                             (guild.id if guild else None, channel.id if channel else None, user.id, text[:1500], due, recur, kind, time.time()))
        every = f", then every {fmt_delta(recur)}" if recur else ""
        where = f" in {channel.mention}" if kind == "server" else ""
        await ui.reply(target, embed=ui.ok(f"Reminder #{rid} set for <t:{int(due)}:f> (<t:{int(due)}:R>){every}{where}."))

    @commands.hybrid_command(name="remind", description="Set a reminder (e.g. 10, 2h30m, tomorrow 9am)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @app_commands.describe(when="When: minutes (10), a duration (2h30m), or a time (tomorrow 9am, 18:30)", text="What to remind you about")
    async def remind(self, ctx, when: str, *, text: str):
        """Set a reminder. `!remind 10 stretch`, `!remind 2h call mum`, `!remind tomorrow 9am standup`."""
        await self.add(ctx, ctx.author, ctx.guild, ctx.channel, f"{when} {text}")

    @commands.command(name="remindevery", aliases=["recurring"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def remindevery(self, ctx, interval: str, *, text: str):
        """Recurring reminder: `!remindevery 1d drink water` (minimum 10 minutes)."""
        delta = parse_duration(interval, minimum=MIN_RECUR, maximum=90 * 86400)
        if delta is None:
            return await ui.reply(ctx, embed=ui.err("Interval must be like `30m`, `6h`, `1d`, `1w` (10 minutes to 90 days)."))
        await self.add(ctx, ctx.author, ctx.guild, ctx.channel, text, recur=int(delta.total_seconds()))

    @commands.command(name="serverremind", aliases=["sremind"])
    @commands.guild_only()
    @checks.mod_only()
    async def serverremind(self, ctx, channel: discord.TextChannel, *, spec: str):
        """Staff: `!serverremind #channel <when> <text>` or `!serverremind #channel every 1d <text>`."""
        if not self.bot.registry.get(ctx.guild.id, "reminders").get("server_reminders", True):
            return await ui.reply(ctx, embed=ui.err("Server reminders are switched off here."))
        if not channel.permissions_for(ctx.guild.me).send_messages:
            return await ui.reply(ctx, embed=ui.err(f"I can't post in {channel.mention}."))
        words = spec.split(maxsplit=2)
        if words and words[0].casefold() == "every" and len(words) == 3:
            delta = parse_duration(words[1], minimum=MIN_RECUR, maximum=90 * 86400)
            if delta is None:
                return await ui.reply(ctx, embed=ui.err("Interval must be like `1h`, `1d`, `1w`."))
            return await self.add(ctx, ctx.author, ctx.guild, channel, words[2], recur=int(delta.total_seconds()), kind="server")
        await self.add(ctx, ctx.author, ctx.guild, channel, spec, kind="server")

    @commands.command(name="reminders", aliases=["myreminders"])
    async def reminders(self, ctx):
        """Your pending reminders (and this server's, for staff)."""
        rows = self.db.fetchall("SELECT * FROM reminders WHERE user_id=? AND status='pending' ORDER BY due_ts", (ctx.author.id,))
        lines = [f"`#{r['id']}` <t:{int(r['due_ts'])}:f> · {ui.clip(r['text'], 70)}" + (f" · 🔁 {fmt_delta(r['recur_seconds'])}" if r["recur_seconds"] else "") + (f" · <#{r['channel_id']}>" if r["kind"] == "server" else "") for r in rows]
        if ctx.guild and checks.is_mod(ctx.author):
            server = self.db.fetchall("SELECT * FROM reminders WHERE guild_id=? AND kind='server' AND status='pending' AND user_id<>? ORDER BY due_ts", (ctx.guild.id, ctx.author.id))
            lines += [f"`#{r['id']}` 🏛️ <t:{int(r['due_ts'])}:f> · <#{r['channel_id']}> · {ui.clip(r['text'], 60)}" for r in server]
        await ui.paginate(ctx, ui.pages_from_lines("⏰ Reminders", lines, footer="!reminder cancel <id> · !reminder edit <id> <text>"))

    @commands.group(name="reminder", invoke_without_command=True)
    async def reminder(self, ctx):
        await self.reminders(ctx)

    def _own(self, ctx, rid):
        row = self.db.fetchone("SELECT * FROM reminders WHERE id=? AND status='pending'", (rid,))
        if row is None:
            return None
        if row["user_id"] == ctx.author.id or (row["kind"] == "server" and ctx.guild and row["guild_id"] == ctx.guild.id and checks.is_mod(ctx.author)):
            return row
        return None

    @reminder.command(name="cancel", aliases=["delete", "remove"])
    async def cancel(self, ctx, rid: int):
        if not self._own(ctx, rid):
            return await ui.reply(ctx, embed=ui.err("No pending reminder of yours with that ID."))
        self.db.execute("UPDATE reminders SET status='cancelled' WHERE id=?", (rid,))
        await ui.reply(ctx, embed=ui.ok(f"Reminder #{rid} cancelled."))

    @reminder.command(name="edit")
    async def edit(self, ctx, rid: int, *, text: str):
        if not self._own(ctx, rid):
            return await ui.reply(ctx, embed=ui.err("No pending reminder of yours with that ID."))
        self.db.execute("UPDATE reminders SET text=? WHERE id=?", (text[:1500], rid))
        await ui.reply(ctx, embed=ui.ok(f"Reminder #{rid} updated."))

    @reminder.command(name="move", aliases=["reschedule"])
    async def move(self, ctx, rid: int, *, when: str):
        row = self._own(ctx, rid)
        if not row:
            return await ui.reply(ctx, embed=ui.err("No pending reminder of yours with that ID."))
        try:
            due = parse_when(when, self.tz(ctx.author.id))
        except ValueError as exc:
            return await ui.reply(ctx, embed=ui.err(str(exc)))
        self.db.execute("UPDATE reminders SET due_ts=? WHERE id=?", (due.timestamp(), rid))
        await ui.reply(ctx, embed=ui.ok(f"Reminder #{rid} moved to <t:{int(due.timestamp())}:f>."))

    # ---------------------------------------------------------------- delivery
    @tasks.loop(seconds=20)
    async def loop(self):
        now = time.time()
        for row in self.db.fetchall("SELECT * FROM reminders WHERE status='pending' AND due_ts<=? ORDER BY due_ts LIMIT 50", (now,)):
            if not self.db.claim("reminders", row["id"], expected="pending", new="sending"):
                continue
            try:
                await self.deliver(dict(row))
            finally:
                if row["recur_seconds"]:
                    nxt = row["due_ts"]
                    while nxt <= time.time():
                        nxt += row["recur_seconds"]
                    self.db.execute("UPDATE reminders SET status='pending', due_ts=?, sent_count=sent_count+1 WHERE id=?", (nxt, row["id"]))
                else:
                    self.db.execute("UPDATE reminders SET status='done', sent_count=sent_count+1 WHERE id=?", (row["id"],))

    async def deliver(self, r):
        guild = self.bot.get_guild(r["guild_id"]) if r["guild_id"] else None
        channel = (guild.get_channel(r["channel_id"]) if guild else None) or (self.bot.get_channel(r["channel_id"]) if r["channel_id"] else None)
        late = time.time() - r["due_ts"]
        late_note = f"\n-# (delivered {fmt_delta(late)} late — I was offline)" if late > 120 else ""
        if r["kind"] == "server":
            if channel:
                await ui.safe_send(channel, embed=discord.Embed(description=f"⏰ {r['text']}{late_note}", colour=ui.PINK))
            return
        user = self.bot.get_user(r["user_id"])
        wants_dm = user_prefs(self.db, r["user_id"]).get("dm_reminders", 1) and (guild is None or self.bot.registry.get(guild.id, "reminders").get("default_dm", True))
        text = f"⏰ **Reminder:** {r['text']}{late_note}"
        if user and wants_dm and await ui.safe_dm(user, text):
            return
        if channel:
            await ui.safe_send(channel, f"<@{r['user_id']}> {text}", allowed_mentions=discord.AllowedMentions(users=True))
        elif user:
            await ui.safe_dm(user, text)

    @loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Reminders(bot))
