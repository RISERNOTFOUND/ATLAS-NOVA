"""Discord-native support tickets: private channels, claiming, priority, transcripts, feedback.

Privacy: ticket channels are visible only to the opener, configured staff roles and ATLAS.
Transcripts go ONLY to the staff log channel (keep it private) and to the opener by DM.
"""
import io
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option

PRIORITIES = ("low", "normal", "high", "urgent")
PRIORITY_ICON = {"low": "🟢", "normal": "🔵", "high": "🟠", "urgent": "🔴"}


def staff_ids(bot, guild_id):
    s = bot.registry.get(guild_id, "tickets")
    ids = [int(r) for r in s.get("staff_roles") or []]
    ids += [int(r) for r in bot.registry.get(guild_id, "moderation").get("staff_roles") or []]
    return ids


def is_ticket_staff(bot, member):
    return checks.is_staff_for(member, staff_ids(bot, member.guild.id))


def get_ticket(bot, guild_id, channel_id=None, ticket_id=None):
    if ticket_id is not None:
        row = bot.db.fetchone("SELECT * FROM tickets WHERE guild_id=? AND id=?", (guild_id, ticket_id))
    else:
        row = bot.db.fetchone("SELECT * FROM tickets WHERE guild_id=? AND channel_id=?", (guild_id, channel_id))
    return dict(row) if row else None


async def build_transcript(channel, ticket):
    lines = [f"Transcript of ticket #{ticket['number']:04d} ({channel.name}) in {channel.guild.name}",
             f"Opened by user {ticket['opener_id']} at {datetime.fromtimestamp(ticket['created_ts'], timezone.utc):%Y-%m-%d %H:%M UTC}",
             f"Category: {ticket['category'] or 'general'} · Priority: {ticket['priority']}", "-" * 60]
    async for msg in channel.history(limit=2000, oldest_first=True):
        stamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
        body = msg.clean_content or ""
        if msg.embeds and not body:
            body = "[embed] " + (msg.embeds[0].title or msg.embeds[0].description or "")[:200]
        files = " ".join(f"[file: {a.filename}]" for a in msg.attachments)
        lines.append(f"[{stamp}] {msg.author} ({msg.author.id}): {body} {files}".rstrip())
    return "\n".join(lines)


class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tkt:(?P<id>\d+):(?P<action>claim|close|reopen|delete|transcript)"):
    LABELS = {"claim": ("Claim", "🙋", discord.ButtonStyle.primary), "close": ("Close", "🔒", discord.ButtonStyle.danger),
              "reopen": ("Reopen", "🔓", discord.ButtonStyle.success), "delete": ("Delete channel", "🗑️", discord.ButtonStyle.secondary),
              "transcript": ("Transcript", "📄", discord.ButtonStyle.secondary)}

    def __init__(self, ticket_id, action):
        label, emoji, style = self.LABELS[action]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"tkt:{ticket_id}:{action}"))
        self.ticket_id, self.action = int(ticket_id), action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"], match["action"])

    async def callback(self, interaction):
        cog = interaction.client.get_cog("Tickets")
        await cog.handle_button(interaction, self.ticket_id, self.action)


class OpenTicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tkt:open"):
    def __init__(self):
        super().__init__(discord.ui.Button(label="Open a ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="tkt:open"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction):
        await interaction.client.get_cog("Tickets").start_open(interaction)


class FeedbackButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tktfb:(?P<gid>\d+):(?P<id>\d+):(?P<score>[1-5])"):
    def __init__(self, guild_id, ticket_id, score):
        super().__init__(discord.ui.Button(label="⭐" * int(score), style=discord.ButtonStyle.secondary, custom_id=f"tktfb:{guild_id}:{ticket_id}:{score}"))
        self.guild_id, self.ticket_id, self.score = int(guild_id), int(ticket_id), int(score)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["gid"], match["id"], match["score"])

    async def callback(self, interaction):
        db = interaction.client.db
        row = db.fetchone("SELECT opener_id, rating FROM tickets WHERE guild_id=? AND id=?", (self.guild_id, self.ticket_id))
        if row is None or row["opener_id"] != interaction.user.id:
            return await interaction.response.send_message("This feedback form isn't yours.", ephemeral=True)
        db.execute("UPDATE tickets SET rating=? WHERE guild_id=? AND id=?", (self.score, self.guild_id, self.ticket_id))
        await interaction.response.edit_message(content=f"Thanks for the feedback! You rated your support {'⭐' * self.score}.", view=None)


class SubjectModal(discord.ui.Modal, title="Open a ticket"):
    subject = discord.ui.TextInput(label="What do you need help with?", style=discord.TextStyle.paragraph, max_length=800)

    def __init__(self, cog, category):
        super().__init__()
        self.cog, self.category = cog, category

    async def on_submit(self, interaction):
        await self.cog.create_ticket(interaction, self.category, str(self.subject))


class CategoryPicker(discord.ui.View):
    def __init__(self, cog, categories):
        super().__init__(timeout=120)
        self.cog = cog
        select = discord.ui.Select(placeholder="Choose a topic…", options=[
            discord.SelectOption(label=c["name"][:100], value=c["name"][:100], emoji=c["emoji"] or None, description=ui.clip(c["description"], 100) or None)
            for c in categories[:25]])
        select.callback = self.chosen
        self.select = select
        self.add_item(select)

    async def chosen(self, interaction):
        await interaction.response.send_modal(SubjectModal(self.cog, self.select.values[0]))


class Tickets(commands.Cog, name="Tickets"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.creating = set()
        bot.db.migrate("tickets_001", """
            CREATE TABLE IF NOT EXISTS ticket_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL,
                emoji TEXT, description TEXT);
            CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, number INTEGER NOT NULL,
                channel_id INTEGER, opener_id INTEGER NOT NULL, category TEXT, subject TEXT, status TEXT NOT NULL, claimed_by INTEGER,
                priority TEXT DEFAULT 'normal', created_ts REAL NOT NULL, closed_ts REAL, last_activity_ts REAL, reminded_ts REAL, rating INTEGER);
            CREATE INDEX IF NOT EXISTS idx_tickets_guild ON tickets(guild_id, status);
            CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets(channel_id)""")
        bot.registry.register(Module("tickets", "Tickets", "🎫", "Private support channels for members and staff.", [
            Option("category", "Ticket category", "category", None, "Discord category new tickets are created in"),
            Option("staff_roles", "Staff roles", "roles", [], "Can see and manage all tickets"),
            Option("log_channel", "Transcript/log channel", "channel", None, "Staff-only! Transcripts are posted here"),
            Option("max_open", "Max open tickets per member", "int", 2, "", 1, 10),
            Option("ping_staff", "Ping staff on new tickets", "bool", True),
            Option("inactivity_hours", "Inactivity reminder (hours)", "int", 24, "0 = off", 0, 720),
            Option("dm_transcript", "DM transcript to opener", "bool", True),
            Option("ask_feedback", "Ask for feedback on close", "bool", True),
        ]))
        bot.add_dynamic_items(TicketButton, OpenTicketButton, FeedbackButton)

    async def cog_load(self):
        self.inactivity_loop.start()

    async def cog_unload(self):
        self.inactivity_loop.cancel()

    def settings(self, guild_id):
        return self.bot.registry.get(guild_id, "tickets")

    def controls(self, ticket):
        view = discord.ui.View(timeout=None)
        if ticket["status"] == "open":
            view.add_item(TicketButton(ticket["id"], "claim"))
            view.add_item(TicketButton(ticket["id"], "close"))
        else:
            view.add_item(TicketButton(ticket["id"], "reopen"))
            view.add_item(TicketButton(ticket["id"], "transcript"))
            view.add_item(TicketButton(ticket["id"], "delete"))
        return view

    def ticket_embed(self, guild, ticket):
        e = discord.Embed(title=f"🎫 Ticket #{ticket['number']:04d} · {ticket['category'] or 'Support'}", colour=ui.INFO if ticket["status"] == "open" else ui.WARN,
                          description=ui.clip(ticket["subject"] or "", 2000))
        e.add_field(name="Opened by", value=f"<@{ticket['opener_id']}>")
        e.add_field(name="Status", value=ticket["status"])
        e.add_field(name="Priority", value=f"{PRIORITY_ICON.get(ticket['priority'], '')} {ticket['priority']}")
        e.add_field(name="Claimed by", value=f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else "nobody yet")
        e.set_footer(text="Only you, staff and ATLAS can see this channel.")
        return e

    # ---------------------------------------------------------------- opening
    async def start_open(self, interaction):
        s = self.settings(interaction.guild_id)
        if not s.get("enabled"):
            return await interaction.response.send_message("Tickets are switched off right now.", ephemeral=True)
        open_count = self.db.scalar("SELECT count(*) FROM tickets WHERE guild_id=? AND opener_id=? AND status='open'", (interaction.guild_id, interaction.user.id), 0)
        if open_count >= int(s.get("max_open") or 2):
            return await interaction.response.send_message("You already have the maximum number of open tickets. Please use your existing one.", ephemeral=True)
        cats = [dict(r) for r in self.db.fetchall("SELECT * FROM ticket_categories WHERE guild_id=? ORDER BY id", (interaction.guild_id,))]
        if cats:
            return await interaction.response.send_message("What's your ticket about?", view=CategoryPicker(self, cats), ephemeral=True)
        await interaction.response.send_modal(SubjectModal(self, None))

    async def create_ticket(self, interaction, category, subject):
        guild, user = interaction.guild, interaction.user
        if user.id in self.creating:
            return await interaction.response.send_message("Already creating your ticket…", ephemeral=True)
        self.creating.add(user.id)
        try:
            s = self.settings(guild.id)
            me = guild.me
            if not me.guild_permissions.manage_channels:
                return await interaction.response.send_message("I need **Manage Channels** to open tickets. Please tell staff.", ephemeral=True)
            await interaction.response.defer(ephemeral=True, thinking=True)
            parent = guild.get_channel(int(s["category"])) if s.get("category") else None
            overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
                          user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
                          me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True, embed_links=True, attach_files=True)}
            staff_roles = [guild.get_role(r) for r in staff_ids(self.bot, guild.id)]
            for role in filter(None, staff_roles):
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, manage_messages=True)
            with self.db.transaction() as conn:
                number = (conn.execute("SELECT max(number) FROM tickets WHERE guild_id=?", (guild.id,)).fetchone()[0] or 0) + 1
                cur = conn.execute("INSERT INTO tickets(guild_id, number, opener_id, category, subject, status, created_ts, last_activity_ts) VALUES (?,?,?,?,?,?,?,?)",
                                   (guild.id, number, user.id, category, subject[:800], "open", time.time(), time.time()))
                ticket_id = cur.lastrowid
            name = f"ticket-{number:04d}-{''.join(ch for ch in user.name.lower() if ch.isalnum())[:12] or 'member'}"
            try:
                channel = await guild.create_text_channel(name, category=parent if isinstance(parent, discord.CategoryChannel) else None,
                                                          overwrites=overwrites, topic=f"Ticket #{number:04d} for {user} · {category or 'support'}",
                                                          reason=f"Ticket opened by {user} ({user.id})")
            except discord.HTTPException as exc:
                self.db.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
                return await interaction.followup.send(f"I couldn't create the ticket channel ({type(exc).__name__}). Please tell staff.", ephemeral=True)
            self.db.execute("UPDATE tickets SET channel_id=? WHERE id=?", (channel.id, ticket_id))
            ticket = get_ticket(self.bot, guild.id, ticket_id=ticket_id)
            ping = " ".join(r.mention for r in filter(None, staff_roles)) if s.get("ping_staff") else ""
            await channel.send(content=f"{user.mention} {ping}".strip(), embed=self.ticket_embed(guild, ticket), view=self.controls(ticket),
                               allowed_mentions=discord.AllowedMentions(users=True, roles=True))
            await interaction.followup.send(f"Your ticket is open: {channel.mention}", ephemeral=True)
            self.bot.dispatch("bot_log", guild, "tickets", discord.Embed(description=f"🎫 Ticket #{number:04d} opened by {user.mention} ({category or 'support'}) → {channel.mention}", colour=ui.INFO))
        finally:
            self.creating.discard(user.id)

    # ---------------------------------------------------------------- actions
    async def handle_button(self, interaction, ticket_id, action):
        ticket = get_ticket(self.bot, interaction.guild_id, ticket_id=ticket_id)
        if ticket is None:
            return await interaction.response.send_message("That ticket no longer exists.", ephemeral=True)
        member = interaction.user
        staff = is_ticket_staff(self.bot, member)
        if action == "claim":
            if not staff:
                return await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
            self.db.execute("UPDATE tickets SET claimed_by=? WHERE id=?", (member.id, ticket_id))
            ticket["claimed_by"] = member.id
            await interaction.response.edit_message(embed=self.ticket_embed(interaction.guild, ticket), view=self.controls(ticket))
            await interaction.channel.send(f"🙋 {member.mention} is handling this ticket.", allowed_mentions=discord.AllowedMentions.none())
        elif action == "close":
            if not (staff or member.id == ticket["opener_id"]):
                return await interaction.response.send_message("Only the opener or staff can close this ticket.", ephemeral=True)
            await interaction.response.defer()
            await self.close_ticket(interaction.channel, ticket, member)
        elif action == "reopen":
            if not staff:
                return await interaction.response.send_message("Only staff can reopen tickets.", ephemeral=True)
            await interaction.response.defer()
            await self.reopen_ticket(interaction.channel, ticket, member)
        elif action == "transcript":
            if not staff:
                return await interaction.response.send_message("Only staff can export transcripts.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            text = await build_transcript(interaction.channel, ticket)
            await interaction.followup.send(file=discord.File(io.BytesIO(text.encode()), filename=f"ticket-{ticket['number']:04d}.txt"), ephemeral=True)
        elif action == "delete":
            if not staff:
                return await interaction.response.send_message("Only staff can delete ticket channels.", ephemeral=True)
            if ticket["status"] != "closed":
                return await interaction.response.send_message("Close the ticket first.", ephemeral=True)
            if not await ui.confirm(interaction, "Delete this ticket channel permanently? The transcript stays in the log channel."):
                return
            self.db.execute("UPDATE tickets SET status='archived' WHERE id=?", (ticket_id,))
            try:
                await interaction.channel.delete(reason=f"Ticket #{ticket['number']} deleted by {member}")
            except discord.HTTPException:
                pass

    async def close_ticket(self, channel, ticket, actor):
        if ticket["status"] != "open":
            return
        guild = channel.guild
        s = self.settings(guild.id)
        self.db.execute("UPDATE tickets SET status='closed', closed_ts=? WHERE id=?", (time.time(), ticket["id"]))
        ticket["status"] = "closed"
        opener = guild.get_member(ticket["opener_id"])
        if opener:
            try:
                await channel.set_permissions(opener, view_channel=True, send_messages=False, read_message_history=True, reason="Ticket closed")
            except discord.HTTPException:
                pass
        await channel.send(embed=discord.Embed(description=f"🔒 Ticket closed by {actor.mention}.", colour=ui.WARN), view=self.controls(ticket))
        text = await build_transcript(channel, ticket)
        fname = f"ticket-{ticket['number']:04d}.txt"
        log = guild.get_channel(int(s["log_channel"])) if s.get("log_channel") else None
        if isinstance(log, discord.TextChannel):
            e = discord.Embed(title=f"📄 Ticket #{ticket['number']:04d} closed", colour=ui.WARN,
                              description=f"Opened by <@{ticket['opener_id']}> · closed by {actor.mention}\nCategory: {ticket['category'] or 'support'} · Priority: {ticket['priority']}")
            await ui.safe_send(log, embed=e, file=discord.File(io.BytesIO(text.encode()), filename=fname))
        if opener and s.get("dm_transcript", True):
            await ui.safe_dm(opener, f"Your ticket in **{guild.name}** was closed. Here's a copy of the conversation.", file=discord.File(io.BytesIO(text.encode()), filename=fname))
        if opener and s.get("ask_feedback", True):
            view = discord.ui.View(timeout=None)
            for score in range(1, 6):
                view.add_item(FeedbackButton(guild.id, ticket["id"], score))
            await ui.safe_dm(opener, f"How was your support experience in **{guild.name}**?", view=view)
        self.bot.dispatch("bot_log", guild, "tickets", discord.Embed(description=f"🔒 Ticket #{ticket['number']:04d} closed by {actor.mention}", colour=ui.WARN))

    async def reopen_ticket(self, channel, ticket, actor):
        self.db.execute("UPDATE tickets SET status='open', closed_ts=NULL, last_activity_ts=? WHERE id=?", (time.time(), ticket["id"]))
        ticket["status"] = "open"
        opener = channel.guild.get_member(ticket["opener_id"])
        if opener:
            try:
                await channel.set_permissions(opener, view_channel=True, send_messages=True, read_message_history=True, attach_files=True, reason="Ticket reopened")
            except discord.HTTPException:
                pass
        await channel.send(embed=self.ticket_embed(channel.guild, ticket), view=self.controls(ticket))
        await channel.send(f"🔓 Reopened by {actor.mention}.", allowed_mentions=discord.AllowedMentions.none())

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        self.db.execute("UPDATE tickets SET last_activity_ts=?, reminded_ts=NULL WHERE channel_id=? AND status='open'", (time.time(), message.channel.id))

    @tasks.loop(minutes=15)
    async def inactivity_loop(self):
        rows = self.db.fetchall("SELECT * FROM tickets WHERE status='open' AND channel_id IS NOT NULL")
        now = time.time()
        for t in rows:
            s = self.settings(t["guild_id"])
            hours = int(s.get("inactivity_hours") or 0)
            if not s.get("enabled") or hours <= 0 or now - (t["last_activity_ts"] or t["created_ts"]) < hours * 3600 or t["reminded_ts"]:
                continue
            guild = self.bot.get_guild(t["guild_id"])
            channel = guild.get_channel(t["channel_id"]) if guild else None
            if channel is None:
                self.db.execute("UPDATE tickets SET status='archived' WHERE id=?", (t["id"],))
                continue
            who = f"<@{t['opener_id']}>" + (f" <@{t['claimed_by']}>" if t["claimed_by"] else "")
            await ui.safe_send(channel, f"⏰ {who} this ticket has been quiet for {hours}h. Is it resolved? Press **Close** if so.",
                               allowed_mentions=discord.AllowedMentions(users=True))
            self.db.execute("UPDATE tickets SET reminded_ts=? WHERE id=?", (now, t["id"]))

    @inactivity_loop.before_loop
    async def before_inactivity(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------------- commands
    @commands.command(name="ticketpanel")
    @commands.guild_only()
    @checks.admin_only()
    async def ticketpanel(self, ctx, channel: discord.TextChannel = None):
        """Post the 'Open a ticket' button."""
        channel = channel or ctx.channel
        if not self.settings(ctx.guild.id).get("enabled"):
            self.bot.registry.set(ctx.guild, ctx.author, "tickets", "enabled", True)
        view = discord.ui.View(timeout=None)
        view.add_item(OpenTicketButton())
        e = discord.Embed(title="🎫 Need help?", colour=ui.PINK, description="Press the button to open a private ticket with the staff team. Only you and staff can see it.")
        await channel.send(embed=e, view=view)
        if channel != ctx.channel:
            await ui.reply(ctx, embed=ui.ok(f"Ticket panel posted in {channel.mention}."))

    def current(self, ctx):
        return get_ticket(self.bot, ctx.guild.id, channel_id=ctx.channel.id)

    @commands.group(name="ticket", invoke_without_command=True)
    @commands.guild_only()
    async def ticket(self, ctx):
        """Ticket tools (use inside a ticket channel)."""
        t = self.current(ctx)
        if t is None:
            return await ui.reply(ctx, embed=ui.err("This isn't a ticket channel. Use the ticket panel to open one."))
        await ui.reply(ctx, embed=self.ticket_embed(ctx.guild, t))

    async def _staff_ticket(self, ctx):
        t = self.current(ctx)
        if t is None:
            await ui.reply(ctx, embed=ui.err("Use this inside a ticket channel."))
            return None
        if not is_ticket_staff(self.bot, ctx.author):
            await ui.reply(ctx, embed=ui.err("Only staff can do that."))
            return None
        return t

    @ticket.command(name="claim")
    async def t_claim(self, ctx):
        t = await self._staff_ticket(ctx)
        if t:
            self.db.execute("UPDATE tickets SET claimed_by=? WHERE id=?", (ctx.author.id, t["id"]))
            await ui.reply(ctx, embed=ui.ok(f"{ctx.author.mention} claimed this ticket."), ephemeral=False)

    @ticket.command(name="transfer", aliases=["assign"])
    async def t_transfer(self, ctx, staff_member: discord.Member):
        t = await self._staff_ticket(ctx)
        if t:
            if not is_ticket_staff(self.bot, staff_member):
                return await ui.reply(ctx, embed=ui.err("That member isn't staff."))
            self.db.execute("UPDATE tickets SET claimed_by=? WHERE id=?", (staff_member.id, t["id"]))
            await ctx.send(f"🔁 Ticket assigned to {staff_member.mention}.", allowed_mentions=discord.AllowedMentions(users=True))

    @ticket.command(name="priority")
    async def t_priority(self, ctx, level: str):
        t = await self._staff_ticket(ctx)
        if t:
            level = level.casefold()
            if level not in PRIORITIES:
                return await ui.reply(ctx, embed=ui.err("Use low, normal, high or urgent."))
            self.db.execute("UPDATE tickets SET priority=? WHERE id=?", (level, t["id"]))
            await ui.reply(ctx, embed=ui.ok(f"Priority set to {PRIORITY_ICON[level]} **{level}**."), ephemeral=False)

    @ticket.command(name="close")
    async def t_close(self, ctx):
        t = self.current(ctx)
        if t is None:
            return await ui.reply(ctx, embed=ui.err("Use this inside a ticket channel."))
        if not (is_ticket_staff(self.bot, ctx.author) or ctx.author.id == t["opener_id"]):
            return await ui.reply(ctx, embed=ui.err("Only the opener or staff can close this ticket."))
        await self.close_ticket(ctx.channel, t, ctx.author)

    @ticket.command(name="reopen")
    async def t_reopen(self, ctx):
        t = await self._staff_ticket(ctx)
        if t:
            await self.reopen_ticket(ctx.channel, t, ctx.author)

    @ticket.command(name="add")
    async def t_add(self, ctx, member: discord.Member):
        t = await self._staff_ticket(ctx)
        if t:
            await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True, reason=f"Added to ticket by {ctx.author}")
            await ui.reply(ctx, embed=ui.ok(f"Added {member.mention}."), ephemeral=False)

    @ticket.command(name="remove")
    async def t_remove(self, ctx, member: discord.Member):
        t = await self._staff_ticket(ctx)
        if t:
            if member.id == t["opener_id"]:
                return await ui.reply(ctx, embed=ui.err("You can't remove the ticket opener; close the ticket instead."))
            await ctx.channel.set_permissions(member, overwrite=None, reason=f"Removed from ticket by {ctx.author}")
            await ui.reply(ctx, embed=ui.ok(f"Removed {member.mention}."), ephemeral=False)

    @ticket.command(name="transcript")
    async def t_transcript(self, ctx):
        t = await self._staff_ticket(ctx)
        if t:
            text = await build_transcript(ctx.channel, t)
            try:
                await ctx.author.send(file=discord.File(io.BytesIO(text.encode()), filename=f"ticket-{t['number']:04d}.txt"))
                await ui.reply(ctx, embed=ui.ok("Transcript sent to your DMs."))
            except discord.Forbidden:
                await ui.reply(ctx, embed=ui.err("I couldn't DM you."))

    @ticket.command(name="stats")
    async def t_stats(self, ctx, days: int = 30):
        if not is_ticket_staff(self.bot, ctx.author):
            return await ui.reply(ctx, embed=ui.err("Staff only."))
        since = time.time() - days * 86400
        rows = self.db.fetchall("SELECT * FROM tickets WHERE guild_id=? AND created_ts>=?", (ctx.guild.id, since))
        closed = [r for r in rows if r["closed_ts"]]
        avg = sum(r["closed_ts"] - r["created_ts"] for r in closed) / len(closed) if closed else 0
        ratings = [r["rating"] for r in rows if r["rating"]]
        handlers = {}
        for r in rows:
            if r["claimed_by"]:
                handlers[r["claimed_by"]] = handlers.get(r["claimed_by"], 0) + 1
        e = discord.Embed(title=f"🎫 Ticket stats · last {days} days", colour=ui.INFO)
        e.add_field(name="Opened", value=str(len(rows)))
        e.add_field(name="Closed", value=str(len(closed)))
        e.add_field(name="Open now", value=str(self.db.scalar("SELECT count(*) FROM tickets WHERE guild_id=? AND status='open'", (ctx.guild.id,), 0)))
        e.add_field(name="Avg. time to close", value=f"{avg / 3600:.1f} h" if closed else "—")
        e.add_field(name="Avg. rating", value=f"{sum(ratings) / len(ratings):.1f} ⭐ ({len(ratings)})" if ratings else "—")
        if handlers:
            e.add_field(name="Top handlers", value="\n".join(f"<@{u}> · {n}" for u, n in sorted(handlers.items(), key=lambda kv: -kv[1])[:5]), inline=False)
        await ui.reply(ctx, embed=e)

    @ticket.command(name="archive")
    async def t_archive(self, ctx):
        """List closed tickets (staff)."""
        if not is_ticket_staff(self.bot, ctx.author):
            return await ui.reply(ctx, embed=ui.err("Staff only."))
        rows = self.db.fetchall("SELECT * FROM tickets WHERE guild_id=? AND status IN ('closed','archived') ORDER BY id DESC LIMIT 100", (ctx.guild.id,))
        lines = [f"`#{r['number']:04d}` <@{r['opener_id']}> · {r['category'] or 'support'} · closed <t:{int(r['closed_ts'] or r['created_ts'])}:R>" + (f" · {'⭐' * r['rating']}" if r["rating"] else "") for r in rows]
        await ui.paginate(ctx, ui.pages_from_lines("🗄️ Ticket archive", lines))

    @ticket.group(name="category", invoke_without_command=True)
    async def t_category(self, ctx):
        rows = self.db.fetchall("SELECT * FROM ticket_categories WHERE guild_id=?", (ctx.guild.id,))
        await ui.reply(ctx, embed=ui.embed("Ticket categories", "\n".join(f"{r['emoji'] or '•'} **{r['name']}** — {r['description'] or ''}" for r in rows) or "None (members just describe their issue). Add: `!ticket category add <emoji> <name> | <description>`"))

    @t_category.command(name="add")
    @checks.admin_only()
    async def t_cat_add(self, ctx, emoji: str, *, rest: str):
        name, _, desc = rest.partition("|")
        if self.db.scalar("SELECT count(*) FROM ticket_categories WHERE guild_id=?", (ctx.guild.id,), 0) >= 25:
            return await ui.reply(ctx, embed=ui.err("Up to 25 categories."))
        self.db.insert("INSERT INTO ticket_categories(guild_id, name, emoji, description) VALUES (?,?,?,?)", (ctx.guild.id, name.strip()[:80], emoji[:40], desc.strip()[:100]))
        await ui.reply(ctx, embed=ui.ok(f"Added category **{name.strip()}**."))

    @t_category.command(name="remove")
    @checks.admin_only()
    async def t_cat_remove(self, ctx, *, name: str):
        n = self.db.execute("DELETE FROM ticket_categories WHERE guild_id=? AND lower(name)=lower(?)", (ctx.guild.id, name.strip())).rowcount
        await ui.reply(ctx, embed=ui.ok("Removed.") if n else ui.err("No such category."))


async def setup(bot):
    await bot.add_cog(Tickets(bot))
