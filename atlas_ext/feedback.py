"""Suggestions (named or anonymous, voting, staff status) and private member reports.

Reports go to the staff review queue only; nothing is done to the reported member automatically.
Anonymous suggestions never show the author publicly.
"""
import difflib
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from common import checks, ui
from common.config import Module, Option

from .moderation import add_review_item

STATUS_STYLE = {"open": ("🗳️ Open for votes", ui.INFO), "considering": ("🤔 Under consideration", ui.WARN), "approved": ("✅ Approved", ui.GOOD),
                "rejected": ("❌ Rejected", ui.BAD), "implemented": ("🚀 Implemented", ui.PINK), "duplicate": ("♻️ Duplicate", ui.WARN)}


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.casefold())).strip()


def similar(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


class VoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sug:(?P<id>\d+):(?P<vote>up|down)"):
    def __init__(self, sid, vote, count=0):
        super().__init__(discord.ui.Button(label=str(count), emoji="👍" if vote == "up" else "👎",
                                           style=discord.ButtonStyle.success if vote == "up" else discord.ButtonStyle.danger, custom_id=f"sug:{sid}:{vote}"))
        self.sid, self.vote = int(sid), vote

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"], match["vote"])

    async def callback(self, interaction):
        cog = interaction.client.get_cog("Feedback")
        row = cog.db.fetchone("SELECT * FROM suggestions WHERE id=? AND guild_id=?", (self.sid, interaction.guild_id))
        if row is None:
            return await interaction.response.send_message("This suggestion no longer exists.", ephemeral=True)
        if row["status"] not in ("open", "considering"):
            return await interaction.response.send_message("Voting has closed on this suggestion.", ephemeral=True)
        value = 1 if self.vote == "up" else -1
        prev = cog.db.scalar("SELECT vote FROM suggestion_votes WHERE suggestion_id=? AND user_id=?", (self.sid, interaction.user.id))
        if prev == value:
            cog.db.execute("DELETE FROM suggestion_votes WHERE suggestion_id=? AND user_id=?", (self.sid, interaction.user.id))
            note = "Vote removed."
        else:
            cog.db.execute("INSERT INTO suggestion_votes(suggestion_id, user_id, vote) VALUES (?,?,?) ON CONFLICT(suggestion_id, user_id) DO UPDATE SET vote=excluded.vote",
                           (self.sid, interaction.user.id, value))
            note = "Vote changed." if prev else "Vote counted."
        row = cog.db.fetchone("SELECT * FROM suggestions WHERE id=?", (self.sid,))
        await interaction.response.edit_message(embed=cog.embed(interaction.guild, row), view=cog.view(row))
        await interaction.followup.send(note, ephemeral=True)


class DuplicateCheck(discord.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=90)
        self.author_id, self.value = author_id, None

    async def interaction_check(self, interaction):
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Submit anyway", style=discord.ButtonStyle.primary)
    async def go(self, interaction, button):
        self.value = True
        await interaction.response.edit_message(content="Submitting…", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        self.value = False
        await interaction.response.edit_message(content="Cancelled. Consider voting on the existing suggestion instead!", view=None)
        self.stop()


class ReportModal(discord.ui.Modal, title="Report to staff"):
    reason = discord.ui.TextInput(label="What's wrong?", style=discord.TextStyle.paragraph, max_length=1000)

    def __init__(self, cog, target, link=None, excerpt=None):
        super().__init__()
        self.cog, self.target, self.link, self.excerpt = cog, target, link, excerpt

    async def on_submit(self, interaction):
        await self.cog.file_report(interaction, self.target, str(self.reason), self.link, self.excerpt)


class Feedback(commands.Cog, name="Feedback"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("feedback_001", """
            CREATE TABLE IF NOT EXISTS suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, number INTEGER NOT NULL,
                author_id INTEGER NOT NULL, anonymous INTEGER DEFAULT 0, category TEXT, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                response TEXT, responder_id INTEGER, channel_id INTEGER, message_id INTEGER, created_ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_sug_guild ON suggestions(guild_id, status);
            CREATE TABLE IF NOT EXISTS suggestion_votes (suggestion_id INTEGER NOT NULL, user_id INTEGER NOT NULL, vote INTEGER NOT NULL,
                PRIMARY KEY (suggestion_id, user_id));
            CREATE TABLE IF NOT EXISTS member_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL,
                target_id INTEGER, link TEXT, reason TEXT, status TEXT NOT NULL DEFAULT 'open', review_item_id INTEGER, created_ts REAL NOT NULL)""")
        bot.registry.register(Module("feedback", "Suggestions & reports", "💡", "Community suggestions with voting, plus private reports to staff.", [
            Option("suggestion_channel", "Suggestions channel", "channel", None),
            Option("allow_anonymous", "Allow anonymous suggestions", "bool", True),
            Option("categories", "Suggestion categories", "text", "general, events, channels, bots, feature", "Comma-separated", max_length=300),
            Option("duplicate_threshold", "Duplicate similarity (%)", "int", 75, "Warn when a new idea is this similar to an open one", 50, 100),
            Option("reports", "Private reports", "bool", True, "Enables /modreport and the 'Report message' menu"),
            Option("bug_channel", "Bug-report channel", "channel", None, "Defaults to the review queue"),
        ], default_enabled=True))
        bot.add_dynamic_items(VoteButton)
        self.report_menu = app_commands.ContextMenu(name="Report message", callback=self.report_message_menu)
        self.report_menu.guild_only = True

    async def cog_load(self):
        self.bot.tree.add_command(self.report_menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.report_menu.name, type=self.report_menu.type)

    def settings(self, guild_id):
        return self.bot.registry.get(guild_id, "feedback")

    def votes(self, sid):
        up = self.db.scalar("SELECT count(*) FROM suggestion_votes WHERE suggestion_id=? AND vote=1", (sid,), 0)
        down = self.db.scalar("SELECT count(*) FROM suggestion_votes WHERE suggestion_id=? AND vote=-1", (sid,), 0)
        return up, down

    def embed(self, guild, row):
        label, colour = STATUS_STYLE.get(row["status"], STATUS_STYLE["open"])
        up, down = self.votes(row["id"])
        e = discord.Embed(title=f"💡 Suggestion #{row['number']}" + (f" · {row['category']}" if row["category"] else ""), description=ui.clip(row["content"], 3000), colour=colour)
        if row["anonymous"]:
            e.set_author(name="Anonymous member")
        else:
            member = guild.get_member(row["author_id"])
            e.set_author(name=member.display_name if member else f"User {row['author_id']}", icon_url=member.display_avatar.url if member else None)
        e.add_field(name="Status", value=label)
        e.add_field(name="Votes", value=f"👍 {up} · 👎 {down} · score {up - down:+d}")
        if row["response"]:
            e.add_field(name="Staff response", value=ui.clip(row["response"], 1000) + (f"\n— <@{row['responder_id']}>" if row["responder_id"] else ""), inline=False)
        e.set_footer(text="Press 👍/👎 to vote · press again to remove your vote")
        return e

    def view(self, row):
        if row["status"] not in ("open", "considering"):
            return None
        up, down = self.votes(row["id"])
        v = discord.ui.View(timeout=None)
        v.add_item(VoteButton(row["id"], "up", up))
        v.add_item(VoteButton(row["id"], "down", down))
        return v

    async def submit(self, target, guild, author, content, anonymous=False, category=None):
        s = self.settings(guild.id)
        if not s.get("enabled"):
            return await ui.reply(target, embed=ui.err("Suggestions are switched off here."))
        channel = guild.get_channel(int(s["suggestion_channel"])) if s.get("suggestion_channel") else None
        if not isinstance(channel, discord.TextChannel):
            return await ui.reply(target, embed=ui.err("No suggestions channel is set up yet. An admin can set one with `!config feedback`."))
        if anonymous and not s.get("allow_anonymous", True):
            return await ui.reply(target, embed=ui.err("Anonymous suggestions are turned off here."))
        content = content.strip()
        if len(content) < 10:
            return await ui.reply(target, embed=ui.err("Please describe your idea in a bit more detail (10+ characters)."))
        threshold = int(s.get("duplicate_threshold") or 75) / 100
        best, best_score = None, 0
        for row in self.db.fetchall("SELECT id, number, content, channel_id, message_id FROM suggestions WHERE guild_id=? AND status IN ('open','considering') ORDER BY id DESC LIMIT 300", (guild.id,)):
            score = similar(content, row["content"])
            if score > best_score:
                best, best_score = row, score
        if best is not None and best_score >= threshold:
            view = DuplicateCheck(author.id)
            link = ui.jump(guild.id, best["channel_id"], best["message_id"]) if best["message_id"] else ""
            await ui.reply(target, f"This looks {round(best_score * 100)}% similar to suggestion **#{best['number']}** {link}. Submit anyway?", view=view)
            await view.wait()
            if not view.value:
                return
        cats = [c.strip() for c in str(s.get("categories") or "").split(",") if c.strip()]
        if category and cats and category.casefold() not in [c.casefold() for c in cats]:
            category = None
        with self.db.transaction() as conn:
            number = (conn.execute("SELECT max(number) FROM suggestions WHERE guild_id=?", (guild.id,)).fetchone()[0] or 0) + 1
            sid = conn.execute("INSERT INTO suggestions(guild_id, number, author_id, anonymous, category, content, created_ts) VALUES (?,?,?,?,?,?,?)",
                               (guild.id, number, author.id, int(anonymous), category, content[:3000], time.time())).lastrowid
        row = self.db.fetchone("SELECT * FROM suggestions WHERE id=?", (sid,))
        msg = await channel.send(embed=self.embed(guild, row), view=self.view(row))
        self.db.execute("UPDATE suggestions SET channel_id=?, message_id=? WHERE id=?", (channel.id, msg.id, sid))
        try:
            await msg.create_thread(name=f"Suggestion #{number} discussion", auto_archive_duration=10080)
        except discord.HTTPException:
            pass
        await ui.reply(target, embed=ui.ok(f"Suggestion **#{number}** posted in {channel.mention}" + (" anonymously." if anonymous else ".")))

    @commands.command(name="suggest")
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def suggest(self, ctx, *, text: str):
        """Post a suggestion. `!suggest anon <idea>` hides your name. Optional `[category]` at the start."""
        anonymous = False
        if text.casefold().startswith(("anon ", "anonymous ")):
            anonymous, text = True, text.split(" ", 1)[1]
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        category = None
        m = re.match(r"^\[([^\]]{1,40})\]\s*(.+)$", text, re.S)
        if m:
            category, text = m.group(1).strip(), m.group(2)
        if anonymous:
            await self.submit(_DMTarget(ctx.author), ctx.guild, ctx.author, text, True, category)
        else:
            await self.submit(ctx, ctx.guild, ctx.author, text, False, category)

    @app_commands.command(name="suggest", description="Post a suggestion for the server")
    @app_commands.guild_only()
    @app_commands.describe(idea="Your idea", anonymous="Hide your name publicly", category="Optional category")
    async def suggest_slash(self, interaction: discord.Interaction, idea: str, anonymous: bool = False, category: str = None):
        await self.submit(interaction, interaction.guild, interaction.user, idea, anonymous, category)

    @commands.command(name="featurerequest", aliases=["fr"])
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def featurerequest(self, ctx, *, idea: str):
        """Request a feature (posted as a suggestion in the 'feature' category)."""
        await self.submit(ctx, ctx.guild, ctx.author, idea, False, "feature")

    @commands.command(name="bugreport", aliases=["reportproblem"])
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def bugreport(self, ctx, *, problem: str):
        """Report a problem with the server or bots to staff."""
        s = self.settings(ctx.guild.id)
        channel = ctx.guild.get_channel(int(s["bug_channel"])) if s.get("bug_channel") else None
        if isinstance(channel, discord.TextChannel):
            await ui.safe_send(channel, embed=discord.Embed(title="🐞 Problem report", description=ui.clip(problem, 3000), colour=ui.WARN).set_footer(text=f"From {ctx.author} ({ctx.author.id})"))
        else:
            await add_review_item(self.bot, ctx.guild, "problem report", problem, reporter_id=ctx.author.id, link=ctx.message.jump_url)
        await ui.reply(ctx, embed=ui.ok("Thanks! Staff have your report."))

    async def set_status(self, ctx, number, status, response):
        if not checks.is_staff_for(ctx.author, self.bot.registry.get(ctx.guild.id, "moderation").get("staff_roles")):
            return await ui.reply(ctx, embed=ui.err("Only staff can change suggestion status."))
        row = self.db.fetchone("SELECT * FROM suggestions WHERE guild_id=? AND number=?", (ctx.guild.id, number))
        if not row:
            return await ui.reply(ctx, embed=ui.err("No suggestion with that number."))
        self.db.execute("UPDATE suggestions SET status=?, response=?, responder_id=? WHERE id=?", (status, (response or row["response"] or "")[:1000], ctx.author.id, row["id"]))
        row = self.db.fetchone("SELECT * FROM suggestions WHERE id=?", (row["id"],))
        channel = ctx.guild.get_channel(row["channel_id"]) if row["channel_id"] else None
        if channel:
            try:
                msg = await channel.fetch_message(row["message_id"])
                await msg.edit(embed=self.embed(ctx.guild, row), view=self.view(row))
            except discord.HTTPException:
                pass
        author = ctx.guild.get_member(row["author_id"])
        if author:
            await ui.safe_dm(author, f"Your suggestion **#{number}** in **{ctx.guild.name}** is now **{STATUS_STYLE[status][0]}**." + (f"\nStaff response: {response}" if response else ""))
        await ui.reply(ctx, embed=ui.ok(f"Suggestion #{number} → {STATUS_STYLE[status][0]}"))

    @commands.group(name="suggestion", invoke_without_command=True)
    @commands.guild_only()
    async def suggestion(self, ctx, number: int = None):
        """Show a suggestion. Staff: approve/reject/implement/consider/duplicate <number> [response]."""
        if number is None:
            return await ui.reply(ctx, embed=ui.embed(description="`!suggestion <number>` · staff: `!suggestion approve|reject|implement|consider|duplicate <number> [response]`"))
        row = self.db.fetchone("SELECT * FROM suggestions WHERE guild_id=? AND number=?", (ctx.guild.id, number))
        await ui.reply(ctx, embed=self.embed(ctx.guild, row) if row else ui.err("No suggestion with that number."))

    @suggestion.command(name="approve")
    async def s_approve(self, ctx, number: int, *, response: str = ""):
        await self.set_status(ctx, number, "approved", response)

    @suggestion.command(name="reject")
    async def s_reject(self, ctx, number: int, *, response: str = ""):
        await self.set_status(ctx, number, "rejected", response)

    @suggestion.command(name="implement")
    async def s_implement(self, ctx, number: int, *, response: str = ""):
        await self.set_status(ctx, number, "implemented", response)

    @suggestion.command(name="consider")
    async def s_consider(self, ctx, number: int, *, response: str = ""):
        await self.set_status(ctx, number, "considering", response)

    @suggestion.command(name="duplicate")
    async def s_duplicate(self, ctx, number: int, *, response: str = ""):
        await self.set_status(ctx, number, "duplicate", response)

    @commands.command(name="suggestions")
    @commands.guild_only()
    async def suggestions(self, ctx, status: str = "open"):
        """Browse suggestions by status (open, approved, rejected, implemented, considering, all)."""
        if status == "all":
            rows = self.db.fetchall("SELECT * FROM suggestions WHERE guild_id=? ORDER BY id DESC LIMIT 200", (ctx.guild.id,))
        else:
            rows = self.db.fetchall("SELECT * FROM suggestions WHERE guild_id=? AND status=? ORDER BY id DESC LIMIT 200", (ctx.guild.id, status))
        lines = []
        for r in rows:
            up, down = self.votes(r["id"])
            link = f" [↗]({ui.jump(ctx.guild.id, r['channel_id'], r['message_id'])})" if r["message_id"] else ""
            lines.append(f"`#{r['number']}` {STATUS_STYLE.get(r['status'], ('?',))[0].split()[0]} {ui.clip(r['content'], 70)} · {up - down:+d}{link}")
        await ui.paginate(ctx, ui.pages_from_lines(f"💡 Suggestions ({status})", lines, per_page=10))

    @commands.command(name="suggestionstats")
    @commands.guild_only()
    async def suggestionstats(self, ctx):
        rows = self.db.fetchall("SELECT status, count(*) c FROM suggestions WHERE guild_id=? GROUP BY status", (ctx.guild.id,))
        top = self.db.fetchall("SELECT s.number, s.content, sum(v.vote) score FROM suggestions s JOIN suggestion_votes v ON v.suggestion_id=s.id "
                               "WHERE s.guild_id=? AND s.status IN ('open','considering') GROUP BY s.id ORDER BY score DESC LIMIT 5", (ctx.guild.id,))
        e = discord.Embed(title="💡 Suggestion stats", colour=ui.INFO, description="\n".join(f"{STATUS_STYLE.get(r['status'], (r['status'],))[0]}: **{r['c']}**" for r in rows) or "No suggestions yet.")
        if top:
            e.add_field(name="Most supported (open)", value="\n".join(f"`#{r['number']}` {r['score']:+d} · {ui.clip(r['content'], 60)}" for r in top), inline=False)
        await ui.reply(ctx, embed=e)

    # ---------------------------------------------------------------- reports
    async def file_report(self, interaction, target, reason, link=None, excerpt=None):
        guild = interaction.guild
        if not self.settings(guild.id).get("reports", True):
            return await interaction.response.send_message("Private reports are switched off here. Please contact a staff member.", ephemeral=True)
        rid = self.db.insert("INSERT INTO member_reports(guild_id, reporter_id, target_id, link, reason, created_ts) VALUES (?,?,?,?,?,?)",
                             (guild.id, interaction.user.id, getattr(target, "id", None), link, reason[:1000], time.time()))
        extra = f"Message excerpt: {ui.clip(excerpt, 300)}" if excerpt else ""
        item = await add_review_item(self.bot, guild, "report", f"Report #{rid}: {reason}", target_id=getattr(target, "id", None),
                                     reporter_id=interaction.user.id, link=link, extra=extra)
        self.db.execute("UPDATE member_reports SET review_item_id=? WHERE id=?", (item, rid))
        await interaction.response.send_message(f"Thank you. Your report (#{rid}) was sent privately to staff. You'll get a DM when it has been reviewed.", ephemeral=True)

    @app_commands.command(name="modreport", description="Privately report a member or problem to staff")
    @app_commands.guild_only()
    @app_commands.describe(member="Who you're reporting (optional)", reason="What happened")
    async def modreport(self, interaction: discord.Interaction, reason: str, member: discord.Member = None):
        await self.file_report(interaction, member, reason)

    async def report_message_menu(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.send_modal(ReportModal(self, message.author, message.jump_url, message.content))

    @commands.command(name="myreports")
    @commands.guild_only()
    async def myreports(self, ctx):
        """Status of reports you've filed here."""
        rows = self.db.fetchall("SELECT id, status, created_ts FROM member_reports WHERE guild_id=? AND reporter_id=? ORDER BY id DESC LIMIT 20", (ctx.guild.id, ctx.author.id))
        text = "\n".join(f"`#{r['id']}` · {r['status']} · <t:{int(r['created_ts'])}:R>" for r in rows) or "You haven't filed any reports here."
        await ui.safe_dm(ctx.author, embed=ui.embed("Your reports", text))
        await ui.reply(ctx, embed=ui.ok("Sent to your DMs."))

    @commands.Cog.listener()
    async def on_review_item_closed(self, guild, item, status, moderator):
        if item.get("kind") != "report":
            return
        row = self.db.fetchone("SELECT * FROM member_reports WHERE review_item_id=?", (item["id"],))
        if not row:
            return
        self.db.execute("UPDATE member_reports SET status=? WHERE id=?", (status, row["id"]))
        reporter = guild.get_member(row["reporter_id"])
        if reporter:
            await ui.safe_dm(reporter, f"Your report #{row['id']} in **{guild.name}** has been reviewed by staff. Thank you for helping keep the community safe.")


class _DMTarget:
    """Lets submit() answer an anonymous prefix suggestion privately by DM."""

    def __init__(self, user):
        self.user = user
        self.author = user
        self.interaction = None

    async def reply(self, content=None, **kwargs):
        kwargs.pop("mention_author", None)
        kwargs.pop("allowed_mentions", None)
        try:
            return await self.user.send(content, **kwargs)
        except discord.HTTPException:
            return None

    send = reply


async def setup(bot):
    await bot.add_cog(Feedback(bot))
