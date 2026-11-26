"""Diagnostics, preferences and configuration export/import shared by both bots.

Nothing here ever shows tokens, environment variables or file paths outside the project, and there is
no shell/eval command of any kind.
"""
import asyncio
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands

from . import checks, ui
from .errors import recent
from .timeparse import get_tz, tz_name
from .voice import library_report

EXPORT_VERSION = 1
NEEDED_GUILD_PERMS = ("view_channel", "send_messages", "embed_links", "read_message_history", "manage_roles", "manage_channels",
                      "manage_messages", "moderate_members", "connect", "speak", "attach_files", "add_reactions", "view_audit_log")


def memory_mb():
    try:
        import psutil  # optional
        return round(psutil.Process().memory_info().rss / 1_048_576, 1)
    except Exception:
        pass
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(usage / 1024, 1) if sys.platform != "darwin" else round(usage / 1_048_576, 1)
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters = PMC()
            counters.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
            return round(counters.WorkingSetSize / 1_048_576, 1)
        except Exception:
            pass
    return None


def cpu_seconds():
    try:
        t = os.times()
        return round(t.user + t.system, 1)
    except Exception:
        return None


def running_tasks(bot):
    names = []
    for cog in bot.cogs.values():
        for attr in dir(cog):
            loop = getattr(cog, attr, None)
            if hasattr(loop, "is_running") and hasattr(loop, "start"):
                try:
                    names.append(f"{type(cog).__name__}.{attr}: {'running' if loop.is_running() else 'STOPPED'}")
                except Exception:
                    pass
    return names


def bot_embed(bot, extra_lines=()):
    up = datetime.now(timezone.utc) - bot.started_at
    e = discord.Embed(title=f"🩺 {bot.bot_name} diagnostics", colour=ui.INFO, timestamp=discord.utils.utcnow())
    mem = memory_mb()
    e.add_field(name="Uptime", value=str(up).split(".")[0], inline=True)
    e.add_field(name="Latency", value=f"{round(bot.latency * 1000)} ms" if bot.latency == bot.latency else "n/a", inline=True)
    e.add_field(name="Memory", value=f"{mem} MB" if mem else "unavailable", inline=True)
    e.add_field(name="CPU time", value=f"{cpu_seconds()} s" if cpu_seconds() is not None else "unavailable", inline=True)
    e.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    e.add_field(name="Asyncio tasks", value=str(len(asyncio.all_tasks())), inline=True)
    try:
        integrity = bot.db.integrity()
        counts = bot.db.table_counts()
        e.add_field(name="Database", value=f"integrity: **{integrity}** · {len(counts)} tables · {sum(counts.values()):,} rows · backups: {len(bot.db.list_backups())}", inline=False)
    except Exception as exc:
        e.add_field(name="Database", value=f"⚠️ {type(exc).__name__}", inline=False)
    prefix_cmds = len({c.qualified_name for c in bot.walk_commands()})
    slash_cmds = len(bot.tree.get_commands()) + sum(len(bot.tree.get_commands(guild=g)) for g in bot.guilds[:1])
    e.add_field(name="Commands", value=f"{prefix_cmds} prefix · {slash_cmds} slash/context entries", inline=True)
    e.add_field(name="Loaded modules", value=ui.clip(", ".join(sorted(bot.cogs)) or "none", 1000), inline=False)
    failed = getattr(bot, "failed_extensions", {})
    if failed:
        e.add_field(name="⚠️ Failed to load", value="\n".join(f"`{n}` (error {i})" for n, i in failed.items())[:1000], inline=False)
    tasks_ = running_tasks(bot)
    stopped = [t for t in tasks_ if t.endswith("STOPPED")]
    e.add_field(name="Background tasks", value=f"{len(tasks_) - len(stopped)} running" + (f", stopped: {', '.join(t.split(':')[0] for t in stopped)[:600]}" if stopped else ""), inline=False)
    intents = bot.intents
    missing = [n for n, on in (("Server Members", intents.members), ("Message Content", intents.message_content), ("Presence", intents.presences)) if not on]
    e.add_field(name="Intents", value="all enabled" if not missing else "⚠️ off: " + ", ".join(missing), inline=True)
    errs = recent(limit=50, since_seconds=86400)
    e.add_field(name="Errors (24h)", value=str(len(errs)), inline=True)
    libs = library_report()
    e.add_field(name="Voice libraries", value=" · ".join(f"{k}: {v}" for k, v in libs.items())[:1000], inline=False)
    for line in extra_lines:
        e.add_field(name=line[0], value=line[1][:1000], inline=False)
    return e


def perms_embed(guild, me):
    perms = me.guild_permissions
    lines = [("✅ " if getattr(perms, p) else "❌ ") + p.replace("_", " ").title() for p in NEEDED_GUILD_PERMS]
    e = discord.Embed(title="🔐 Permission check", colour=ui.INFO, description="Server-level permissions I have (channel overrides can still deny them):")
    e.add_field(name="Permissions", value="\n".join(lines), inline=True)
    e.add_field(name="My highest role", value=f"{me.top_role.mention} (position {me.top_role.position} of {len(guild.roles) - 1})", inline=False)
    unreadable = [c for c in guild.text_channels if not c.permissions_for(me).view_channel]
    if unreadable:
        e.add_field(name=f"Text channels I can't see ({len(unreadable)})", value=ui.clip(", ".join(c.mention for c in unreadable), 1000), inline=False)
    return e


def errors_embed(module=None):
    rows = recent(limit=15, module=module)
    e = discord.Embed(title="🧾 Recent errors", colour=ui.WARN)
    if not rows:
        e.description = "No errors recorded since the last restart. 🎉"
    for ts, eid, mod, summary in rows:
        e.add_field(name=f"`{eid}` · {mod} · <t:{int(ts)}:R>"[:256], value=ui.clip(summary, 300), inline=False)
    e.set_footer(text="Full tracebacks are in the logs/ folder on the bot computer.")
    return e


def validate_config(bot, guild):
    """Check configured channels/roles still exist and the bot can use them."""
    problems = []
    me = guild.me
    for name, module in bot.registry.modules.items():
        values = bot.registry.get(guild.id, name)
        if not values.get("enabled"):
            continue
        for opt in module.options:
            value = values.get(opt.key)
            if not value:
                continue
            ids = value if isinstance(value, list) else [value]
            for raw in ids:
                if opt.kind in ("channel", "channels", "voice_channel", "category"):
                    ch = guild.get_channel(int(raw))
                    if ch is None:
                        problems.append(f"{module.label} → {opt.label}: channel `{raw}` no longer exists")
                    elif opt.kind in ("channel", "channels") and hasattr(ch, "permissions_for"):
                        miss = checks.missing_channel_perms(ch, me, "view_channel", "send_messages")
                        if miss:
                            problems.append(f"{module.label} → {opt.label}: missing {', '.join(miss)} in {ch.mention}")
                elif opt.kind in ("role", "roles"):
                    role = guild.get_role(int(raw))
                    if role is None:
                        problems.append(f"{module.label} → {opt.label}: role `{raw}` no longer exists")
                    elif role >= me.top_role and opt.key.endswith(("role", "roles")) and "staff" not in opt.key and "exempt" not in opt.key and "ping" not in opt.key:
                        problems.append(f"{module.label} → {opt.label}: {role.mention} is above my highest role (I can't assign it)")
    return problems


# ---------------------------------------------------------------- export / import
def export_payload(bot, guild, extra_tables=()):
    data = {"format": f"{bot.bot_name.lower()}-config", "version": EXPORT_VERSION, "guild_id": guild.id,
            "exported_at": datetime.now(timezone.utc).isoformat(), "settings": bot.db.all_settings(guild.id), "tables": {}}
    for table in extra_tables:
        try:
            rows = bot.db.fetchall(f"SELECT * FROM {table} WHERE guild_id=?", (guild.id,))
            data["tables"][table] = [dict(r) for r in rows]
        except Exception:
            continue
    return data


def validate_import(bot, payload, extra_tables=()):
    legacy = {"atlas": "atlas", "nova": "nova"}.get(bot.bot_name.lower(), "")   # exports made before the ATLAS/NOVA rebrand
    accepted = {f"{bot.bot_name.lower()}-config", f"{legacy}-config"}
    if not isinstance(payload, dict) or payload.get("format") not in accepted:
        raise ValueError(f"That file isn't a {bot.bot_name} configuration export.")
    if payload.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported export version {payload.get('version')} (expected {EXPORT_VERSION}).")
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("The export has no settings section.")
    unknown = [m for m in settings if m not in bot.registry.modules]
    tables = {t: rows for t, rows in (payload.get("tables") or {}).items() if t in extra_tables and isinstance(rows, list)}
    return settings, tables, unknown


def apply_import(bot, guild, settings, tables):
    bot.db.backup("pre-import")
    applied = 0
    for module, data in settings.items():
        if module in bot.registry.modules and isinstance(data, dict):
            bot.db.set_settings(guild.id, module, data)
            applied += 1
    restored = {}
    with bot.db.transaction() as conn:
        for table, rows in tables.items():
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            conn.execute(f"DELETE FROM {table} WHERE guild_id=?", (guild.id,))
            count = 0
            for row in rows:
                row = {k: v for k, v in row.items() if k in cols and k != "id"}
                row["guild_id"] = guild.id
                keys = list(row)
                conn.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys])
                count += 1
            restored[table] = count
    return applied, restored


def ensure_core_tables(db):
    db.migrate("core_001", """
        CREATE TABLE IF NOT EXISTS user_prefs (user_id INTEGER PRIMARY KEY, timezone TEXT, dm_reminders INTEGER DEFAULT 1,
            announce_milestones INTEGER DEFAULT 1, announce_anniversary INTEGER DEFAULT 1, starboard_optout INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS config_backups (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, created_by INTEGER, label TEXT, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_config_backups_guild ON config_backups(guild_id)""")


def user_prefs(db, user_id):
    row = db.fetchone("SELECT * FROM user_prefs WHERE user_id=?", (user_id,))
    return dict(row) if row else {"user_id": user_id, "timezone": None, "dm_reminders": 1, "announce_milestones": 1,
                                  "announce_anniversary": 1, "starboard_optout": 0}


def set_pref(db, user_id, column, value):
    assert column in ("timezone", "dm_reminders", "announce_milestones", "announce_anniversary", "starboard_optout")
    db.execute(f"INSERT INTO user_prefs(user_id, {column}) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET {column}=excluded.{column}",
               (user_id, value))


def user_tz(db, user_id, fallback="Asia/Kolkata"):
    name = user_prefs(db, user_id).get("timezone") or fallback
    try:
        return get_tz(name)
    except ValueError:
        return get_tz(fallback)


async def send_export(ctx, bot, guild, tables, label="manual"):
    payload = export_payload(bot, guild, tables)
    text = json.dumps(payload, indent=2, default=str)
    bot.db.insert("INSERT INTO config_backups(guild_id, created_at, created_by, label, payload) VALUES (?,?,?,?,?)",
                  (guild.id, payload["exported_at"], ctx.author.id, label, text))
    keep = bot.db.fetchall("SELECT id FROM config_backups WHERE guild_id=? ORDER BY id DESC LIMIT -1 OFFSET 25", (guild.id,))
    for row in keep:
        bot.db.execute("DELETE FROM config_backups WHERE id=?", (row["id"],))
    file = discord.File(io.BytesIO(text.encode()), filename=f"{bot.bot_name.lower()}-config-{guild.id}-{int(time.time())}.json")
    return file, payload


class PrefsView(discord.ui.View):
    TOGGLES = (("dm_reminders", "Reminders by DM"), ("announce_milestones", "Milestone shout-outs"),
               ("announce_anniversary", "Join-anniversary shout-outs"), ("starboard_optout", "Keep my messages off the starboard"))

    def __init__(self, db, user_id):
        super().__init__(timeout=180)
        self.db, self.user_id = db, user_id
        self._build()

    def _build(self):
        self.clear_items()
        prefs = user_prefs(self.db, self.user_id)
        for column, label in self.TOGGLES:
            on = bool(prefs.get(column))
            button = discord.ui.Button(label=f"{label}: {'on' if on else 'off'}", style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary)
            button.callback = self._make(column)
            self.add_item(button)

    def _make(self, column):
        async def callback(interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("These are someone else's preferences.", ephemeral=True)
            set_pref(self.db, self.user_id, column, 0 if user_prefs(self.db, self.user_id).get(column) else 1)
            self._build()
            await interaction.response.edit_message(embed=prefs_embed(self.db, self.user_id), view=self)
        return callback


def prefs_embed(db, user_id):
    prefs = user_prefs(db, user_id)
    e = discord.Embed(title="🛠️ Your preferences", colour=ui.PINK,
                      description="These apply to you in every server where this bot runs. Press a button to toggle.")
    e.add_field(name="Timezone", value=prefs.get("timezone") or "not set (Asia/Kolkata used)", inline=False)
    return e


def make_core_cog(bot, p="", export_tables=()):
    """Build the Core cog. `p` prefixes prefix-command names ("m" for NOVA so `!mconfig` never clashes with ATLAS's `!config`)."""
    from discord import app_commands
    from .config import open_panel

    ensure_core_tables(bot.db)

    async def do_diag(target, section):
        user = target.user if isinstance(target, discord.Interaction) else target.author
        guild = target.guild
        if guild is None or not checks.is_admin(user):
            return await ui.reply(target, embed=ui.err("Diagnostics are for server admins (Manage Server)."))
        section = (section or "").casefold()
        if section.startswith("err"):
            return await ui.reply(target, embed=errors_embed())
        if section.startswith("perm"):
            return await ui.reply(target, embed=perms_embed(guild, guild.me))
        if section.startswith("conf"):
            problems = validate_config(bot, guild)
            e = discord.Embed(title="🧩 Configuration check", colour=ui.GOOD if not problems else ui.WARN,
                              description="\n".join(f"• {x}" for x in problems)[:4000] or "Every configured channel and role exists and is usable. ✅")
            return await ui.reply(target, embed=e)
        extra = getattr(bot, "diag_extra", None)
        lines = extra(guild) if callable(extra) else []
        return await ui.reply(target, embed=bot_embed(bot, lines))

    async def do_timezone(target, zone):
        user = target.user if isinstance(target, discord.Interaction) else target.author
        if not zone:
            prefs = user_prefs(bot.db, user.id)
            return await ui.reply(target, embed=ui.embed(description=f"Your timezone: **{prefs.get('timezone') or 'not set (Asia/Kolkata used)'}**. Set it with `{p}timezone Europe/London` or `+05:30`."))
        try:
            tz = get_tz(zone)
        except ValueError as exc:
            return await ui.reply(target, embed=ui.err(str(exc)))
        set_pref(bot.db, user.id, "timezone", tz_name(tz))
        now = datetime.now(tz).strftime("%H:%M")
        await ui.reply(target, embed=ui.ok(f"Timezone saved as **{tz_name(tz)}** (your local time now: {now})."))

    class Core(commands.Cog, name="Core"):
        def __init__(self):
            self.bot = bot

        async def cog_load(self):
            bot.tree.on_error = self.on_app_error

        async def on_app_error(self, interaction, error):
            original = getattr(error, "original", error)
            if isinstance(error, (app_commands.CheckFailure, app_commands.MissingPermissions)):
                text = str(error) or "You can't use that here."
            elif isinstance(error, app_commands.CommandOnCooldown):
                text = f"Slow down, try again in {error.retry_after:.0f}s."
            else:
                from .errors import report_error
                eid = report_error("slash", original, f"/{interaction.command.qualified_name if interaction.command else '?'} by {interaction.user.id}")
                text = f"Something went wrong (error ID `{eid}`)."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"⚠️ {text}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"⚠️ {text}", ephemeral=True)
            except discord.HTTPException:
                pass

        # ---------------- config
        @commands.command(name=f"{p}config", aliases=[f"{p}settings"])
        @commands.guild_only()
        async def config_cmd(self, ctx, module: str = None):
            """Open the settings panel (Manage Server)."""
            await open_panel(ctx, bot.registry, module)

        @app_commands.command(name="config", description="Open the settings panel for this bot")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.describe(module="Jump straight to one module")
        async def config_slash(self, interaction: discord.Interaction, module: str = None):
            await open_panel(interaction, bot.registry, module)

        @config_slash.autocomplete("module")
        async def module_ac(self, interaction, current):
            return [app_commands.Choice(name=m.label, value=m.name) for m in bot.registry.modules.values()
                    if current.casefold() in m.label.casefold()][:25]

        # ---------------- diagnostics
        @commands.command(name=f"{p}diag", aliases=[f"{p}diagnostics", f"{p}botinfo"])
        @commands.guild_only()
        async def diag_cmd(self, ctx, section: str = ""):
            """Bot diagnostics: `diag`, `diag errors`, `diag perms`, `diag config` (Manage Server)."""
            await do_diag(ctx, section)

        @app_commands.command(name="diag", description="Bot diagnostics (admins)")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_guild=True)
        @app_commands.choices(section=[app_commands.Choice(name=n, value=n) for n in ("overview", "errors", "perms", "config")])
        async def diag_slash(self, interaction: discord.Interaction, section: str = "overview"):
            await do_diag(interaction, section)

        # ---------------- preferences
        @commands.command(name=f"{p}timezone", aliases=[f"{p}tz"])
        async def timezone_cmd(self, ctx, *, zone: str = ""):
            """Set your timezone for reminders and events."""
            await do_timezone(ctx, zone)

        @app_commands.command(name="timezone", description="Set your timezone for reminders and events")
        async def timezone_slash(self, interaction: discord.Interaction, zone: str = ""):
            await do_timezone(interaction, zone)

        @commands.command(name=f"{p}prefs", aliases=[f"{p}preferences"])
        async def prefs_cmd(self, ctx):
            """Your personal notification preferences and opt-outs."""
            await ui.reply(ctx, embed=prefs_embed(bot.db, ctx.author.id), view=PrefsView(bot.db, ctx.author.id))

        @app_commands.command(name="prefs", description="Your personal notification preferences and opt-outs")
        async def prefs_slash(self, interaction: discord.Interaction):
            await ui.reply(interaction, embed=prefs_embed(bot.db, interaction.user.id), view=PrefsView(bot.db, interaction.user.id))

        # ---------------- export / import
        @commands.command(name=f"{p}configexport")
        @commands.guild_only()
        async def config_export(self, ctx):
            """Export this server's bot configuration as a JSON file (no tokens or secrets)."""
            if not checks.is_admin(ctx.author):
                return await ui.reply(ctx, embed=ui.err("You need **Manage Server**."))
            file, payload = await send_export(ctx, bot, ctx.guild, export_tables, "export")
            try:
                await ctx.author.send(f"Configuration export for **{ctx.guild.name}** ({len(payload['settings'])} modules).", file=file)
                await ui.reply(ctx, embed=ui.ok("Sent the export to your DMs and saved a copy to backup history."))
            except discord.Forbidden:
                await ui.reply(ctx, embed=ui.err("I couldn't DM you. Enable DMs from this server and try again (a copy was still saved to backup history)."))

        @commands.command(name=f"{p}configimport")
        @commands.guild_only()
        async def config_import(self, ctx):
            """Import a configuration export (attach the .json). Shows a preview and needs confirmation."""
            if not checks.is_admin(ctx.author):
                return await ui.reply(ctx, embed=ui.err("You need **Manage Server**."))
            if not ctx.message.attachments:
                return await ui.reply(ctx, embed=ui.err(f"Attach the exported `.json` file to `{ctx.prefix}{p}configimport`."))
            attachment = ctx.message.attachments[0]
            if attachment.size > 2_000_000:
                return await ui.reply(ctx, embed=ui.err("That file is too large to be a config export."))
            try:
                payload = json.loads(await attachment.read())
                settings, tables, unknown = validate_import(bot, payload, export_tables)
            except (ValueError, UnicodeDecodeError) as exc:
                return await ui.reply(ctx, embed=ui.err(f"Import rejected: {exc}"))
            note = f"\nIgnored unknown modules: {', '.join(unknown)}" if unknown else ""
            other = f"\n⚠️ This export came from a different server (`{payload.get('guild_id')}`); channel/role IDs may not exist here." if payload.get("guild_id") != ctx.guild.id else ""
            text = (f"Import **{len(settings) - len(unknown)}** module settings and {sum(len(r) for r in tables.values())} records "
                    f"({', '.join(tables) or 'no tables'})? Current settings will be **replaced**; a backup is taken first.{note}{other}")
            if not await ui.confirm(ctx, text, confirm_label="Import"):
                return
            current, _ = await send_export(ctx, bot, ctx.guild, export_tables, "pre-import")
            applied, restored = apply_import(bot, ctx.guild, {k: v for k, v in settings.items() if k not in unknown}, tables)
            await ui.reply(ctx, embed=ui.ok(f"Imported {applied} modules and restored {sum(restored.values())} records. Previous configuration saved to backup history ({p}configbackups)."))

        @commands.command(name=f"{p}configbackups")
        @commands.guild_only()
        async def config_backups(self, ctx, restore_id: int = None):
            """List configuration backup history, or restore one by ID (with confirmation)."""
            if not checks.is_admin(ctx.author):
                return await ui.reply(ctx, embed=ui.err("You need **Manage Server**."))
            if restore_id is None:
                rows = bot.db.fetchall("SELECT id, created_at, created_by, label FROM config_backups WHERE guild_id=? ORDER BY id DESC LIMIT 25", (ctx.guild.id,))
                lines = [f"`#{r['id']}` · {r['label']} · <t:{int(datetime.fromisoformat(r['created_at']).timestamp())}:f> · <@{r['created_by']}>" for r in rows]
                return await ui.paginate(ctx, ui.pages_from_lines("🗄️ Configuration backups", lines, footer=f"Restore with {p}configbackups <id>"))
            row = bot.db.fetchone("SELECT payload FROM config_backups WHERE guild_id=? AND id=?", (ctx.guild.id, restore_id))
            if not row:
                return await ui.reply(ctx, embed=ui.err("No backup with that ID for this server."))
            settings, tables, unknown = validate_import(bot, json.loads(row["payload"]), export_tables)
            if not await ui.confirm(ctx, f"Restore backup #{restore_id}? Current settings are backed up first.", confirm_label="Restore"):
                return
            await send_export(ctx, bot, ctx.guild, export_tables, "pre-restore")
            applied, restored = apply_import(bot, ctx.guild, {k: v for k, v in settings.items() if k not in unknown}, tables)
            await ui.reply(ctx, embed=ui.ok(f"Restored {applied} modules and {sum(restored.values())} records from backup #{restore_id}."))

    return Core()
