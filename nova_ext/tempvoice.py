"""Join-to-create temporary voice channels with owner controls.

Safety: NOVA only ever deletes voice channels that it created (recorded in nova.db). Ordinary
voice channels are never deleted, even if empty. Owners don't get Discord permissions on their room;
NOVA applies their changes after checking ownership, so nobody can escalate permissions.
"""
import asyncio
import time

import discord
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option


def get_room(db, channel_id):
    row = db.fetchone("SELECT * FROM tempvoice_channels WHERE channel_id=?", (channel_id,))
    return dict(row) if row else None


class RoomButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tv:(?P<cid>\d+):(?P<action>rename|limit|lock|hide|transfer|claim)"):
    LABELS = {"rename": ("Rename", "✏️"), "limit": ("Limit", "👥"), "lock": ("Lock/Unlock", "🔒"), "hide": ("Hide/Show", "👁️"),
              "transfer": ("Transfer", "🔁"), "claim": ("Claim", "👑")}

    def __init__(self, cid, action):
        label, emoji = self.LABELS[action]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=f"tv:{cid}:{action}"))
        self.cid, self.action = int(cid), action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["cid"], match["action"])

    async def callback(self, interaction):
        await interaction.client.get_cog("TempVoice").button(interaction, self.cid, self.action)


class TextModal(discord.ui.Modal):
    def __init__(self, title, label, callback, max_length=100, default=None):
        super().__init__(title=title)
        self.cb = callback
        self.value = discord.ui.TextInput(label=label, max_length=max_length, default=default)
        self.add_item(self.value)

    async def on_submit(self, interaction):
        await self.cb(interaction, str(self.value.value).strip())


class TransferPicker(discord.ui.View):
    def __init__(self, cog, channel):
        super().__init__(timeout=60)
        self.cog, self.channel = cog, channel
        select = discord.ui.UserSelect(placeholder="New owner (must be in the room)…")
        select.callback = self.chosen
        self.select = select
        self.add_item(select)

    async def chosen(self, interaction):
        await self.cog.transfer(interaction, self.channel, self.select.values[0])


class TempVoice(commands.Cog, name="TempVoice"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.cooldown = {}
        self.locks = {}
        bot.db.migrate("tempvoice_001", """
            CREATE TABLE IF NOT EXISTS tempvoice_hubs (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL UNIQUE,
                category_id INTEGER, name_template TEXT NOT NULL DEFAULT '{user}''s room', default_limit INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tempvoice_channels (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL,
                hub_id INTEGER, created_ts REAL NOT NULL, locked INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0)""")
        bot.registry.register(Module("tempvoice", "Temporary voice channels", "🔊", "Join a hub channel to get your own voice room; it disappears when empty.", [
            Option("max_rooms", "Max rooms per server", "int", 50, "", 1, 200),
            Option("cooldown", "Create cooldown per member (s)", "int", 15, "", 0, 600),
            Option("panel", "Post control panel in room chat", "bool", True),
        ], default_enabled=True))
        bot.add_dynamic_items(RoomButton)

    async def cog_load(self):
        self.sweep.start()

    async def cog_unload(self):
        self.sweep.cancel()

    # ---------------------------------------------------------------- lifecycle
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        if after.channel and after.channel != before.channel:
            hub = self.db.fetchone("SELECT * FROM tempvoice_hubs WHERE channel_id=?", (after.channel.id,))
            if hub and self.bot.registry.enabled(member.guild.id, "tempvoice"):
                await self.create_room(member, dict(hub))
        if before.channel and before.channel != after.channel and get_room(self.db, before.channel.id):
            await self.maybe_delete(before.channel)

    async def create_room(self, member, hub):
        guild = member.guild
        s = self.bot.registry.get(guild.id, "tempvoice")
        if time.time() - self.cooldown.get(member.id, 0) < int(s.get("cooldown") or 0):
            return
        self.cooldown[member.id] = time.time()
        existing = self.db.fetchone("SELECT channel_id FROM tempvoice_channels WHERE guild_id=? AND owner_id=?", (guild.id, member.id))
        if existing:
            room = guild.get_channel(existing["channel_id"])
            if room:
                try:
                    return await member.move_to(room, reason="Back to their temporary room")
                except discord.HTTPException:
                    return
        if self.db.scalar("SELECT count(*) FROM tempvoice_channels WHERE guild_id=?", (guild.id,), 0) >= int(s.get("max_rooms") or 50):
            return
        hub_channel = guild.get_channel(hub["channel_id"])
        category = guild.get_channel(hub["category_id"]) if hub["category_id"] else (hub_channel.category if hub_channel else None)
        me = guild.me
        perms = (category or guild).permissions_for(me) if category else me.guild_permissions
        if not (perms.manage_channels and perms.move_members and perms.connect):
            from common.errors import record_problem
            record_problem("tempvoice", f"Missing Manage Channels/Move Members/Connect for temp rooms in {guild.name}")
            return
        name = hub["name_template"].replace("{user}", member.display_name)[:95]
        overwrites = dict(category.overwrites) if isinstance(category, discord.CategoryChannel) else {}
        overwrites[me] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True, manage_channels=True, send_messages=True, embed_links=True)
        try:
            room = await guild.create_voice_channel(name, category=category if isinstance(category, discord.CategoryChannel) else None,
                                                    user_limit=hub["default_limit"] or 0, overwrites=overwrites, reason=f"Temporary room for {member}")
        except discord.HTTPException:
            return
        self.db.execute("INSERT INTO tempvoice_channels(channel_id, guild_id, owner_id, hub_id, created_ts) VALUES (?,?,?,?,?)",
                        (room.id, guild.id, member.id, hub["id"], time.time()))
        try:
            await member.move_to(room, reason="Join-to-create")
        except discord.HTTPException:
            await self.maybe_delete(room, grace=0)
            return
        if s.get("panel", True):
            await ui.safe_send(room, content=member.mention, embed=self.panel_embed(room, member.id), view=self.panel_view(room.id),
                               allowed_mentions=discord.AllowedMentions(users=True))

    async def maybe_delete(self, channel, grace=5):
        cid, guild = channel.id, channel.guild
        lock = self.locks.setdefault(cid, asyncio.Lock())
        async with lock:
            if grace:
                await asyncio.sleep(grace)
            channel = guild.get_channel(cid)
            if channel is None:
                self.db.execute("DELETE FROM tempvoice_channels WHERE channel_id=?", (cid,))
                return
            if not get_room(self.db, channel.id):   # never delete channels NOVA didn't create
                return
            if any(not m.bot for m in channel.members):
                return
            try:
                await channel.delete(reason="Temporary room is empty")
            except discord.NotFound:
                pass
            except discord.HTTPException:
                return
            self.db.execute("DELETE FROM tempvoice_channels WHERE channel_id=?", (cid,))
        self.locks.pop(cid, None)

    @tasks.loop(minutes=2)
    async def sweep(self):
        """Restart recovery: forget rooms that no longer exist and remove empty ones."""
        for row in self.db.fetchall("SELECT * FROM tempvoice_channels"):
            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                continue
            channel = guild.get_channel(row["channel_id"])
            if channel is None:
                self.db.execute("DELETE FROM tempvoice_channels WHERE channel_id=?", (row["channel_id"],))
            elif not any(not m.bot for m in channel.members):
                await self.maybe_delete(channel, grace=0)

    @sweep.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------------- controls
    def panel_embed(self, channel, owner_id):
        room = get_room(self.db, channel.id) or {}
        e = discord.Embed(title="🔊 Your temporary room", colour=ui.PINK,
                          description="Use the buttons (or `!mvc …` commands) to manage this room. It's deleted automatically when everyone leaves.")
        e.add_field(name="Owner", value=f"<@{owner_id}>")
        e.add_field(name="Locked", value="yes" if room.get("locked") else "no")
        e.add_field(name="Hidden", value="yes" if room.get("hidden") else "no")
        return e

    def panel_view(self, cid):
        v = discord.ui.View(timeout=None)
        for action in ("rename", "limit", "lock", "hide", "transfer", "claim"):
            v.add_item(RoomButton(cid, action))
        return v

    def owner_check(self, member, channel):
        room = get_room(self.db, channel.id) if channel else None
        if room is None:
            return None, "This isn't a temporary room."
        if room["owner_id"] != member.id and not checks.has_perm(member, "manage_channels"):
            return None, f"Only the room owner (<@{room['owner_id']}>) can do that."
        return room, None

    async def apply(self, target, member, channel, action, value=None):
        room, problem = self.owner_check(member, channel)
        if problem and action != "claim":
            return await ui.reply(target, embed=ui.err(problem))
        everyone = channel.guild.default_role
        try:
            if action == "rename":
                if not value:
                    return await ui.reply(target, embed=ui.err("Give a name."))
                await channel.edit(name=value[:95], reason=f"Temp room rename by {member}")
                msg = f"Renamed to **{discord.utils.escape_markdown(value[:95])}**."
            elif action == "limit":
                n = int(value)
                if not 0 <= n <= 99:
                    raise ValueError
                await channel.edit(user_limit=n, reason=f"Temp room limit by {member}")
                msg = f"User limit set to **{n or 'unlimited'}**."
            elif action == "lock":
                locked = not room["locked"]
                await channel.set_permissions(everyone, connect=not locked and None, reason=f"Temp room lock by {member}")
                for m in channel.members:   # people already inside can still reconnect
                    if locked:
                        await channel.set_permissions(m, connect=True)
                self.db.execute("UPDATE tempvoice_channels SET locked=? WHERE channel_id=?", (int(locked), channel.id))
                msg = "🔒 Locked — only people already inside can join." if locked else "🔓 Unlocked."
            elif action == "hide":
                hidden = not room["hidden"]
                await channel.set_permissions(everyone, view_channel=False if hidden else None, connect=False if room["locked"] else None, reason=f"Temp room visibility by {member}")
                for m in channel.members:
                    if hidden:
                        await channel.set_permissions(m, view_channel=True, connect=True)
                self.db.execute("UPDATE tempvoice_channels SET hidden=? WHERE channel_id=?", (int(hidden), channel.id))
                msg = "👁️ Hidden from the channel list." if hidden else "👁️ Visible again."
            elif action == "claim":
                room = get_room(self.db, channel.id)
                if room is None:
                    return await ui.reply(target, embed=ui.err("This isn't a temporary room."))
                if any(m.id == room["owner_id"] for m in channel.members):
                    return await ui.reply(target, embed=ui.err("The owner is still here."))
                if not member.voice or member.voice.channel != channel:
                    return await ui.reply(target, embed=ui.err("Join the room to claim it."))
                self.db.execute("UPDATE tempvoice_channels SET owner_id=? WHERE channel_id=?", (member.id, channel.id))
                msg = f"👑 {member.mention} now owns this room."
            else:
                return
        except ValueError:
            return await ui.reply(target, embed=ui.err("Limit must be a number from 0 (unlimited) to 99."))
        except discord.HTTPException:
            return await ui.reply(target, embed=ui.err("Discord refused that change (check my Manage Channels permission)."))
        await ui.reply(target, embed=ui.ok(msg))

    async def transfer(self, target, channel, new_owner):
        member = target.user if isinstance(target, discord.Interaction) else target.author
        room, problem = self.owner_check(member, channel)
        if problem:
            return await ui.reply(target, embed=ui.err(problem))
        if new_owner.bot or not getattr(new_owner, "voice", None) or new_owner.voice.channel != channel:
            return await ui.reply(target, embed=ui.err("The new owner must be a member who is in this room."))
        self.db.execute("UPDATE tempvoice_channels SET owner_id=? WHERE channel_id=?", (new_owner.id, channel.id))
        await ui.reply(target, embed=ui.ok(f"🔁 {new_owner.mention} now owns this room."))

    async def button(self, interaction, cid, action):
        channel = interaction.guild.get_channel(cid)
        if channel is None:
            return await interaction.response.send_message("This room no longer exists.", ephemeral=True)
        member = interaction.user
        if action == "rename":
            room, problem = self.owner_check(member, channel)
            if problem:
                return await interaction.response.send_message(problem, ephemeral=True)
            return await interaction.response.send_modal(TextModal("Rename room", "New name", lambda i, v: self.apply(i, member, channel, "rename", v), 95, channel.name))
        if action == "limit":
            room, problem = self.owner_check(member, channel)
            if problem:
                return await interaction.response.send_message(problem, ephemeral=True)
            return await interaction.response.send_modal(TextModal("User limit", "0 = unlimited, max 99", lambda i, v: self.apply(i, member, channel, "limit", v), 2, str(channel.user_limit)))
        if action == "transfer":
            room, problem = self.owner_check(member, channel)
            if problem:
                return await interaction.response.send_message(problem, ephemeral=True)
            return await interaction.response.send_message("Pick the new owner:", view=TransferPicker(self, channel), ephemeral=True)
        await self.apply(interaction, member, channel, action)

    def my_room(self, ctx):
        return ctx.author.voice.channel if ctx.author.voice and ctx.author.voice.channel and get_room(self.db, ctx.author.voice.channel.id) else None

    # ---------------------------------------------------------------- commands
    @commands.group(name="mvc", aliases=["mtempvc"], invoke_without_command=True)
    @commands.guild_only()
    async def mvc(self, ctx):
        """Temporary rooms. In your room: name, limit, lock, unlock, hide, show, transfer, claim, panel. Admin: setup, hubs, removehub."""
        await ui.reply(ctx, embed=ui.embed("🔊 Temporary voice rooms",
            "**In your room:** `!mvc name <text>` · `!mvc limit <0-99>` · `!mvc lock` · `!mvc unlock` · `!mvc hide` · `!mvc show` · `!mvc transfer @member` · `!mvc claim` · `!mvc panel`\n"
            "**Admins:** `!mvc setup <#hub voice channel> [category] [| name template]` · `!mvc hubs` · `!mvc removehub <#channel>`\nTemplate placeholder: `{user}`"))

    async def _room_cmd(self, ctx, action, value=None):
        room = self.my_room(ctx)
        if room is None:
            return await ui.reply(ctx, embed=ui.err("Join your temporary room first."))
        await self.apply(ctx, ctx.author, room, action, value)

    @mvc.command(name="name", aliases=["rename"])
    async def mvc_name(self, ctx, *, name: str):
        await self._room_cmd(ctx, "rename", name)

    @mvc.command(name="limit")
    async def mvc_limit(self, ctx, limit: str):
        await self._room_cmd(ctx, "limit", limit)

    @mvc.command(name="lock")
    async def mvc_lock(self, ctx):
        room = self.my_room(ctx)
        if room and get_room(self.db, room.id)["locked"]:
            return await ui.reply(ctx, embed=ui.err("Already locked."))
        await self._room_cmd(ctx, "lock")

    @mvc.command(name="unlock")
    async def mvc_unlock(self, ctx):
        room = self.my_room(ctx)
        if room and not get_room(self.db, room.id)["locked"]:
            return await ui.reply(ctx, embed=ui.err("Not locked."))
        await self._room_cmd(ctx, "lock")

    @mvc.command(name="hide")
    async def mvc_hide(self, ctx):
        room = self.my_room(ctx)
        if room and get_room(self.db, room.id)["hidden"]:
            return await ui.reply(ctx, embed=ui.err("Already hidden."))
        await self._room_cmd(ctx, "hide")

    @mvc.command(name="show", aliases=["unhide"])
    async def mvc_show(self, ctx):
        room = self.my_room(ctx)
        if room and not get_room(self.db, room.id)["hidden"]:
            return await ui.reply(ctx, embed=ui.err("Not hidden."))
        await self._room_cmd(ctx, "hide")

    @mvc.command(name="claim")
    async def mvc_claim(self, ctx):
        room = self.my_room(ctx)
        if room is None:
            return await ui.reply(ctx, embed=ui.err("Join the temporary room first."))
        await self.apply(ctx, ctx.author, room, "claim")

    @mvc.command(name="transfer")
    async def mvc_transfer(self, ctx, member: discord.Member):
        room = self.my_room(ctx)
        if room is None:
            return await ui.reply(ctx, embed=ui.err("Join your temporary room first."))
        await self.transfer(ctx, room, member)

    @mvc.command(name="panel")
    async def mvc_panel(self, ctx):
        room = self.my_room(ctx)
        if room is None:
            return await ui.reply(ctx, embed=ui.err("Join your temporary room first."))
        await ui.safe_send(room, embed=self.panel_embed(room, get_room(self.db, room.id)["owner_id"]), view=self.panel_view(room.id))
        await ui.reply(ctx, embed=ui.ok("Control panel posted in the room's chat."))

    @mvc.command(name="setup")
    @checks.perm_only("manage_channels", "Manage Channels")
    async def mvc_setup(self, ctx, hub: discord.VoiceChannel, category: discord.CategoryChannel = None, *, template: str = ""):
        template = template.lstrip("| ").strip() or "{user}'s room"
        target = category or hub.category
        perms = (target or ctx.guild).permissions_for(ctx.guild.me) if target else ctx.guild.me.guild_permissions
        missing = [n for n, ok in (("Manage Channels", perms.manage_channels), ("Move Members", perms.move_members), ("Connect", perms.connect), ("View Channel", perms.view_channel)) if not ok]
        if missing:
            return await ui.reply(ctx, embed=ui.err(f"I'm missing {', '.join(missing)}" + (f" in **{target.name}**" if target else "") + "."))
        self.db.execute("INSERT INTO tempvoice_hubs(guild_id, channel_id, category_id, name_template) VALUES (?,?,?,?) "
                        "ON CONFLICT(channel_id) DO UPDATE SET category_id=excluded.category_id, name_template=excluded.name_template",
                        (ctx.guild.id, hub.id, category.id if category else None, template[:80]))
        if not self.bot.registry.enabled(ctx.guild.id, "tempvoice"):
            self.bot.registry.set(ctx.guild, ctx.author, "tempvoice", "enabled", True)
        await ui.reply(ctx, embed=ui.ok(f"Joining {hub.mention} now creates a room" + (f" in **{target.name}**" if target else "") + f" named `{template}`."))

    @mvc.command(name="hubs")
    @checks.perm_only("manage_channels", "Manage Channels")
    async def mvc_hubs(self, ctx):
        rows = self.db.fetchall("SELECT * FROM tempvoice_hubs WHERE guild_id=?", (ctx.guild.id,))
        rooms = self.db.scalar("SELECT count(*) FROM tempvoice_channels WHERE guild_id=?", (ctx.guild.id,), 0)
        await ui.reply(ctx, embed=ui.embed("🔊 Join-to-create hubs", "\n".join(f"<#{r['channel_id']}> → " + (f"<#{r['category_id']}>" if r["category_id"] else "same category") + f" · `{r['name_template']}`" for r in rows) or "None.", Active_rooms=str(rooms)))

    @mvc.command(name="removehub")
    @checks.perm_only("manage_channels", "Manage Channels")
    async def mvc_removehub(self, ctx, hub: discord.VoiceChannel):
        n = self.db.execute("DELETE FROM tempvoice_hubs WHERE guild_id=? AND channel_id=?", (ctx.guild.id, hub.id)).rowcount
        await ui.reply(ctx, embed=ui.ok("Hub removed (the channel itself is untouched).") if n else ui.err("That channel isn't a hub."))


async def setup(bot):
    await bot.add_cog(TempVoice(bot))
