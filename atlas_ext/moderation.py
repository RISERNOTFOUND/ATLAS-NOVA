"""Moderation cases, moderator notes, history, softban and the shared human-review queue.

ATLAS's existing commands (warn/kick/ban/timeout/...) keep their exact behaviour and permissions;
they now also emit a `mod_action` event, which this module turns into a numbered case and a
moderation-log entry. Nothing here punishes anyone automatically.
"""
import time

import discord
from discord.ext import commands

import config as settings
from common import checks, ui
from common.config import Module, Option

ACTION_ICON = {"warn": "⚠️", "kick": "👢", "ban": "🔨", "softban": "🧹", "unban": "♻️", "timeout": "🔇", "untimeout": "🔊",
               "forgive": "🤝", "note": "📝", "automod": "🤖"}


def ts_fmt(ts):
    return f"<t:{int(ts)}:f>"


class ReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rq:(?P<id>\d+):(?P<action>resolve|dismiss)"):
    """Persistent buttons on review-queue items; re-checks moderator rights on every press."""

    def __init__(self, item_id, action):
        label, style = ("Resolve", discord.ButtonStyle.success) if action == "resolve" else ("Dismiss", discord.ButtonStyle.secondary)
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"rq:{item_id}:{action}"))
        self.item_id, self.action = int(item_id), action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"], match["action"])

    async def callback(self, interaction):
        if not checks.is_mod(interaction.user):
            return await interaction.response.send_message("Only moderators can handle review items.", ephemeral=True)
        db = interaction.client.db
        row = db.fetchone("SELECT * FROM review_queue WHERE id=? AND guild_id=?", (self.item_id, interaction.guild_id))
        if row is None:
            return await interaction.response.send_message("That review item no longer exists.", ephemeral=True)
        if row["status"] != "open":
            return await interaction.response.send_message(f"Already {row['status']}.", ephemeral=True)
        status = "resolved" if self.action == "resolve" else "dismissed"
        db.execute("UPDATE review_queue SET status=?, resolved_by=?, resolved_ts=? WHERE id=?", (status, interaction.user.id, time.time(), self.item_id))
        embed = interaction.message.embeds[0] if interaction.message and interaction.message.embeds else discord.Embed()
        embed.colour = ui.GOOD if status == "resolved" else ui.WARN
        embed.set_footer(text=f"{status.title()} by {interaction.user} · item #{self.item_id}")
        await interaction.response.edit_message(embed=embed, view=None)
        interaction.client.dispatch("review_item_closed", interaction.guild, dict(row), status, interaction.user)


def review_view(item_id):
    view = discord.ui.View(timeout=None)
    view.add_item(ReviewButton(item_id, "resolve"))
    view.add_item(ReviewButton(item_id, "dismiss"))
    return view


async def add_review_item(bot, guild, kind, summary, *, target_id=None, reporter_id=None, link=None, extra=""):
    """Create a review item and post it to the configured review channel. Used by automod, reports, verification."""
    item_id = bot.db.insert("INSERT INTO review_queue(guild_id, kind, target_id, reporter_id, summary, link, status, created_ts) VALUES (?,?,?,?,?,?,?,?)",
                            (guild.id, kind, target_id, reporter_id, summary[:1500], link, "open", time.time()))
    s = bot.registry.get(guild.id, "moderation")
    channel = guild.get_channel(int(s["review_channel"])) if s.get("review_channel") else None
    if channel is None and bot.registry.get(guild.id, "logging").get("mod_channel"):
        channel = guild.get_channel(int(bot.registry.get(guild.id, "logging")["mod_channel"]))
    e = discord.Embed(title=f"🔎 Review needed · {kind}", description=ui.clip(summary, 3500), colour=ui.WARN, timestamp=discord.utils.utcnow())
    if target_id:
        e.add_field(name="Member", value=f"<@{target_id}> (`{target_id}`)")
    if reporter_id:
        e.add_field(name="Reported by", value=f"<@{reporter_id}>")
    if link:
        e.add_field(name="Context", value=f"[jump]({link})")
    if extra:
        e.add_field(name="Details", value=ui.clip(extra, 1000), inline=False)
    e.set_footer(text=f"Review item #{item_id} · no automatic punishment was applied")
    if isinstance(channel, discord.TextChannel):
        msg = await ui.safe_send(channel, embed=e, view=review_view(item_id))
        if msg:
            bot.db.execute("UPDATE review_queue SET message_id=?, channel_id=? WHERE id=?", (msg.id, channel.id, item_id))
    return item_id


class Moderation(commands.Cog, name="Moderation"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("moderation_001", """
            CREATE TABLE IF NOT EXISTS mod_cases (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, case_no INTEGER NOT NULL,
                action TEXT NOT NULL, target_id INTEGER, moderator_id INTEGER, reason TEXT, created_ts REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cases_guild_no ON mod_cases(guild_id, case_no);
            CREATE INDEX IF NOT EXISTS idx_cases_target ON mod_cases(guild_id, target_id);
            CREATE TABLE IF NOT EXISTS mod_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, target_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL, note TEXT NOT NULL, created_ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_notes_target ON mod_notes(guild_id, target_id);
            CREATE TABLE IF NOT EXISTS review_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, kind TEXT NOT NULL,
                target_id INTEGER, reporter_id INTEGER, summary TEXT, link TEXT, status TEXT NOT NULL, created_ts REAL NOT NULL,
                resolved_by INTEGER, resolved_ts REAL, message_id INTEGER, channel_id INTEGER);
            CREATE INDEX IF NOT EXISTS idx_review_guild_status ON review_queue(guild_id, status)""")
        bot.registry.register(Module("moderation", "Moderation", "🛡️", "Case numbers, moderation log, appeal info and the human review queue.", [
            Option("review_channel", "Review queue channel", "channel", None, "Staff channel for items needing a human decision"),
            Option("dm_on_action", "DM members about actions", "bool", True, "Tell members about warns/timeouts/kicks/bans they receive"),
            Option("appeal_info", "Appeal information", "text", "", "Shown in those DMs, e.g. how to appeal", max_length=500),
            Option("staff_roles", "Extra staff roles", "roles", [], "Roles treated as staff for tickets/reports"),
        ], default_enabled=True))
        bot.add_dynamic_items(ReviewButton)

    def new_case(self, guild_id, action, target_id, moderator_id, reason):
        with self.db.transaction() as conn:
            number = (conn.execute("SELECT max(case_no) FROM mod_cases WHERE guild_id=?", (guild_id,)).fetchone()[0] or 0) + 1
            conn.execute("INSERT INTO mod_cases(guild_id, case_no, action, target_id, moderator_id, reason, created_ts) VALUES (?,?,?,?,?,?,?)",
                         (guild_id, number, action, target_id, moderator_id, (reason or "")[:1000], time.time()))
        return number

    def case_embed(self, guild, row):
        e = discord.Embed(title=f"{ACTION_ICON.get(row['action'], '•')} Case #{row['case_no']} · {row['action']}", colour=ui.WARN)
        e.add_field(name="Member", value=f"<@{row['target_id']}> (`{row['target_id']}`)")
        e.add_field(name="Moderator", value=f"<@{row['moderator_id']}>")
        e.add_field(name="When", value=ts_fmt(row["created_ts"]))
        e.add_field(name="Reason", value=ui.clip(row["reason"] or "—", 1000), inline=False)
        return e

    @commands.Cog.listener()
    async def on_mod_action(self, guild, action, moderator, target, reason, notify=True):
        number = self.new_case(guild.id, action, getattr(target, "id", None), getattr(moderator, "id", None), reason)
        row = self.db.fetchone("SELECT * FROM mod_cases WHERE guild_id=? AND case_no=?", (guild.id, number))
        self.bot.dispatch("bot_log", guild, "moderation", self.case_embed(guild, row))
        s = self.bot.registry.get(guild.id, "moderation")
        if notify and s.get("dm_on_action", True) and action in ("warn", "timeout", "kick", "ban", "softban") and isinstance(target, (discord.Member, discord.User)):
            text = f"**{guild.name}** — {action} (case #{number}).\nReason: {ui.clip(reason or 'No reason provided', 500)}"
            if s.get("appeal_info"):
                text += f"\n\n**Appeals:** {s['appeal_info']}"
            await ui.safe_dm(target, text)

    # ---------------------------------------------------------------- commands
    @commands.command(name="case")
    @commands.guild_only()
    @checks.mod_only()
    async def case(self, ctx, number: int):
        """Show one moderation case."""
        row = self.db.fetchone("SELECT * FROM mod_cases WHERE guild_id=? AND case_no=?", (ctx.guild.id, number))
        if not row:
            return await ui.reply(ctx, embed=ui.err("No case with that number."))
        await ui.reply(ctx, embed=self.case_embed(ctx.guild, row))

    @commands.command(name="reason")
    @commands.guild_only()
    @checks.mod_only()
    async def reason(self, ctx, number: int, *, text: str):
        """Update the reason on a case."""
        cur = self.db.execute("UPDATE mod_cases SET reason=? WHERE guild_id=? AND case_no=?", (text[:1000], ctx.guild.id, number))
        await ui.reply(ctx, embed=ui.ok(f"Updated case #{number}.") if cur.rowcount else ui.err("No case with that number."))

    @commands.command(name="cases")
    @commands.guild_only()
    @checks.mod_only()
    async def cases(self, ctx, member: discord.User = None):
        """List recent cases (optionally for one member)."""
        if member:
            rows = self.db.fetchall("SELECT * FROM mod_cases WHERE guild_id=? AND target_id=? ORDER BY case_no DESC LIMIT 100", (ctx.guild.id, member.id))
        else:
            rows = self.db.fetchall("SELECT * FROM mod_cases WHERE guild_id=? ORDER BY case_no DESC LIMIT 100", (ctx.guild.id,))
        lines = [f"`#{r['case_no']}` {ACTION_ICON.get(r['action'], '•')} **{r['action']}** <@{r['target_id']}> · {ts_fmt(r['created_ts'])} · {ui.clip(r['reason'], 60)}" for r in rows]
        await ui.paginate(ctx, ui.pages_from_lines(f"📁 Cases{' for ' + str(member) if member else ''}", lines, per_page=12))

    @commands.command(name="note")
    @commands.guild_only()
    @checks.mod_only()
    async def note(self, ctx, member: discord.User, *, text: str):
        """Add a private moderator note (the member is not told)."""
        self.db.insert("INSERT INTO mod_notes(guild_id, target_id, moderator_id, note, created_ts) VALUES (?,?,?,?,?)",
                       (ctx.guild.id, member.id, ctx.author.id, text[:1000], time.time()))
        await ui.reply(ctx, embed=ui.ok(f"Note added for {member.mention}."))

    @commands.command(name="notes")
    @commands.guild_only()
    @checks.mod_only()
    async def notes(self, ctx, member: discord.User):
        rows = self.db.fetchall("SELECT * FROM mod_notes WHERE guild_id=? AND target_id=? ORDER BY id DESC LIMIT 50", (ctx.guild.id, member.id))
        lines = [f"`{r['id']}` {ts_fmt(r['created_ts'])} by <@{r['moderator_id']}>: {ui.clip(r['note'], 200)}" for r in rows]
        await ui.paginate(ctx, ui.pages_from_lines(f"📝 Notes for {member}", lines, per_page=8))

    @commands.command(name="delnote")
    @commands.guild_only()
    @checks.mod_only()
    async def delnote(self, ctx, note_id: int):
        cur = self.db.execute("DELETE FROM mod_notes WHERE guild_id=? AND id=?", (ctx.guild.id, note_id))
        await ui.reply(ctx, embed=ui.ok("Note deleted.") if cur.rowcount else ui.err("No such note."))

    @commands.command(name="modhistory", aliases=["history", "offender"])
    @commands.guild_only()
    @checks.mod_only()
    async def modhistory(self, ctx, member: discord.User):
        """Repeat-offender summary: warnings, cases, notes and review items."""
        warnings = self.bot.atlas.store.get("warnings", {}).get(self.bot.atlas.key(ctx.guild.id, member.id), [])
        counts = {r["action"]: r["c"] for r in self.db.fetchall("SELECT action, count(*) c FROM mod_cases WHERE guild_id=? AND target_id=? GROUP BY action", (ctx.guild.id, member.id))}
        notes = self.db.scalar("SELECT count(*) FROM mod_notes WHERE guild_id=? AND target_id=?", (ctx.guild.id, member.id), 0)
        reviews = self.db.scalar("SELECT count(*) FROM review_queue WHERE guild_id=? AND target_id=?", (ctx.guild.id, member.id), 0)
        recent = self.db.fetchall("SELECT * FROM mod_cases WHERE guild_id=? AND target_id=? ORDER BY case_no DESC LIMIT 5", (ctx.guild.id, member.id))
        total = sum(counts.values())
        colour = ui.GOOD if total == 0 and not warnings else (ui.WARN if total < 3 else ui.BAD)
        e = discord.Embed(title=f"🗂️ Moderation history · {member}", colour=colour)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Active warnings", value=str(len(warnings)))
        e.add_field(name="Cases", value=", ".join(f"{k}: {v}" for k, v in counts.items()) or "none")
        e.add_field(name="Notes / review items", value=f"{notes} / {reviews}")
        if recent:
            e.add_field(name="Most recent", value="\n".join(f"`#{r['case_no']}` {r['action']} · {ts_fmt(r['created_ts'])} · {ui.clip(r['reason'], 50)}" for r in recent), inline=False)
        if total >= 3:
            e.set_footer(text="Repeat offender: 3 or more cases on record.")
        await ui.reply(ctx, embed=e)

    @commands.command(name="softban")
    @commands.guild_only()
    @commands.bot_has_guild_permissions(ban_members=True)
    async def softban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Ban + immediate unban to clear a member's last day of messages (same owner restriction as !ban)."""
        if ctx.author.id != settings.MODERATION_OWNER_ID:
            return await ui.reply(ctx, embed=ui.err("Only the configured moderation owner can use softban (same rule as `!ban`)."))
        problem = checks.hierarchy_problem(ctx.author, member, ctx.guild.me)
        if problem:
            return await ui.reply(ctx, embed=ui.err(problem))
        if not await ui.confirm(ctx, f"Softban {member.mention}? They are removed and their last 24h of messages deleted; they can rejoin with an invite.", confirm_label="Softban"):
            return
        await ui.safe_dm(member, f"You were removed from **{ctx.guild.name}** (softban; you can rejoin). Reason: {reason[:500]}")
        await member.ban(reason=f"Softban by {ctx.author}: {reason}", delete_message_seconds=86400)
        await ctx.guild.unban(member, reason="Softban (immediate unban)")
        self.bot.dispatch("mod_action", ctx.guild, "softban", ctx.author, member, reason, False)
        await ui.reply(ctx, embed=ui.ok(f"Softbanned **{member}**."))

    @commands.command(name="reviewqueue", aliases=["rq"])
    @commands.guild_only()
    @checks.mod_only()
    async def reviewqueue(self, ctx, status: str = "open"):
        """Items waiting for a human decision (automod flags, reports, verification)."""
        rows = self.db.fetchall("SELECT * FROM review_queue WHERE guild_id=? AND status=? ORDER BY id DESC LIMIT 100", (ctx.guild.id, status))
        lines = [f"`#{r['id']}` **{r['kind']}** {('<@' + str(r['target_id']) + '>') if r['target_id'] else ''} · {ts_fmt(r['created_ts'])}"
                 f"{(' · [jump](' + r['link'] + ')') if r['link'] else ''}\n> {ui.clip(r['summary'], 120)}" for r in rows]
        await ui.paginate(ctx, ui.pages_from_lines(f"🔎 Review queue ({status})", lines, per_page=6, footer="Use the buttons on each item in the review channel, or !rqclose <id>"))

    @commands.command(name="rqclose")
    @commands.guild_only()
    @checks.mod_only()
    async def rqclose(self, ctx, item_id: int, status: str = "resolved"):
        status = "dismissed" if status.startswith("dis") else "resolved"
        cur = self.db.execute("UPDATE review_queue SET status=?, resolved_by=?, resolved_ts=? WHERE guild_id=? AND id=? AND status='open'",
                              (status, ctx.author.id, time.time(), ctx.guild.id, item_id))
        await ui.reply(ctx, embed=ui.ok(f"Item #{item_id} {status}.") if cur.rowcount else ui.err("No open item with that ID."))


async def setup(bot):
    await bot.add_cog(Moderation(bot))
