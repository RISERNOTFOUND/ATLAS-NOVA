"""NOVA's role management: button/dropdown role menus, reaction roles, temporary roles, templates,
bulk assign/remove, role info/hierarchy/permission diagnostics, role logs and history.

Every action checks Discord permissions and role hierarchy for BOTH the bot and the member asking.
Roles with staff/administrative permissions can never be put in self-service menus.
The owner-only `!mclearrolecolour` stays in nova.py and is untouched by this module.
"""
import asyncio
import json
import re
import time

import discord
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option
from common.timeparse import fmt_delta, parse_duration

MESSAGE_LINK = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")
BUILTIN_TEMPLATES = {
    "interests": [{"name": n, "colour": 0} for n in ("Gamer", "Music Lover", "Artist", "Producer", "Movie Buff", "Bookworm", "Night Owl")],
    "pings": [{"name": n, "colour": 0, "mentionable": True} for n in ("Event Pings", "Announcement Pings", "Giveaway Pings", "Stream Pings")],
    "pronouns": [{"name": n, "colour": 0} for n in ("he/him", "she/her", "they/them", "any pronouns", "ask my pronouns")],
}


def menu_safe(role, me):
    """Reason a role can't be self-assigned through a menu, or None."""
    problem = checks.role_problem(role, me)
    if problem:
        return problem
    bad = checks.dangerous_perms(role)
    if bad:
        return f"It has staff permissions ({', '.join(bad[:3])})"
    return None


class MenuButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rm:(?P<menu>\d+):(?P<role>\d+)"):
    def __init__(self, menu_id, role_id, label="role", emoji=None):
        super().__init__(discord.ui.Button(label=ui.clip(label, 80), emoji=emoji or None, style=discord.ButtonStyle.secondary, custom_id=f"rm:{menu_id}:{role_id}"))
        self.menu_id, self.role_id = int(menu_id), int(role_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["menu"], match["role"])

    async def callback(self, interaction):
        await interaction.client.get_cog("Roles").menu_pick(interaction, self.menu_id, [self.role_id])


class MenuSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"rmsel:(?P<menu>\d+)"):
    def __init__(self, menu_id, options=None, max_values=1):
        options = options or [discord.SelectOption(label="loading", value="0")]
        super().__init__(discord.ui.Select(custom_id=f"rmsel:{menu_id}", placeholder="Choose your roles…", min_values=0,
                                           max_values=max(1, min(max_values, len(options))), options=options))
        self.menu_id = int(menu_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["menu"])

    async def callback(self, interaction):
        values = [int(v) for v in interaction.data.get("values", [])]
        await interaction.client.get_cog("Roles").menu_select(interaction, self.menu_id, values)


class Roles(commands.Cog, name="Roles"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.member_locks = {}
        bot.db.migrate("roles_001", """
            CREATE TABLE IF NOT EXISTS role_menus (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT,
                style TEXT NOT NULL DEFAULT 'buttons', mode TEXT NOT NULL DEFAULT 'toggle', required_role INTEGER, channel_id INTEGER, message_id INTEGER,
                created_ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS role_menu_items (menu_id INTEGER NOT NULL, role_id INTEGER NOT NULL, label TEXT, emoji TEXT, description TEXT,
                position INTEGER DEFAULT 0, PRIMARY KEY (menu_id, role_id));
            CREATE TABLE IF NOT EXISTS reaction_roles (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, emoji TEXT NOT NULL,
                role_id INTEGER NOT NULL, PRIMARY KEY (message_id, emoji));
            CREATE TABLE IF NOT EXISTS temp_roles (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
                expires_ts REAL NOT NULL, actor_id INTEGER, reason TEXT, status TEXT NOT NULL DEFAULT 'active');
            CREATE INDEX IF NOT EXISTS idx_temp_roles_due ON temp_roles(status, expires_ts);
            CREATE TABLE IF NOT EXISTS role_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL,
                created_ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS role_history (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
                action TEXT NOT NULL, actor_id INTEGER, source TEXT, ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_role_history ON role_history(guild_id, user_id, ts)""")
        bot.registry.register(Module("roles", "Role management", "🎭", "Role menus, temporary roles, templates, bulk tools and role logs.", [
            Option("log_channel", "Role log channel", "channel", None, "Role assignments/removals and role changes"),
            Option("track_history", "Record role change history", "bool", True, "Needs Server Members Intent"),
            Option("cleanup_colour_roles", "Auto-delete unused colour roles", "bool", False, "Only NOVA's own 🎨 #RRGGBB roles with no members"),
            Option("menu_cooldown", "Menu cooldown (seconds)", "int", 3, "", 0, 60),
        ], default_enabled=True))
        bot.add_dynamic_items(MenuButton, MenuSelect)
        self.last_pick = {}

    async def cog_load(self):
        self.temp_loop.start()
        self.cleanup_loop.start()

    async def cog_unload(self):
        self.temp_loop.cancel()
        self.cleanup_loop.cancel()

    # ---------------------------------------------------------------- logging/history
    async def log(self, guild, text, colour=ui.INFO):
        channel = self.bot.registry.channel(guild, "roles", "log_channel")
        if isinstance(channel, discord.TextChannel):
            await ui.safe_send(channel, embed=discord.Embed(description=text[:4000], colour=colour, timestamp=discord.utils.utcnow()))

    def history(self, guild_id, user_id, role_id, action, actor_id=None, source=None):
        self.db.insert("INSERT INTO role_history(guild_id, user_id, role_id, action, actor_id, source, ts) VALUES (?,?,?,?,?,?,?)",
                       (guild_id, user_id, role_id, action, actor_id, source, time.time()))

    async def give(self, member, role, actor_id, source, reason):
        await member.add_roles(role, reason=reason)
        self.history(member.guild.id, member.id, role.id, "add", actor_id, source)
        await self.log(member.guild, f"➕ {role.mention} → {member.mention} · {source}" + (f" by <@{actor_id}>" if actor_id and actor_id != member.id else ""), ui.GOOD)

    async def take(self, member, role, actor_id, source, reason):
        await member.remove_roles(role, reason=reason)
        self.history(member.guild.id, member.id, role.id, "remove", actor_id, source)
        await self.log(member.guild, f"➖ {role.mention} ✕ {member.mention} · {source}" + (f" by <@{actor_id}>" if actor_id and actor_id != member.id else ""), ui.WARN)

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles or not self.bot.registry.get(after.guild.id, "roles").get("track_history", True):
            return
        recent = self.db.fetchall("SELECT role_id, action FROM role_history WHERE guild_id=? AND user_id=? AND ts>?", (after.guild.id, after.id, time.time() - 5))
        mine = {(r["role_id"], r["action"]) for r in recent}
        for role in after.roles:
            if role not in before.roles and (role.id, "add") not in mine:
                self.history(after.guild.id, after.id, role.id, "add", None, "external")
        for role in before.roles:
            if role not in after.roles and (role.id, "remove") not in mine:
                self.history(after.guild.id, after.id, role.id, "remove", None, "external")

    # ---------------------------------------------------------------- menus: data
    def menu(self, menu_id, guild_id=None):
        row = self.db.fetchone("SELECT * FROM role_menus WHERE id=?" + (" AND guild_id=?" if guild_id else ""), (menu_id, guild_id) if guild_id else (menu_id,))
        return dict(row) if row else None

    def items(self, menu_id):
        return [dict(r) for r in self.db.fetchall("SELECT * FROM role_menu_items WHERE menu_id=? ORDER BY position, rowid", (menu_id,))]

    def menu_embed(self, guild, menu):
        items = self.items(menu["id"])
        lines = []
        for it in items:
            role = guild.get_role(it["role_id"])
            lines.append(f"{it['emoji'] or '•'} {role.mention if role else '*deleted role*'}" + (f" — {it['description']}" if it["description"] else ""))
        mode_text = {"toggle": "Pick to add, pick again to remove.", "unique": "You can hold only one of these at a time.", "add_only": "Roles can be added but not removed here."}[menu["mode"]]
        e = discord.Embed(title=menu["title"], description=(menu["description"] + "\n\n" if menu["description"] else "") + "\n".join(lines), colour=ui.PINK)
        e.set_footer(text=mode_text + (" Requires a role to use." if menu["required_role"] else ""))
        return e

    def menu_view(self, guild, menu):
        items = self.items(menu["id"])
        v = discord.ui.View(timeout=None)
        if menu["style"] == "dropdown":
            options = []
            for it in items[:25]:
                role = guild.get_role(it["role_id"])
                if role:
                    options.append(discord.SelectOption(label=ui.clip(it["label"] or role.name, 100), value=str(role.id), emoji=it["emoji"] or None,
                                                        description=ui.clip(it["description"], 100) or None))
            if options:
                v.add_item(MenuSelect(menu["id"], options, 1 if menu["mode"] == "unique" else len(options)))
        else:
            for it in items[:25]:
                role = guild.get_role(it["role_id"])
                if role:
                    v.add_item(MenuButton(menu["id"], role.id, it["label"] or role.name, it["emoji"]))
        return v

    def validate(self, guild, menu):
        problems = []
        items = self.items(menu["id"])
        if not items:
            problems.append("The menu has no roles.")
        for it in items:
            role = guild.get_role(it["role_id"])
            if role is None:
                problems.append(f"Role `{it['role_id']}` no longer exists.")
                continue
            p = menu_safe(role, guild.me)
            if p:
                problems.append(f"{role.mention}: {p}")
        if menu["required_role"] and guild.get_role(menu["required_role"]) is None:
            problems.append("The required role no longer exists.")
        if menu["channel_id"]:
            ch = guild.get_channel(menu["channel_id"])
            if ch is None:
                problems.append("The channel it was posted in no longer exists.")
            else:
                miss = checks.missing_channel_perms(ch, guild.me, "view_channel", "send_messages", "embed_links")
                if miss:
                    problems.append(f"Missing in {ch.mention}: {', '.join(miss)}")
        return problems

    # ---------------------------------------------------------------- menus: interactions
    def eligibility(self, member, menu, role):
        if menu["required_role"] and not any(r.id == menu["required_role"] for r in member.roles):
            return f"You need <@&{menu['required_role']}> to use this menu."
        problem = menu_safe(role, member.guild.me)
        if problem:
            return f"I can't manage {role.mention} right now ({problem}). Please tell staff."
        return None

    async def menu_pick(self, interaction, menu_id, role_ids):
        menu = self.menu(menu_id, interaction.guild_id)
        if menu is None:
            return await interaction.response.send_message("This role menu no longer exists.", ephemeral=True)
        member = interaction.user
        cooldown = int(self.bot.registry.get(interaction.guild_id, "roles").get("menu_cooldown") or 0)
        if time.time() - self.last_pick.get(member.id, 0) < cooldown:
            return await interaction.response.send_message("Slow down a little 🙂", ephemeral=True)
        self.last_pick[member.id] = time.time()
        allowed = {it["role_id"] for it in self.items(menu_id)}
        added, removed, problems = [], [], []
        lock = self.member_locks.setdefault(member.id, asyncio.Lock())
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with lock:
            for rid in role_ids:
                role = interaction.guild.get_role(rid)
                if role is None or rid not in allowed:
                    problems.append("That role is no longer part of this menu.")
                    continue
                why = self.eligibility(member, menu, role)
                if why:
                    problems.append(why)
                    continue
                try:
                    if role in member.roles:
                        if menu["mode"] == "add_only":
                            problems.append(f"{role.mention} can't be removed through this menu.")
                        else:
                            await self.take(member, role, member.id, f"role menu #{menu_id}", f"Role menu #{menu_id}")
                            removed.append(role.mention)
                    else:
                        if menu["mode"] == "unique":
                            for other in [r for r in member.roles if r.id in allowed and r.id != rid]:
                                await self.take(member, other, member.id, f"role menu #{menu_id} (swap)", f"Role menu #{menu_id} unique swap")
                                removed.append(other.mention)
                        await self.give(member, role, member.id, f"role menu #{menu_id}", f"Role menu #{menu_id}")
                        added.append(role.mention)
                except discord.HTTPException:
                    problems.append(f"Discord refused to change {role.mention}.")
        lines = ([f"➕ Added {', '.join(added)}"] if added else []) + ([f"➖ Removed {', '.join(removed)}"] if removed else []) + problems
        await interaction.followup.send("\n".join(lines) or "No changes.", ephemeral=True)

    async def menu_select(self, interaction, menu_id, values):
        """Dropdown semantics: the selection becomes the member's set of roles from this menu (within mode rules)."""
        menu = self.menu(menu_id, interaction.guild_id)
        if menu is None:
            return await interaction.response.send_message("This role menu no longer exists.", ephemeral=True)
        member = interaction.user
        allowed = {it["role_id"] for it in self.items(menu_id)}
        held = {r.id for r in member.roles if r.id in allowed}
        wanted = {v for v in values if v in allowed}
        to_add = list(wanted - held)
        to_remove = [] if menu["mode"] == "add_only" else list(held - wanted)
        if menu["mode"] == "unique" and len(wanted) > 1:
            return await interaction.response.send_message("Pick only one role from this menu.", ephemeral=True)
        if not to_add and not to_remove:
            return await interaction.response.send_message("No changes.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        added, removed, problems = [], [], []
        async with self.member_locks.setdefault(member.id, asyncio.Lock()):
            for rid in to_remove:
                role = interaction.guild.get_role(rid)
                if role is None or menu_safe(role, interaction.guild.me):
                    continue
                try:
                    await self.take(member, role, member.id, f"role menu #{menu_id}", f"Role menu #{menu_id}")
                    removed.append(role.mention)
                except discord.HTTPException:
                    problems.append(f"Couldn't remove {role.mention}.")
            for rid in to_add:
                role = interaction.guild.get_role(rid)
                why = self.eligibility(member, menu, role) if role else "That role no longer exists."
                if why:
                    problems.append(why)
                    continue
                try:
                    await self.give(member, role, member.id, f"role menu #{menu_id}", f"Role menu #{menu_id}")
                    added.append(role.mention)
                except discord.HTTPException:
                    problems.append(f"Couldn't add {role.mention}.")
        lines = ([f"➕ Added {', '.join(added)}"] if added else []) + ([f"➖ Removed {', '.join(removed)}"] if removed else []) + problems
        await interaction.followup.send("\n".join(lines) or "No changes.", ephemeral=True)

    # ---------------------------------------------------------------- menus: commands
    @commands.group(name="mrolemenu", aliases=["mrm"], invoke_without_command=True)
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mrolemenu(self, ctx):
        """Role menus. Subcommands: create, add, remove, mode, require, preview, validate, post, refresh, list, delete."""
        await ui.reply(ctx, embed=ui.embed("🎭 Role menus",
            "`!mrolemenu create buttons|dropdown Title | optional description`\n`!mrolemenu add <id> @Role [emoji] [label] [| description]`\n"
            "`!mrolemenu remove <id> @Role` · `!mrolemenu mode <id> toggle|unique|add_only` · `!mrolemenu require <id> @Role|none`\n"
            "`!mrolemenu preview <id>` · `!mrolemenu validate <id>` · `!mrolemenu post <id> #channel` · `!mrolemenu refresh <id>` · `!mrolemenu list` · `!mrolemenu delete <id>`"))

    @mrolemenu.command(name="create")
    async def rm_create(self, ctx, style: str, *, text: str):
        style = style.casefold()
        if style not in ("buttons", "dropdown"):
            return await ui.reply(ctx, embed=ui.err("Style must be `buttons` or `dropdown`."))
        title, _, desc = text.partition("|")
        mid = self.db.insert("INSERT INTO role_menus(guild_id, title, description, style, created_ts) VALUES (?,?,?,?,?)",
                             (ctx.guild.id, title.strip()[:200], desc.strip()[:1500], style, time.time()))
        await ui.reply(ctx, embed=ui.ok(f"Menu #{mid} created. Add roles with `!mrolemenu add {mid} @Role 🎮 Label`."))

    async def _menu_or_err(self, ctx, menu_id):
        menu = self.menu(menu_id, ctx.guild.id)
        if menu is None:
            await ui.reply(ctx, embed=ui.err("No role menu with that ID in this server."))
        return menu

    @mrolemenu.command(name="add")
    async def rm_add(self, ctx, menu_id: int, role: discord.Role, *, rest: str = ""):
        menu = await self._menu_or_err(ctx, menu_id)
        if not menu:
            return
        problem = menu_safe(role, ctx.guild.me) or (checks.role_problem(role, ctx.guild.me, ctx.author) if ctx.author.id != ctx.guild.owner_id else None)
        if problem:
            return await ui.reply(ctx, embed=ui.err(f"{role.mention} can't go in a self-service menu: {problem}"))
        if len(self.items(menu_id)) >= 25:
            return await ui.reply(ctx, embed=ui.err("A menu holds at most 25 roles."))
        main, _, desc = rest.partition("|")
        words = main.split(maxsplit=1)
        emoji, label = None, None
        if words and (discord.PartialEmoji.from_str(words[0]).id or not words[0].isascii()):
            emoji, label = words[0], (words[1] if len(words) > 1 else None)
        else:
            label = main.strip() or None
        self.db.execute("INSERT INTO role_menu_items(menu_id, role_id, label, emoji, description, position) VALUES (?,?,?,?,?,?) "
                        "ON CONFLICT(menu_id, role_id) DO UPDATE SET label=excluded.label, emoji=excluded.emoji, description=excluded.description",
                        (menu_id, role.id, (label or "")[:80] or None, emoji, desc.strip()[:100] or None, len(self.items(menu_id))))
        await ui.reply(ctx, embed=ui.ok(f"Added {role.mention} to menu #{menu_id}. `!mrolemenu refresh {menu_id}` updates a posted menu."))

    @mrolemenu.command(name="remove")
    async def rm_remove(self, ctx, menu_id: int, role: discord.Role):
        if not await self._menu_or_err(ctx, menu_id):
            return
        n = self.db.execute("DELETE FROM role_menu_items WHERE menu_id=? AND role_id=?", (menu_id, role.id)).rowcount
        await ui.reply(ctx, embed=ui.ok("Removed.") if n else ui.err("That role isn't in the menu."))

    @mrolemenu.command(name="mode")
    async def rm_mode(self, ctx, menu_id: int, mode: str):
        if not await self._menu_or_err(ctx, menu_id):
            return
        mode = mode.casefold().replace("-", "_")
        if mode not in ("toggle", "unique", "add_only"):
            return await ui.reply(ctx, embed=ui.err("Mode must be toggle, unique or add_only."))
        self.db.execute("UPDATE role_menus SET mode=? WHERE id=?", (mode, menu_id))
        await ui.reply(ctx, embed=ui.ok(f"Menu #{menu_id} mode: **{mode}**."))

    @mrolemenu.command(name="require")
    async def rm_require(self, ctx, menu_id: int, *, role: str):
        if not await self._menu_or_err(ctx, menu_id):
            return
        if role.casefold() == "none":
            self.db.execute("UPDATE role_menus SET required_role=NULL WHERE id=?", (menu_id,))
            return await ui.reply(ctx, embed=ui.ok("Requirement removed."))
        try:
            r = await commands.RoleConverter().convert(ctx, role)
        except commands.BadArgument:
            return await ui.reply(ctx, embed=ui.err("I couldn't find that role."))
        self.db.execute("UPDATE role_menus SET required_role=? WHERE id=?", (r.id, menu_id))
        await ui.reply(ctx, embed=ui.ok(f"Members now need {r.mention} to use menu #{menu_id}."))

    @mrolemenu.command(name="preview")
    async def rm_preview(self, ctx, menu_id: int):
        menu = await self._menu_or_err(ctx, menu_id)
        if menu:
            problems = self.validate(ctx.guild, menu)
            await ui.reply(ctx, content=("⚠️ " + " / ".join(problems))[:1900] if problems else "Preview (buttons are live but changes only apply to the real posted menu's rules):",
                           embed=self.menu_embed(ctx.guild, menu), view=self.menu_view(ctx.guild, menu))

    @mrolemenu.command(name="validate")
    async def rm_validate(self, ctx, menu_id: int):
        menu = await self._menu_or_err(ctx, menu_id)
        if menu:
            problems = self.validate(ctx.guild, menu)
            await ui.reply(ctx, embed=ui.ok(f"Menu #{menu_id} looks good.") if not problems else ui.err("\n".join(problems)))

    @mrolemenu.command(name="post")
    async def rm_post(self, ctx, menu_id: int, channel: discord.TextChannel = None):
        menu = await self._menu_or_err(ctx, menu_id)
        if not menu:
            return
        problems = self.validate(ctx.guild, {**menu, "channel_id": None})
        if problems:
            return await ui.reply(ctx, embed=ui.err("Fix these first:\n" + "\n".join(problems)))
        channel = channel or ctx.channel
        msg = await channel.send(embed=self.menu_embed(ctx.guild, menu), view=self.menu_view(ctx.guild, menu))
        self.db.execute("UPDATE role_menus SET channel_id=?, message_id=? WHERE id=?", (channel.id, msg.id, menu_id))
        await ui.reply(ctx, embed=ui.ok(f"Menu #{menu_id} posted in {channel.mention}."))

    @mrolemenu.command(name="refresh")
    async def rm_refresh(self, ctx, menu_id: int):
        menu = await self._menu_or_err(ctx, menu_id)
        if not menu or not menu["message_id"]:
            return await ui.reply(ctx, embed=ui.err("That menu hasn't been posted yet.")) if menu else None
        channel = ctx.guild.get_channel(menu["channel_id"])
        try:
            msg = await channel.fetch_message(menu["message_id"])
            await msg.edit(embed=self.menu_embed(ctx.guild, menu), view=self.menu_view(ctx.guild, menu))
        except (discord.HTTPException, AttributeError):
            return await ui.reply(ctx, embed=ui.err("I couldn't find the posted menu. Post it again with `!mrolemenu post`."))
        await ui.reply(ctx, embed=ui.ok("Menu refreshed."))

    @mrolemenu.command(name="list")
    async def rm_list(self, ctx):
        rows = self.db.fetchall("SELECT * FROM role_menus WHERE guild_id=? ORDER BY id", (ctx.guild.id,))
        lines = [f"`#{r['id']}` **{r['title']}** · {r['style']} · {r['mode']} · {len(self.items(r['id']))} roles" + (f" · <#{r['channel_id']}>" if r["channel_id"] else " · not posted") for r in rows]
        await ui.paginate(ctx, ui.pages_from_lines("🎭 Role menus", lines))

    @mrolemenu.command(name="delete")
    async def rm_delete(self, ctx, menu_id: int):
        menu = await self._menu_or_err(ctx, menu_id)
        if not menu or not await ui.confirm(ctx, f"Delete role menu #{menu_id}? Roles members already have are not touched."):
            return
        self.db.execute("DELETE FROM role_menu_items WHERE menu_id=?", (menu_id,))
        self.db.execute("DELETE FROM role_menus WHERE id=?", (menu_id,))
        if menu["message_id"] and ctx.guild.get_channel(menu["channel_id"] or 0):
            try:
                msg = await ctx.guild.get_channel(menu["channel_id"]).fetch_message(menu["message_id"])
                await msg.edit(view=None)
            except discord.HTTPException:
                pass
        await ui.reply(ctx, embed=ui.ok("Menu deleted."))

    # ---------------------------------------------------------------- reaction roles
    @commands.group(name="mreactionrole", aliases=["mrr"], invoke_without_command=True)
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mreactionrole(self, ctx):
        """Reaction roles: add <message link> <emoji> @Role · remove <message link> <emoji> · list"""
        rows = self.db.fetchall("SELECT * FROM reaction_roles WHERE guild_id=?", (ctx.guild.id,))
        await ui.paginate(ctx, ui.pages_from_lines("😀 Reaction roles", [f"{r['emoji']} → <@&{r['role_id']}> on [message]({ui.jump(ctx.guild.id, r['channel_id'], r['message_id'])})" for r in rows]
                                                   or ["None. `!mreactionrole add <message link> <emoji> @Role`"]))

    @mreactionrole.command(name="add")
    async def rr_add(self, ctx, link: str, emoji: str, role: discord.Role):
        m = MESSAGE_LINK.search(link)
        if not m or int(m.group(1)) != ctx.guild.id:
            return await ui.reply(ctx, embed=ui.err("Give a message link from this server."))
        problem = menu_safe(role, ctx.guild.me)
        if problem:
            return await ui.reply(ctx, embed=ui.err(f"{role.mention} can't be self-assigned: {problem}"))
        channel = ctx.guild.get_channel_or_thread(int(m.group(2)))
        try:
            msg = await channel.fetch_message(int(m.group(3)))
            await msg.add_reaction(emoji)
        except (discord.HTTPException, AttributeError):
            return await ui.reply(ctx, embed=ui.err("I couldn't react to that message with that emoji (check the link, the emoji, and my Add Reactions permission)."))
        self.db.execute("INSERT OR REPLACE INTO reaction_roles(guild_id, channel_id, message_id, emoji, role_id) VALUES (?,?,?,?,?)",
                        (ctx.guild.id, channel.id, msg.id, str(discord.PartialEmoji.from_str(emoji)), role.id))
        await ui.reply(ctx, embed=ui.ok(f"Reacting with {emoji} now gives {role.mention}."))

    @mreactionrole.command(name="remove")
    async def rr_remove(self, ctx, link: str, emoji: str):
        m = MESSAGE_LINK.search(link)
        n = self.db.execute("DELETE FROM reaction_roles WHERE guild_id=? AND message_id=? AND emoji=?", (ctx.guild.id, int(m.group(3)) if m else 0, str(discord.PartialEmoji.from_str(emoji)))).rowcount
        await ui.reply(ctx, embed=ui.ok("Removed.") if n else ui.err("No such reaction role."))

    async def reaction_role(self, payload, add):
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        row = self.db.fetchone("SELECT role_id FROM reaction_roles WHERE message_id=? AND emoji=?", (payload.message_id, str(payload.emoji)))
        if not row:
            return
        guild = self.bot.get_guild(payload.guild_id)
        role = guild.get_role(row["role_id"]) if guild else None
        member = payload.member if add and payload.member else (guild.get_member(payload.user_id) if guild else None)
        if member is None and guild:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if role is None or member is None or member.bot or menu_safe(role, guild.me):
            return
        try:
            if add and role not in member.roles:
                await self.give(member, role, member.id, "reaction role", "Reaction role")
            elif not add and role in member.roles:
                await self.take(member, role, member.id, "reaction role", "Reaction role removed")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.reaction_role(payload, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.reaction_role(payload, False)

    # ---------------------------------------------------------------- temporary roles
    @commands.command(name="mtemprole")
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mtemprole(self, ctx, member: discord.Member, role: discord.Role, duration: str, *, reason: str = ""):
        """Give a role that is removed automatically: `!mtemprole @member @Role 2d [reason]`"""
        delta = parse_duration(duration, minimum=60, maximum=365 * 86400)
        if delta is None:
            return await ui.reply(ctx, embed=ui.err("Duration like `30m`, `12h`, `7d` (1 minute to 1 year)."))
        problem = checks.role_problem(role, ctx.guild.me, ctx.author)
        if problem:
            return await ui.reply(ctx, embed=ui.err(problem))
        expires = time.time() + delta.total_seconds()
        try:
            if role not in member.roles:
                await self.give(member, role, ctx.author.id, f"temporary role ({fmt_delta(delta.total_seconds())})", f"Temporary role by {ctx.author}: {reason}"[:500])
        except discord.HTTPException:
            return await ui.reply(ctx, embed=ui.err("Discord refused the role change."))
        self.db.execute("UPDATE temp_roles SET status='replaced' WHERE guild_id=? AND user_id=? AND role_id=? AND status='active'", (ctx.guild.id, member.id, role.id))
        self.db.insert("INSERT INTO temp_roles(guild_id, user_id, role_id, expires_ts, actor_id, reason) VALUES (?,?,?,?,?,?)",
                       (ctx.guild.id, member.id, role.id, expires, ctx.author.id, reason[:300]))
        await ui.reply(ctx, embed=ui.ok(f"{member.mention} has {role.mention} until <t:{int(expires)}:f> (<t:{int(expires)}:R>)."))

    @commands.command(name="mtemproles")
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mtemproles(self, ctx):
        """Active temporary roles."""
        rows = self.db.fetchall("SELECT * FROM temp_roles WHERE guild_id=? AND status='active' ORDER BY expires_ts", (ctx.guild.id,))
        await ui.paginate(ctx, ui.pages_from_lines("⏳ Temporary roles", [f"`#{r['id']}` <@{r['user_id']}> · <@&{r['role_id']}> · expires <t:{int(r['expires_ts'])}:R>" for r in rows]))

    @commands.command(name="mtemprolecancel")
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mtemprolecancel(self, ctx, temp_id: int, keep: str = "remove"):
        """Cancel a temporary role: `!mtemprolecancel <id> [keep|remove]` (keep = make it permanent)."""
        row = self.db.fetchone("SELECT * FROM temp_roles WHERE guild_id=? AND id=? AND status='active'", (ctx.guild.id, temp_id))
        if not row:
            return await ui.reply(ctx, embed=ui.err("No active temporary role with that ID."))
        self.db.execute("UPDATE temp_roles SET status='cancelled' WHERE id=?", (temp_id,))
        if keep.casefold() != "keep":
            member, role = ctx.guild.get_member(row["user_id"]), ctx.guild.get_role(row["role_id"])
            if member and role and role in member.roles:
                try:
                    await self.take(member, role, ctx.author.id, "temporary role cancelled", f"Temp role cancelled by {ctx.author}")
                except discord.HTTPException:
                    pass
        await ui.reply(ctx, embed=ui.ok("Done."))

    @tasks.loop(seconds=60)
    async def temp_loop(self):
        for row in self.db.fetchall("SELECT * FROM temp_roles WHERE status='active' AND expires_ts<=? LIMIT 50", (time.time(),)):
            if not self.db.claim("temp_roles", row["id"], expected="active", new="expired"):
                continue
            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                continue
            member = guild.get_member(row["user_id"])
            if member is None:
                try:
                    member = await guild.fetch_member(row["user_id"])
                except discord.HTTPException:
                    continue
            role = guild.get_role(row["role_id"])
            if role and role in member.roles and checks.role_problem(role, guild.me) is None:
                try:
                    await self.take(member, role, None, "temporary role expired", "Temporary role expired")
                except discord.HTTPException:
                    self.db.execute("UPDATE temp_roles SET status='active', expires_ts=? WHERE id=?", (time.time() + 600, row["id"]))

    @temp_loop.before_loop
    async def before_temp(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def cleanup_loop(self):
        if not self.bot.intents.members:
            return
        is_colour_role = self.bot.nova.is_colour_role
        for guild in self.bot.guilds:
            if not self.bot.registry.get(guild.id, "roles").get("cleanup_colour_roles"):
                continue
            for role in [r for r in guild.roles if is_colour_role(r) and not r.members and r < guild.me.top_role]:
                try:
                    await role.delete(reason="Unused NOVA colour role (automatic cleanup)")
                    await self.log(guild, f"🧹 Deleted unused colour role `{role.name}`")
                except discord.HTTPException:
                    pass
                await asyncio.sleep(1)

    @cleanup_loop.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.command(name="mrolecleanup")
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mrolecleanup(self, ctx):
        """Preview empty roles. Only NOVA's own unused colour roles can be deleted here (with confirmation)."""
        if not self.bot.intents.members:
            return await ui.reply(ctx, embed=ui.err("This needs Server Members Intent for accurate member counts."))
        empty = [r for r in ctx.guild.roles if not r.is_default() and not r.managed and not r.members]
        colour = [r for r in empty if self.bot.nova.is_colour_role(r) and r < ctx.guild.me.top_role]
        others = [r for r in empty if r not in colour]
        e = discord.Embed(title="🧹 Empty roles", colour=ui.WARN)
        e.add_field(name=f"NOVA colour roles with no members ({len(colour)})", value=ui.clip(", ".join(f"`{r.name}`" for r in colour) or "none", 1000), inline=False)
        e.add_field(name=f"Other empty roles ({len(others)}) — listed only, never deleted by NOVA", value=ui.clip(", ".join(r.mention for r in others) or "none", 1000), inline=False)
        await ui.reply(ctx, embed=e)
        if colour and await ui.confirm(ctx, f"Delete the {len(colour)} unused NOVA colour role(s)? Other roles are not touched."):
            done = 0
            for role in colour:
                try:
                    await role.delete(reason=f"Unused colour role cleanup by {ctx.author}")
                    done += 1
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.5)
            await ui.reply(ctx, embed=ui.ok(f"Deleted {done} colour role(s)."))

    # ---------------------------------------------------------------- templates
    @commands.group(name="mroletemplate", aliases=["mrt"], invoke_without_command=True)
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mroletemplate(self, ctx):
        """Role templates: list, show <name>, save <name> @roles..., apply <name>, delete <name>"""
        rows = self.db.fetchall("SELECT name, data FROM role_templates WHERE guild_id=?", (ctx.guild.id,))
        lines = [f"**{n}** (built-in) · {len(d)} roles" for n, d in BUILTIN_TEMPLATES.items()] + [f"**{r['name']}** · {len(json.loads(r['data']))} roles" for r in rows]
        await ui.reply(ctx, embed=ui.embed("🧩 Role templates", "\n".join(lines) + "\n\n`!mroletemplate save <name> @Role @Role…` · `!mroletemplate apply <name>` · `!mroletemplate show <name>`"))

    def template(self, guild_id, name):
        if name.casefold() in BUILTIN_TEMPLATES:
            return BUILTIN_TEMPLATES[name.casefold()]
        row = self.db.fetchone("SELECT data FROM role_templates WHERE guild_id=? AND lower(name)=lower(?)", (guild_id, name))
        return json.loads(row["data"]) if row else None

    @mroletemplate.command(name="show")
    async def rt_show(self, ctx, name: str):
        data = self.template(ctx.guild.id, name)
        if data is None:
            return await ui.reply(ctx, embed=ui.err("No such template."))
        await ui.reply(ctx, embed=ui.embed(f"🧩 {name}", "\n".join(f"• **{d['name']}** · #{d.get('colour', 0):06X}" + (" · hoisted" if d.get("hoist") else "") + (" · mentionable" if d.get("mentionable") else "") + (" · has permissions" if d.get("permissions") else "") for d in data)))

    @mroletemplate.command(name="save")
    async def rt_save(self, ctx, name: str, roles: commands.Greedy[discord.Role]):
        if not roles or name.casefold() in BUILTIN_TEMPLATES:
            return await ui.reply(ctx, embed=ui.err("Usage: `!mroletemplate save <name> @Role @Role…` (can't overwrite built-ins)."))
        data = [{"name": r.name, "colour": r.colour.value, "hoist": r.hoist, "mentionable": r.mentionable, "permissions": r.permissions.value} for r in roles[:50]]
        self.db.execute("DELETE FROM role_templates WHERE guild_id=? AND lower(name)=lower(?)", (ctx.guild.id, name))
        self.db.insert("INSERT INTO role_templates(guild_id, name, data, created_ts) VALUES (?,?,?,?)", (ctx.guild.id, name[:40], json.dumps(data), time.time()))
        await ui.reply(ctx, embed=ui.ok(f"Template **{name}** saved with {len(data)} roles."))

    @mroletemplate.command(name="apply")
    async def rt_apply(self, ctx, name: str):
        data = self.template(ctx.guild.id, name)
        if data is None:
            return await ui.reply(ctx, embed=ui.err("No such template."))
        existing = {r.name.casefold() for r in ctx.guild.roles}
        todo = [d for d in data if d["name"].casefold() not in existing]
        if not todo:
            return await ui.reply(ctx, embed=ui.ok("Every role in that template already exists."))
        if len(ctx.guild.roles) + len(todo) > 250:
            return await ui.reply(ctx, embed=ui.err("That would exceed Discord's 250-role limit."))
        mine = ctx.guild.me.guild_permissions.value
        actor = ctx.author.guild_permissions.value if ctx.author.id != ctx.guild.owner_id else mine
        stripped = any((d.get("permissions", 0) & ~(mine & actor)) or (d.get("permissions", 0) & 8) for d in todo)
        if not await ui.confirm(ctx, f"Create {len(todo)} role(s): {', '.join(d['name'] for d in todo)}?" +
                                ("\nPermissions you or I don't have (and Administrator) will be left off." if stripped else ""), confirm_label="Create", danger=False):
            return
        made = 0
        for d in todo:
            perms = discord.Permissions(d.get("permissions", 0) & mine & actor & ~8)
            try:
                await ctx.guild.create_role(name=d["name"], colour=discord.Colour(d.get("colour", 0)), hoist=d.get("hoist", False),
                                            mentionable=d.get("mentionable", False), permissions=perms, reason=f"Role template '{name}' by {ctx.author}")
                made += 1
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.5)
        await self.log(ctx.guild, f"🧩 {ctx.author.mention} applied template **{name}** ({made} roles created)")
        await ui.reply(ctx, embed=ui.ok(f"Created {made} role(s)."))

    @mroletemplate.command(name="delete")
    async def rt_delete(self, ctx, name: str):
        n = self.db.execute("DELETE FROM role_templates WHERE guild_id=? AND lower(name)=lower(?)", (ctx.guild.id, name)).rowcount
        await ui.reply(ctx, embed=ui.ok("Deleted.") if n else ui.err("No such custom template."))

    # ---------------------------------------------------------------- bulk
    @commands.command(name="mbulkrole")
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mbulkrole(self, ctx, action: str, role: discord.Role, *, target: str = "humans"):
        """Bulk add/remove a role with preview + confirmation.
        Targets: all · humans · bots · has:@Role · without:@Role · joined:7d"""
        action = action.casefold()
        if action not in ("add", "remove"):
            return await ui.reply(ctx, embed=ui.err("Use `!mbulkrole add|remove @Role [target]`."))
        if not self.bot.intents.members:
            return await ui.reply(ctx, embed=ui.err("Bulk role changes need Server Members Intent enabled for NOVA."))
        problem = checks.role_problem(role, ctx.guild.me, ctx.author)
        if problem:
            return await ui.reply(ctx, embed=ui.err(problem))
        bad = checks.dangerous_perms(role)
        if bad and action == "add" and not checks.is_admin(ctx.author):
            return await ui.reply(ctx, embed=ui.err(f"{role.mention} has staff permissions ({', '.join(bad[:3])}); only admins can bulk-assign it."))
        members = list(ctx.guild.members)
        t = target.strip().casefold()
        if t == "humans":
            members = [m for m in members if not m.bot]
        elif t == "bots":
            members = [m for m in members if m.bot]
        elif t.startswith(("has:", "without:")):
            rid = re.search(r"\d+", t)
            other = ctx.guild.get_role(int(rid.group())) if rid else None
            if other is None:
                return await ui.reply(ctx, embed=ui.err("Couldn't find the filter role."))
            members = [m for m in members if (other in m.roles) == t.startswith("has:")]
        elif t.startswith("joined:"):
            delta = parse_duration(t.split(":", 1)[1])
            if delta is None:
                return await ui.reply(ctx, embed=ui.err("Use e.g. `joined:7d`."))
            cutoff = discord.utils.utcnow() - delta
            members = [m for m in members if m.joined_at and m.joined_at >= cutoff]
        elif t != "all":
            return await ui.reply(ctx, embed=ui.err("Target must be all, humans, bots, has:@Role, without:@Role or joined:7d."))
        # Assigning a role only requires the ROLE to be below NOVA's top role; the member's own roles don't matter.
        todo = [m for m in members if (role not in m.roles) == (action == "add")]
        if not todo:
            return await ui.reply(ctx, embed=ui.ok("Nobody needs changing."))
        eta = fmt_delta(len(todo) * 0.6)
        if not await ui.confirm(ctx, f"{'Give' if action == 'add' else 'Remove'} {role.mention} {'to' if action == 'add' else 'from'} **{len(todo)}** members ({target})? Takes about {eta}.",
                                confirm_label=f"{action.title()} role", danger=action == "remove"):
            return
        status = await ctx.send(f"Working… 0/{len(todo)}")
        done = failed = 0
        for i, m in enumerate(todo, 1):
            try:
                if action == "add":
                    await m.add_roles(role, reason=f"Bulk role by {ctx.author}")
                else:
                    await m.remove_roles(role, reason=f"Bulk role by {ctx.author}")
                self.history(ctx.guild.id, m.id, role.id, action, ctx.author.id, "bulk")
                done += 1
            except discord.HTTPException:
                failed += 1
            if i % 20 == 0:
                try:
                    await status.edit(content=f"Working… {i}/{len(todo)}")
                except discord.HTTPException:
                    pass
            await asyncio.sleep(0.5)
        await status.edit(content=f"✅ {done} changed, {failed} failed.")
        await self.log(ctx.guild, f"📦 Bulk {action} {role.mention}: {done} members ({failed} failed) by {ctx.author.mention}")

    # ---------------------------------------------------------------- info & diagnostics (read-only)
    @commands.command(name="mroleinfo")
    @commands.guild_only()
    async def mroleinfo(self, ctx, role: discord.Role):
        """Details about a role."""
        e = discord.Embed(title=f"🎭 {role.name}", colour=role.colour if role.colour.value else ui.PINK)
        e.add_field(name="ID", value=f"`{role.id}`")
        e.add_field(name="Members", value=str(len(role.members)) if self.bot.intents.members else "needs Members Intent")
        e.add_field(name="Position", value=f"{role.position} of {len(ctx.guild.roles) - 1}")
        e.add_field(name="Colour", value=f"#{role.colour.value:06X}" + (" (gradient)" if getattr(role, "secondary_colour", None) else ""))
        e.add_field(name="Hoisted / mentionable", value=f"{'yes' if role.hoist else 'no'} / {'yes' if role.mentionable else 'no'}")
        e.add_field(name="Managed", value="yes (integration/bot)" if role.managed else "no")
        e.add_field(name="Created", value=discord.utils.format_dt(role.created_at, "D"))
        manageable = checks.role_problem(role, ctx.guild.me)
        e.add_field(name="Can NOVA assign it?", value="✅ yes" if manageable is None else f"❌ {manageable}")
        bad = checks.dangerous_perms(role)
        if bad:
            e.add_field(name="⚠️ Staff permissions", value=", ".join(p.replace("_", " ") for p in bad), inline=False)
        await ui.reply(ctx, embed=e)

    @commands.command(name="mroleperms")
    @commands.guild_only()
    async def mroleperms(self, ctx, role: discord.Role):
        """Every permission a role grants."""
        on = [p.replace("_", " ") for p, v in role.permissions if v]
        await ui.reply(ctx, embed=ui.embed(f"🔐 Permissions of {role.name}", ui.clip(", ".join(on) or "none", 4000)))

    @commands.command(name="mpermcompare")
    @commands.guild_only()
    async def mpermcompare(self, ctx, a: discord.Role, b: discord.Role):
        """Compare two roles' permissions."""
        only_a = [p for p, v in a.permissions if v and not getattr(b.permissions, p)]
        only_b = [p for p, v in b.permissions if v and not getattr(a.permissions, p)]
        both = [p for p, v in a.permissions if v and getattr(b.permissions, p)]
        e = discord.Embed(title=f"⚖️ {a.name} vs {b.name}", colour=ui.INFO)
        e.add_field(name=f"Only {a.name}", value=ui.clip(", ".join(only_a) or "—", 1000), inline=False)
        e.add_field(name=f"Only {b.name}", value=ui.clip(", ".join(only_b) or "—", 1000), inline=False)
        e.add_field(name="Both", value=ui.clip(", ".join(both) or "—", 1000), inline=False)
        e.add_field(name="Higher in hierarchy", value=a.mention if a > b else b.mention)
        await ui.reply(ctx, embed=e)

    @commands.command(name="mrolehierarchy", aliases=["mhierarchy"])
    @commands.guild_only()
    async def mrolehierarchy(self, ctx):
        """Role order with what NOVA can manage."""
        me = ctx.guild.me
        lines = []
        for r in sorted(ctx.guild.roles, key=lambda r: -r.position):
            if r.is_default():
                continue
            mark = "🤖 **NOVA's top role**" if r == me.top_role else ("✅" if r < me.top_role and not r.managed else "⛔")
            lines.append(f"`{r.position:>3}` {mark} {r.mention}" + (" ⚠️staff" if checks.dangerous_perms(r) else ""))
        await ui.paginate(ctx, ui.pages_from_lines("🪜 Role hierarchy", lines, per_page=20, footer="✅ NOVA can assign · ⛔ above her or managed"))

    @commands.command(name="mroleeligible", aliases=["mcanget"])
    @commands.guild_only()
    async def mroleeligible(self, ctx, member: discord.Member, role: discord.Role):
        """Explain whether a member can get a role (via NOVA's menus / staff tools)."""
        reasons = []
        problem = checks.role_problem(role, ctx.guild.me)
        if problem:
            reasons.append(f"NOVA can't assign it: {problem}")
        bad = checks.dangerous_perms(role)
        if bad:
            reasons.append(f"It has staff permissions ({', '.join(bad[:3])}), so it's never available in self-service menus")
        menus = self.db.fetchall("SELECT m.* FROM role_menus m JOIN role_menu_items i ON i.menu_id=m.id WHERE m.guild_id=? AND i.role_id=?", (ctx.guild.id, role.id))
        if menus:
            for m in menus:
                req = f" (needs <@&{m['required_role']}>: {'✅' if any(r.id == m['required_role'] for r in member.roles) else '❌'})" if m["required_role"] else ""
                reasons.append(f"Offered in menu #{m['id']} **{m['title']}**{req}")
        else:
            reasons.append("Not offered in any NOVA role menu")
        reasons.append(f"{member.display_name} {'already has' if role in member.roles else 'does not have'} it")
        await ui.reply(ctx, embed=ui.embed(f"🔎 {member.display_name} → {role.name}", "\n".join(f"• {r}" for r in reasons)))

    @commands.command(name="mrolehistory")
    @commands.guild_only()
    @checks.perm_only("manage_roles", "Manage Roles")
    async def mrolehistory(self, ctx, target: str):
        """Role change history for a member or a role."""
        member = role = None
        try:
            member = await commands.MemberConverter().convert(ctx, target)
        except commands.BadArgument:
            try:
                role = await commands.RoleConverter().convert(ctx, target)
            except commands.BadArgument:
                return await ui.reply(ctx, embed=ui.err("Mention a member or a role."))
        if member:
            rows = self.db.fetchall("SELECT * FROM role_history WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 100", (ctx.guild.id, member.id))
            lines = [f"{'➕' if r['action'] == 'add' else '➖'} <@&{r['role_id']}> · {r['source'] or ''} · <t:{int(r['ts'])}:R>" for r in rows]
            title = f"📜 Role history · {member.display_name}"
        else:
            rows = self.db.fetchall("SELECT * FROM role_history WHERE guild_id=? AND role_id=? ORDER BY id DESC LIMIT 100", (ctx.guild.id, role.id))
            lines = [f"{'➕' if r['action'] == 'add' else '➖'} <@{r['user_id']}> · {r['source'] or ''} · <t:{int(r['ts'])}:R>" for r in rows]
            title = f"📜 Role history · {role.name}"
        await ui.paginate(ctx, ui.pages_from_lines(title, lines, footer="Changes made outside NOVA are recorded as 'external' (needs Members Intent)"))

    @commands.command(name="mbotroles", aliases=["mpermcheck"])
    @commands.guild_only()
    async def mbotroles(self, ctx, channel: discord.abc.GuildChannel = None):
        """NOVA's highest role, permissions and channel-level conflicts (read-only)."""
        me = ctx.guild.me
        need = ("manage_roles", "manage_channels", "manage_messages", "view_audit_log", "connect", "speak", "move_members", "embed_links", "read_message_history")
        e = discord.Embed(title="🤖 NOVA's permissions", colour=ui.INFO)
        e.add_field(name="Highest role", value=f"{me.top_role.mention} (position {me.top_role.position})")
        e.add_field(name="Server permissions", value="\n".join(("✅ " if getattr(me.guild_permissions, p) else "❌ ") + p.replace("_", " ") for p in need), inline=False)
        if channel is not None:
            perms = channel.permissions_for(me)
            denied = [p for p in need if getattr(me.guild_permissions, p) and not getattr(perms, p)]
            e.add_field(name=f"Overridden in #{channel.name}", value=", ".join(p.replace("_", " ") for p in denied) or "no conflicts", inline=False)
        await ui.reply(ctx, embed=e)

    @commands.command(name="mchannelperms")
    @commands.guild_only()
    async def mchannelperms(self, ctx, channel: discord.abc.GuildChannel, *, target: str = None):
        """Effective permissions for a member or role in a channel (read-only)."""
        subject = ctx.author
        if target:
            for conv in (commands.MemberConverter(), commands.RoleConverter()):
                try:
                    subject = await conv.convert(ctx, target)
                    break
                except commands.BadArgument:
                    continue
        if isinstance(subject, discord.Role):
            ow = channel.overwrites_for(subject)
            allow = [p for p, v in ow if v is True]
            deny = [p for p, v in ow if v is False]
            e = ui.embed(f"🔐 {subject.name} in #{channel.name}", f"**Overwrite allows:** {', '.join(allow) or '—'}\n**Overwrite denies:** {', '.join(deny) or '—'}\n**Role base:** {', '.join(p for p, v in subject.permissions if v)[:1500] or '—'}")
        else:
            perms = channel.permissions_for(subject)
            e = ui.embed(f"🔐 {subject.display_name} in #{channel.name}", ui.clip(", ".join(p.replace("_", " ") for p, v in perms if v) or "nothing", 4000))
        await ui.reply(ctx, embed=e)


async def setup(bot):
    await bot.add_cog(Roles(bot))
