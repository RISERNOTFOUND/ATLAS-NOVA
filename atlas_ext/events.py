"""Community events with RSVP buttons, timezone-aware reminders, editing, cancellation and history.
All state is in atlas.db, so RSVPs and reminders survive restarts; each reminder is claimed atomically
before it is sent, so it can't be delivered twice.
"""
import json
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option
from common.diag import user_tz
from common.timeparse import parse_when, tz_name

RSVP = {"going": ("Going", "✅", discord.ButtonStyle.success), "interested": ("Interested", "⭐", discord.ButtonStyle.primary),
        "not": ("Can't go", "❌", discord.ButtonStyle.secondary)}


class RSVPButton(discord.ui.DynamicItem[discord.ui.Button], template=r"evt:(?P<id>\d+):(?P<status>going|interested|not)"):
    def __init__(self, event_id, status, disabled=False):
        label, emoji, style = RSVP[status]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=f"evt:{event_id}:{status}", disabled=disabled))
        self.event_id, self.status = int(event_id), status

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"], match["status"])

    async def callback(self, interaction):
        await interaction.client.get_cog("Events").rsvp(interaction, self.event_id, self.status)


class Events(commands.Cog, name="Events"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("events_001", """
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER, message_id INTEGER,
                creator_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT, start_ts REAL NOT NULL, tz TEXT, status TEXT NOT NULL DEFAULT 'scheduled',
                reminders TEXT NOT NULL DEFAULT '[60,0]', reminded TEXT NOT NULL DEFAULT '[]', created_ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_events_guild ON events(guild_id, status, start_ts);
            CREATE TABLE IF NOT EXISTS event_rsvps (event_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL, ts REAL NOT NULL,
                PRIMARY KEY (event_id, user_id))""")
        bot.registry.register(Module("events", "Events", "📅", "Community events with RSVPs and reminders.", [
            Option("channel", "Default events channel", "channel", None),
            Option("timezone", "Default timezone", "tz", "Asia/Kolkata"),
            Option("reminders", "Reminder offsets (minutes)", "text", "60, 0", "Comma-separated minutes before start", max_length=60),
            Option("dm_reminders", "DM reminders to 'going'", "bool", True),
            Option("who_can_create", "Who can create events", "choice", "staff", choices=("staff", "everyone")),
        ], default_enabled=True))
        bot.add_dynamic_items(RSVPButton)

    async def cog_load(self):
        self.reminder_loop.start()

    async def cog_unload(self):
        self.reminder_loop.cancel()

    def get(self, event_id, guild_id=None):
        row = self.db.fetchone("SELECT * FROM events WHERE id=?" + (" AND guild_id=?" if guild_id else ""), (event_id, guild_id) if guild_id else (event_id,))
        return dict(row) if row else None

    def rsvps(self, event_id):
        out = {"going": [], "interested": [], "not": []}
        for r in self.db.fetchall("SELECT user_id, status FROM event_rsvps WHERE event_id=? ORDER BY ts", (event_id,)):
            out.setdefault(r["status"], []).append(r["user_id"])
        return out

    def embed(self, ev):
        status = ev["status"]
        colour = {"scheduled": ui.PINK, "cancelled": ui.BAD, "done": ui.WARN}.get(status, ui.INFO)
        e = discord.Embed(title=("❌ CANCELLED · " if status == "cancelled" else "📅 ") + ev["title"], description=ui.clip(ev["description"] or "", 3000), colour=colour)
        start = int(ev["start_ts"])
        e.add_field(name="When", value=f"<t:{start}:F>\n<t:{start}:R>", inline=True)
        e.add_field(name="Host", value=f"<@{ev['creator_id']}>", inline=True)
        r = self.rsvps(ev["id"])
        e.add_field(name=f"✅ Going ({len(r['going'])})", value=ui.clip(" ".join(f"<@{u}>" for u in r["going"][:30]) or "—", 1000), inline=False)
        e.add_field(name=f"⭐ Interested ({len(r['interested'])})", value=ui.clip(" ".join(f"<@{u}>" for u in r["interested"][:30]) or "—", 1000), inline=False)
        e.set_footer(text=f"Event #{ev['id']} · times show in your own timezone")
        return e

    def view(self, ev):
        disabled = ev["status"] != "scheduled"
        v = discord.ui.View(timeout=None)
        for status in RSVP:
            v.add_item(RSVPButton(ev["id"], status, disabled))
        return v

    async def refresh(self, ev):
        guild = self.bot.get_guild(ev["guild_id"])
        channel = guild.get_channel(ev["channel_id"]) if guild and ev["channel_id"] else None
        if channel is None or not ev["message_id"]:
            return
        try:
            msg = await channel.fetch_message(ev["message_id"])
            await msg.edit(embed=self.embed(ev), view=self.view(ev))
        except discord.HTTPException:
            pass

    async def rsvp(self, interaction, event_id, status):
        ev = self.get(event_id, interaction.guild_id)
        if ev is None:
            return await interaction.response.send_message("This event no longer exists.", ephemeral=True)
        if ev["status"] != "scheduled":
            return await interaction.response.send_message(f"This event is {ev['status']}.", ephemeral=True)
        current = self.db.scalar("SELECT status FROM event_rsvps WHERE event_id=? AND user_id=?", (event_id, interaction.user.id))
        if current == status:
            self.db.execute("DELETE FROM event_rsvps WHERE event_id=? AND user_id=?", (event_id, interaction.user.id))
            note = "RSVP removed."
        else:
            self.db.execute("INSERT INTO event_rsvps(event_id, user_id, status, ts) VALUES (?,?,?,?) ON CONFLICT(event_id, user_id) DO UPDATE SET status=excluded.status, ts=excluded.ts",
                            (event_id, interaction.user.id, status, time.time()))
            note = f"You're marked as **{RSVP[status][0]}**."
        await interaction.response.edit_message(embed=self.embed(ev), view=self.view(ev))
        await interaction.followup.send(note, ephemeral=True)

    def can_create(self, member):
        return self.bot.registry.get(member.guild.id, "events").get("who_can_create") == "everyone" or checks.is_mod(member) or checks.has_perm(member, "manage_events")

    def tz_for(self, guild_id, user_id):
        default = self.bot.registry.get(guild_id, "events").get("timezone") or "Asia/Kolkata"
        return user_tz(self.db, user_id, default)

    async def create(self, target, guild, author, title, when, description, channel=None):
        s = self.bot.registry.get(guild.id, "events")
        if not s.get("enabled"):
            return await ui.reply(target, embed=ui.err("Events are switched off here."))
        if not self.can_create(author):
            return await ui.reply(target, embed=ui.err("Only staff can create events here."))
        tz = self.tz_for(guild.id, author.id)
        try:
            start = parse_when(when, tz)
        except ValueError as exc:
            return await ui.reply(target, embed=ui.err(str(exc)))
        channel = channel or (guild.get_channel(int(s["channel"])) if s.get("channel") else None) or getattr(target, "channel", None)
        try:
            offsets = sorted({max(0, int(x)) for x in str(s.get("reminders") or "60,0").split(",") if x.strip().isdigit()}, reverse=True)
        except ValueError:
            offsets = [60, 0]
        eid = self.db.insert("INSERT INTO events(guild_id, channel_id, creator_id, title, description, start_ts, tz, reminders, created_ts) VALUES (?,?,?,?,?,?,?,?,?)",
                             (guild.id, channel.id, author.id, title[:200], (description or "")[:3000], start.timestamp(), tz_name(tz), json.dumps(offsets), time.time()))
        ev = self.get(eid)
        msg = await channel.send(embed=self.embed(ev), view=self.view(ev))
        self.db.execute("UPDATE events SET message_id=? WHERE id=?", (msg.id, eid))
        local = start.astimezone(tz).strftime("%a %d %b %Y, %H:%M")
        await ui.reply(target, embed=ui.ok(f"Event #{eid} created for **{local}** ({tz_name(tz)}) in {channel.mention}."))

    @tasks.loop(seconds=30)
    async def reminder_loop(self):
        now = time.time()
        for row in self.db.fetchall("SELECT * FROM events WHERE status='scheduled' AND start_ts<=?", (now + 7 * 86400,)):
            ev = dict(row)
            offsets = json.loads(ev["reminders"] or "[]")
            for offset in offsets:
                if now < ev["start_ts"] - offset * 60 or now > ev["start_ts"] + 3600:
                    continue
                with self.db.transaction() as conn:
                    reminded = json.loads(conn.execute("SELECT reminded FROM events WHERE id=?", (ev["id"],)).fetchone()[0] or "[]")
                    if offset in reminded:
                        continue
                    reminded.append(offset)
                    conn.execute("UPDATE events SET reminded=? WHERE id=?", (json.dumps(reminded), ev["id"]))
                await self.send_reminder(ev, offset)
            if now > ev["start_ts"] + 2 * 3600:
                self.db.execute("UPDATE events SET status='done' WHERE id=? AND status='scheduled'", (ev["id"],))
                ev["status"] = "done"
                await self.refresh(ev)

    async def send_reminder(self, ev, offset):
        guild = self.bot.get_guild(ev["guild_id"])
        channel = guild.get_channel(ev["channel_id"]) if guild else None
        going = self.rsvps(ev["id"])["going"]
        when = "is starting now!" if offset == 0 else f"starts <t:{int(ev['start_ts'])}:R>"
        link = f" [details]({ui.jump(guild.id, ev['channel_id'], ev['message_id'])})" if guild and ev["message_id"] else ""
        text = f"⏰ **{ev['title']}** {when}{link}"
        if channel:
            mentions = " ".join(f"<@{u}>" for u in going[:40])
            await ui.safe_send(channel, f"{text}\n{mentions}".strip(), allowed_mentions=discord.AllowedMentions(users=True))
        if guild and self.bot.registry.get(guild.id, "events").get("dm_reminders", True):
            from common.diag import user_prefs
            for uid in going[:100]:
                member = guild.get_member(uid)
                if member and user_prefs(self.db, uid).get("dm_reminders", 1):
                    await ui.safe_dm(member, f"{text} (in **{guild.name}**)")

    @reminder_loop.before_loop
    async def before_reminders(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------------- commands
    @commands.group(name="event", aliases=["events"], invoke_without_command=True)
    @commands.guild_only()
    async def event(self, ctx):
        """Events: create, list, past, info, edit, cancel, stats."""
        await self.upcoming(ctx)

    @event.command(name="create", aliases=["new"])
    async def create_cmd(self, ctx, *, spec: str):
        """`!event create Title | when | description` — when: `2026-12-31 20:00`, `tomorrow 7pm`, `in 3h`."""
        parts = [p.strip() for p in spec.split("|")]
        if len(parts) < 2:
            return await ui.reply(ctx, embed=ui.err("Use `!event create Title | when | description`, e.g. `!event create Movie night | 2026-12-05 21:00 | Bring snacks`."))
        await self.create(ctx, ctx.guild, ctx.author, parts[0], parts[1], parts[2] if len(parts) > 2 else "", ctx.channel if not self.bot.registry.get(ctx.guild.id, "events").get("channel") else None)

    @app_commands.command(name="eventcreate", description="Create a community event with RSVP buttons")
    @app_commands.guild_only()
    @app_commands.describe(title="Event name", when="e.g. 2026-12-31 20:00, tomorrow 7pm, in 3h (your timezone)", description="Details", channel="Where to post")
    async def eventcreate(self, interaction: discord.Interaction, title: str, when: str, description: str = "", channel: discord.TextChannel = None):
        await self.create(interaction, interaction.guild, interaction.user, title, when, description, channel or interaction.channel)

    @event.command(name="list", aliases=["upcoming"])
    async def upcoming(self, ctx):
        rows = self.db.fetchall("SELECT * FROM events WHERE guild_id=? AND status='scheduled' ORDER BY start_ts LIMIT 50", (ctx.guild.id,))
        lines = [f"`#{r['id']}` **{ui.clip(r['title'], 60)}** · <t:{int(r['start_ts'])}:f> (<t:{int(r['start_ts'])}:R>) · {len(self.rsvps(r['id'])['going'])} going" for r in rows]
        await ui.paginate(ctx, ui.pages_from_lines("📅 Upcoming events", lines, footer="RSVP with the buttons on each event post"))

    @event.command(name="past", aliases=["history"])
    async def past(self, ctx):
        rows = self.db.fetchall("SELECT * FROM events WHERE guild_id=? AND status IN ('done','cancelled') ORDER BY start_ts DESC LIMIT 100", (ctx.guild.id,))
        lines = [f"`#{r['id']}` {'❌ ' if r['status'] == 'cancelled' else ''}**{ui.clip(r['title'], 60)}** · <t:{int(r['start_ts'])}:d> · {len(self.rsvps(r['id'])['going'])} attended (RSVP)" for r in rows]
        await ui.paginate(ctx, ui.pages_from_lines("🗓️ Past events", lines))

    @event.command(name="info")
    async def info(self, ctx, event_id: int):
        ev = self.get(event_id, ctx.guild.id)
        await ui.reply(ctx, embed=self.embed(ev) if ev else ui.err("No such event."))

    def _may_edit(self, member, ev):
        return ev["creator_id"] == member.id or checks.is_mod(member) or checks.has_perm(member, "manage_events")

    @event.command(name="edit")
    async def edit(self, ctx, event_id: int, field: str, *, value: str):
        """`!event edit <id> title|when|description <value>`"""
        ev = self.get(event_id, ctx.guild.id)
        if ev is None:
            return await ui.reply(ctx, embed=ui.err("No such event."))
        if not self._may_edit(ctx.author, ev):
            return await ui.reply(ctx, embed=ui.err("Only the host or staff can edit this event."))
        field = field.casefold()
        if field == "title":
            self.db.execute("UPDATE events SET title=? WHERE id=?", (value[:200], event_id))
        elif field in ("description", "desc"):
            self.db.execute("UPDATE events SET description=? WHERE id=?", (value[:3000], event_id))
        elif field in ("when", "time", "date"):
            try:
                start = parse_when(value, self.tz_for(ctx.guild.id, ctx.author.id))
            except ValueError as exc:
                return await ui.reply(ctx, embed=ui.err(str(exc)))
            self.db.execute("UPDATE events SET start_ts=?, reminded='[]' WHERE id=?", (start.timestamp(), event_id))
        else:
            return await ui.reply(ctx, embed=ui.err("Field must be title, when or description."))
        ev = self.get(event_id)
        await self.refresh(ev)
        await ui.reply(ctx, embed=ui.ok(f"Event #{event_id} updated."))

    @event.command(name="cancel")
    async def cancel(self, ctx, event_id: int, *, reason: str = ""):
        ev = self.get(event_id, ctx.guild.id)
        if ev is None or ev["status"] != "scheduled":
            return await ui.reply(ctx, embed=ui.err("No scheduled event with that ID."))
        if not self._may_edit(ctx.author, ev):
            return await ui.reply(ctx, embed=ui.err("Only the host or staff can cancel this event."))
        if not await ui.confirm(ctx, f"Cancel **{ev['title']}**? Everyone who RSVP'd 'going' gets a DM."):
            return
        self.db.execute("UPDATE events SET status='cancelled' WHERE id=?", (event_id,))
        ev["status"] = "cancelled"
        await self.refresh(ev)
        for uid in self.rsvps(event_id)["going"]:
            member = ctx.guild.get_member(uid)
            if member:
                await ui.safe_dm(member, f"❌ **{ev['title']}** in **{ctx.guild.name}** was cancelled." + (f" Reason: {reason}" if reason else ""))
        await ui.reply(ctx, embed=ui.ok("Event cancelled."))

    @event.command(name="stats")
    async def stats(self, ctx):
        total = self.db.scalar("SELECT count(*) FROM events WHERE guild_id=?", (ctx.guild.id,), 0)
        done = self.db.scalar("SELECT count(*) FROM events WHERE guild_id=? AND status='done'", (ctx.guild.id,), 0)
        cancelled = self.db.scalar("SELECT count(*) FROM events WHERE guild_id=? AND status='cancelled'", (ctx.guild.id,), 0)
        avg = self.db.scalar("SELECT avg(c) FROM (SELECT count(*) c FROM event_rsvps r JOIN events e ON e.id=r.event_id WHERE e.guild_id=? AND e.status='done' AND r.status='going' GROUP BY e.id)", (ctx.guild.id,), 0)
        top = self.db.fetchall("SELECT r.user_id, count(*) c FROM event_rsvps r JOIN events e ON e.id=r.event_id WHERE e.guild_id=? AND r.status='going' AND e.status='done' GROUP BY r.user_id ORDER BY c DESC LIMIT 5", (ctx.guild.id,))
        e = discord.Embed(title="📅 Event stats", colour=ui.INFO)
        e.add_field(name="Events", value=f"{total} total · {done} held · {cancelled} cancelled")
        e.add_field(name="Avg. attendance (RSVP going)", value=f"{avg or 0:.1f}")
        if top:
            e.add_field(name="Most regular attendees", value="\n".join(f"<@{r['user_id']}> · {r['c']}" for r in top), inline=False)
        await ui.reply(ctx, embed=e)


async def setup(bot):
    await bot.add_cog(Events(bot))
