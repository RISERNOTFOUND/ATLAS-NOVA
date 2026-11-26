"""Per-server module settings with a Discord-native configuration panel.

Each feature module registers a schema (its options and defaults). `!config` / `/config` then renders
a select menu of modules; each module page shows current values, an enable/disable toggle and an option
picker. Channel/role options use Discord's native channel/role selects; text/number/time options use modals.
Every callback re-checks that the user is a server admin, so a forwarded or replayed interaction can't
change anything for an ordinary member.
"""
from dataclasses import dataclass, field

import discord

from . import checks, ui
from .timeparse import get_tz, parse_hhmm, tz_name

TEXT_KINDS = {"int", "text", "time", "tz", "emoji"}


@dataclass
class Option:
    key: str
    label: str
    kind: str                 # bool | channel | voice_channel | category | role | roles | channels | int | text | time | tz | emoji | choice
    default: object = None
    help: str = ""
    min: int = 0
    max: int = 1_000_000
    choices: tuple = ()
    max_length: int = 300


@dataclass
class Module:
    name: str
    label: str
    emoji: str
    description: str
    options: list = field(default_factory=list)
    default_enabled: bool = False


class Registry:
    def __init__(self, bot, db):
        self.bot, self.db = bot, db
        self.modules = {}

    def register(self, module):
        self.modules[module.name] = module
        return module

    def get(self, guild_id, name):
        module = self.modules[name]
        stored = self.db.get_settings(guild_id, name)
        values = {"enabled": stored.get("enabled", module.default_enabled)}
        for opt in module.options:
            values[opt.key] = stored.get(opt.key, opt.default)
        return values

    def enabled(self, guild_id, name):
        if guild_id is None or name not in self.modules:
            return False
        return bool(self.get(guild_id, name)["enabled"])

    def set(self, guild, user, name, key, value):
        old = self.db.set_setting(guild.id, name, key, value)
        try:
            self.bot.dispatch("bot_config_change", guild, user, name, key, old, value)
        except Exception:
            pass
        return old

    def channel(self, guild, name, key):
        value = self.get(guild.id, name).get(key)
        return guild.get_channel(int(value)) if value else None

    def role(self, guild, name, key):
        value = self.get(guild.id, name).get(key)
        return guild.get_role(int(value)) if value else None

    def id_list(self, guild_id, name, key):
        return [int(v) for v in (self.get(guild_id, name).get(key) or [])]


def render_value(opt, value):
    if opt.kind == "bool":
        return "✅ on" if value else "❌ off"
    if value in (None, "", []):
        return "*not set*"
    if opt.kind in ("channel", "voice_channel", "category"):
        return f"<#{value}>"
    if opt.kind == "role":
        return f"<@&{value}>"
    if opt.kind == "roles":
        return ", ".join(f"<@&{v}>" for v in value)[:900]
    if opt.kind == "channels":
        return ", ".join(f"<#{v}>" for v in value)[:900]
    return f"`{ui.clip(value, 200)}`"


def overview_embed(registry, guild):
    e = discord.Embed(title="⚙️ Configuration", colour=ui.PINK,
                      description="Pick a module below to see and change its settings. Every module can be switched on or off on its own; switching one off never touches the others or deletes data.")
    lines = []
    for m in registry.modules.values():
        lines.append(f"{m.emoji} **{m.label}** — {'🟢 on' if registry.enabled(guild.id, m.name) else '⚫ off'}")
    for i, part in enumerate(ui.chunk_lines(lines, 1000)):
        e.add_field(name="Modules" if i == 0 else "​", value=part, inline=False)
    return e


def module_embed(registry, guild, name):
    m = registry.modules[name]
    values = registry.get(guild.id, name)
    e = discord.Embed(title=f"{m.emoji} {m.label}", description=m.description, colour=ui.GOOD if values["enabled"] else ui.WARN)
    e.add_field(name="Status", value="🟢 Enabled" if values["enabled"] else "⚫ Disabled", inline=False)
    for opt in m.options[:23]:
        e.add_field(name=opt.label, value=render_value(opt, values.get(opt.key)) + (f"\n-# {opt.help}" if opt.help else ""), inline=True)
    e.set_footer(text="Changes save immediately and persist across restarts.")
    return e


class _Base(discord.ui.View):
    def __init__(self, registry, author_id, guild, timeout=300):
        super().__init__(timeout=timeout)
        self.registry, self.author_id, self.guild = registry, author_id, guild
        self.target = None  # Message or Interaction to edit on refresh

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Run the config command yourself to change settings.", ephemeral=True)
            return False
        if not checks.is_admin(interaction.user):
            await interaction.response.send_message("You need **Manage Server** to change settings.", ephemeral=True)
            return False
        return True


class OverviewView(_Base):
    def __init__(self, registry, author_id, guild):
        super().__init__(registry, author_id, guild)
        options = [discord.SelectOption(label=m.label[:100], value=m.name, emoji=m.emoji,
                                        description=("on" if registry.enabled(guild.id, m.name) else "off") + " · " + ui.clip(m.description, 80))
                   for m in list(registry.modules.values())[:25]]
        select = discord.ui.Select(placeholder="Choose a module…", options=options)
        select.callback = self._pick
        self.select = select
        self.add_item(select)

    async def _pick(self, interaction):
        view = ModuleView(self.registry, self.author_id, self.guild, self.select.values[0])
        await interaction.response.edit_message(embed=module_embed(self.registry, self.guild, view.name), view=view)
        view.target = interaction


class ModuleView(_Base):
    def __init__(self, registry, author_id, guild, name):
        super().__init__(registry, author_id, guild)
        self.name = name
        module = registry.modules[name]
        enabled = registry.enabled(guild.id, name)
        toggle = discord.ui.Button(label="Disable module" if enabled else "Enable module",
                                   style=discord.ButtonStyle.secondary if enabled else discord.ButtonStyle.success, row=0)
        toggle.callback = self._toggle
        self.add_item(toggle)
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=0)
        back.callback = self._back
        self.add_item(back)
        if module.options:
            select = discord.ui.Select(placeholder="Change a setting…", row=1, options=[
                discord.SelectOption(label=o.label[:100], value=o.key, description=ui.clip(o.help or o.kind, 100)) for o in module.options[:25]])
            select.callback = self._pick_option
            self.option_select = select
            self.add_item(select)

    async def refresh(self, interaction=None):
        view = ModuleView(self.registry, self.author_id, self.guild, self.name)
        e = module_embed(self.registry, self.guild, self.name)
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=e, view=view)
            view.target = interaction
            return
        target = self.target
        view.target = target
        try:
            if isinstance(target, discord.Interaction):
                await target.edit_original_response(embed=e, view=view)
            elif isinstance(target, discord.Message):
                await target.edit(embed=e, view=view)
        except discord.HTTPException:
            pass

    async def _toggle(self, interaction):
        current = self.registry.enabled(self.guild.id, self.name)
        self.registry.set(self.guild, interaction.user, self.name, "enabled", not current)
        await self.refresh(interaction)

    async def _back(self, interaction):
        view = OverviewView(self.registry, self.author_id, self.guild)
        await interaction.response.edit_message(embed=overview_embed(self.registry, self.guild), view=view)
        view.target = interaction

    async def _pick_option(self, interaction):
        key = self.option_select.values[0]
        opt = next(o for o in self.registry.modules[self.name].options if o.key == key)
        if opt.kind == "bool":
            current = self.registry.get(self.guild.id, self.name).get(key)
            self.registry.set(self.guild, interaction.user, self.name, key, not current)
            return await self.refresh(interaction)
        if opt.kind in TEXT_KINDS:
            return await interaction.response.send_modal(ValueModal(self, opt))
        picker = PickerView(self, opt)
        await interaction.response.send_message(f"Choose a value for **{opt.label}**:", view=picker, ephemeral=True)


class ValueModal(discord.ui.Modal):
    def __init__(self, parent, opt):
        super().__init__(title=ui.clip(f"Set: {opt.label}", 45))
        self.parent, self.opt = parent, opt
        current = parent.registry.get(parent.guild.id, parent.name).get(opt.key)
        placeholder = {"int": f"{opt.min}–{opt.max}", "time": "HH:MM (24h)", "tz": "e.g. Asia/Kolkata or +05:30"}.get(opt.kind, opt.help[:100] or None)
        self.value = discord.ui.TextInput(label=ui.clip(opt.label, 45), default=None if current is None else str(current)[:opt.max_length],
                                          placeholder=placeholder, required=False,
                                          style=discord.TextStyle.paragraph if opt.max_length > 200 else discord.TextStyle.short,
                                          max_length=min(4000, opt.max_length))
        self.add_item(self.value)

    async def on_submit(self, interaction):
        if not checks.is_admin(interaction.user):
            return await interaction.response.send_message("You need **Manage Server** to change settings.", ephemeral=True)
        raw = str(self.value.value or "").strip()
        opt = self.opt
        try:
            if raw == "":
                value = opt.default if opt.kind != "text" else ""
            elif opt.kind == "int":
                value = int(raw)
                if not opt.min <= value <= opt.max:
                    raise ValueError(f"Must be between {opt.min} and {opt.max}.")
            elif opt.kind == "time":
                h, m = parse_hhmm(raw)
                value = f"{h:02d}:{m:02d}"
            elif opt.kind == "tz":
                value = tz_name(get_tz(raw))
            else:
                value = raw[:opt.max_length]
        except ValueError as exc:
            return await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
        self.parent.registry.set(self.parent.guild, interaction.user, self.parent.name, opt.key, value)
        await interaction.response.send_message(f"Saved **{opt.label}**.", ephemeral=True)
        await self.parent.refresh()


class PickerView(discord.ui.View):
    def __init__(self, parent, opt):
        super().__init__(timeout=120)
        self.parent, self.opt = parent, opt
        if opt.kind in ("channel", "channels", "voice_channel", "category"):
            types = {"voice_channel": [discord.ChannelType.voice, discord.ChannelType.stage_voice],
                     "category": [discord.ChannelType.category]}.get(opt.kind, [discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.forum])
            select = discord.ui.ChannelSelect(channel_types=types, min_values=1, max_values=25 if opt.kind == "channels" else 1)
        elif opt.kind in ("role", "roles"):
            select = discord.ui.RoleSelect(min_values=1, max_values=25 if opt.kind == "roles" else 1)
        else:  # choice
            select = discord.ui.Select(options=[discord.SelectOption(label=str(c)[:100], value=str(c)) for c in opt.choices[:25]])
        select.callback = self._chosen
        self.select = select
        self.add_item(select)
        clear = discord.ui.Button(label="Clear", style=discord.ButtonStyle.secondary)
        clear.callback = self._clear
        self.add_item(clear)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.parent.author_id or not checks.is_admin(interaction.user):
            await interaction.response.send_message("You need **Manage Server** to change settings.", ephemeral=True)
            return False
        return True

    async def _save(self, interaction, value, text):
        self.parent.registry.set(self.parent.guild, interaction.user, self.parent.name, self.opt.key, value)
        await interaction.response.edit_message(content=text, view=None)
        await self.parent.refresh()

    async def _chosen(self, interaction):
        vals = self.select.values
        if self.opt.kind in ("roles", "channels"):
            value = [v.id for v in vals]
        elif self.opt.kind == "choice":
            value = vals[0]
        else:
            value = vals[0].id
        if self.opt.kind in ("role", "roles"):
            me = self.parent.guild.me
            bad = [r for r in vals if isinstance(r, discord.Role) and (r.is_default() or r.managed)]
            if bad:
                return await interaction.response.send_message("@everyone and integration-managed roles can't be used here.", ephemeral=True)
            if me is not None and self.opt.key.endswith(("assign_role", "verified_role", "role_to_give")) and any(r >= me.top_role for r in vals):
                return await interaction.response.send_message("That role is above my highest role, so I couldn't assign it. Move my role higher first.", ephemeral=True)
        await self._save(interaction, value, f"Saved **{self.opt.label}**.")

    async def _clear(self, interaction):
        await self._save(interaction, [] if self.opt.kind in ("roles", "channels") else None, f"Cleared **{self.opt.label}**.")


async def open_panel(ctx_or_inter, registry, module=None):
    guild = ctx_or_inter.guild
    user = ctx_or_inter.user if isinstance(ctx_or_inter, discord.Interaction) else ctx_or_inter.author
    if guild is None:
        return await ui.reply(ctx_or_inter, embed=ui.err("Configuration only works inside a server."))
    if not checks.is_admin(user):
        return await ui.reply(ctx_or_inter, embed=ui.err("You need **Manage Server** to open the configuration panel."))
    if module:
        name = next((m.name for m in registry.modules.values() if module.casefold() in (m.name.casefold(), m.label.casefold())), None)
        if name is None:
            return await ui.reply(ctx_or_inter, embed=ui.err("Unknown module. Modules: " + ", ".join(f"`{m}`" for m in registry.modules)))
        view = ModuleView(registry, user.id, guild, name)
        msg = await ui.reply(ctx_or_inter, embed=module_embed(registry, guild, name), view=view)
    else:
        view = OverviewView(registry, user.id, guild)
        msg = await ui.reply(ctx_or_inter, embed=overview_embed(registry, guild), view=view)
    view.target = ctx_or_inter if isinstance(ctx_or_inter, discord.Interaction) else msg
    return msg
