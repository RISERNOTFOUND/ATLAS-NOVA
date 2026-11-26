"""Versioned server rules with acceptance records, lookup, staff editing and update announcements.
ATLAS's original `!postrules` is unchanged; the first rules version is seeded from that same text.
"""
import re
import time

import discord
from discord.ext import commands

from common import checks, ui
from common.config import Module, Option


class AcceptRules(discord.ui.DynamicItem[discord.ui.Button], template=r"rules:accept"):
    def __init__(self):
        super().__init__(discord.ui.Button(label="I accept the rules", emoji="📜", style=discord.ButtonStyle.success, custom_id="rules:accept"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction):
        await interaction.client.get_cog("Rules").accept(interaction)


class EditRules(discord.ui.Modal, title="Edit server rules"):
    def __init__(self, cog, current):
        super().__init__()
        self.cog = cog
        self.text = discord.ui.TextInput(label="Rules (one rule per line)", style=discord.TextStyle.paragraph, default=current[:4000], max_length=4000)
        self.add_item(self.text)

    async def on_submit(self, interaction):
        if not checks.is_admin(interaction.user):
            return await interaction.response.send_message("Only admins can edit the rules.", ephemeral=True)
        self.cog.save_draft(interaction.guild_id, str(self.text.value), interaction.user.id)
        await interaction.response.send_message("Draft saved. Review it with `!rules draft`, then publish with `!rulespublish`.", ephemeral=True)


class Rules(commands.Cog, name="Rules"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("rules_001", """
            CREATE TABLE IF NOT EXISTS rules_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, version INTEGER NOT NULL,
                content TEXT NOT NULL, status TEXT NOT NULL, author_id INTEGER, created_ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_rules_guild ON rules_versions(guild_id, status);
            CREATE TABLE IF NOT EXISTS rules_acceptance (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, version INTEGER NOT NULL, ts REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, version))""")
        bot.registry.register(Module("rules", "Rules", "📜", "Versioned rules, acceptance records and update announcements.", [
            Option("channel", "Rules channel", "channel", None),
            Option("announce_updates", "Announce rule updates", "bool", True),
            Option("announce_channel", "Update announcement channel", "channel", None, "Defaults to the rules channel"),
            Option("remind_new", "Remind new members by DM", "bool", False),
            Option("acceptance_role", "Role given on acceptance", "role", None, "Optional"),
        ], default_enabled=True))
        bot.add_dynamic_items(AcceptRules)

    # ---------------------------------------------------------------- data
    def published(self, guild_id):
        row = self.db.fetchone("SELECT * FROM rules_versions WHERE guild_id=? AND status='published' ORDER BY version DESC LIMIT 1", (guild_id,))
        if row is None and self.db.scalar("SELECT count(*) FROM rules_versions WHERE guild_id=?", (guild_id,), 0) == 0:
            text = getattr(self.bot.atlas, "community_rules", "")
            lines = [l for l in text.splitlines() if re.match(r"^\d+\.", l.strip())]
            if lines:
                self.db.insert("INSERT INTO rules_versions(guild_id, version, content, status, created_ts) VALUES (?,?,?,?,?)",
                               (guild_id, 1, "\n".join(re.sub(r"^\d+\.\s*", "", l.strip()) for l in lines), "published", time.time()))
                return self.published(guild_id)
        return dict(row) if row else None

    def draft(self, guild_id):
        row = self.db.fetchone("SELECT * FROM rules_versions WHERE guild_id=? AND status='draft' ORDER BY id DESC LIMIT 1", (guild_id,))
        return dict(row) if row else None

    def save_draft(self, guild_id, text, author_id):
        lines = [re.sub(r"^\s*\d+[.)]\s*", "", l).strip() for l in text.splitlines() if l.strip()]
        self.db.execute("DELETE FROM rules_versions WHERE guild_id=? AND status='draft'", (guild_id,))
        current = self.published(guild_id)
        self.db.insert("INSERT INTO rules_versions(guild_id, version, content, status, author_id, created_ts) VALUES (?,?,?,?,?,?)",
                       (guild_id, (current["version"] + 1) if current else 1, "\n".join(lines), "draft", author_id, time.time()))

    def has_accepted(self, guild_id, user_id):
        current = self.published(guild_id)
        if current is None:
            return True
        return bool(self.db.fetchone("SELECT 1 FROM rules_acceptance WHERE guild_id=? AND user_id=? AND version=?", (guild_id, user_id, current["version"])))

    def rules_embed(self, guild, row=None, title=None):
        row = row or self.published(guild.id)
        if row is None:
            return ui.embed("📜 Rules", "No rules have been published yet.")
        lines = [f"**{i}.** {l}" for i, l in enumerate(row["content"].splitlines(), 1)]
        e = discord.Embed(title=title or f"📜 {guild.name} rules", description=ui.clip("\n".join(lines), 4000), colour=ui.PINK)
        e.set_footer(text=f"Version {row['version']} · updated {time.strftime('%d %b %Y', time.localtime(row['created_ts']))}")
        return e

    def accept_view(self):
        v = discord.ui.View(timeout=None)
        v.add_item(AcceptRules())
        return v

    async def accept(self, interaction):
        current = self.published(interaction.guild_id)
        if current is None:
            return await interaction.response.send_message("There are no published rules to accept yet.", ephemeral=True)
        self.db.execute("INSERT OR IGNORE INTO rules_acceptance(guild_id, user_id, version, ts) VALUES (?,?,?,?)",
                        (interaction.guild_id, interaction.user.id, current["version"], time.time()))
        role = self.bot.registry.role(interaction.guild, "rules", "acceptance_role")
        note = ""
        if role and role not in interaction.user.roles and checks.role_problem(role, interaction.guild.me) is None:
            try:
                await interaction.user.add_roles(role, reason="Accepted the rules")
            except discord.HTTPException:
                note = " (I couldn't give the role — please tell staff.)"
        verify = self.bot.registry.get(interaction.guild_id, "verification")
        extra = " Now press **Verify** again to finish." if verify.get("enabled") and verify.get("require_rules") else ""
        await interaction.response.send_message(f"✅ Thanks for accepting rules version {current['version']}!{note}{extra}", ephemeral=True)

    # ---------------------------------------------------------------- events
    @commands.Cog.listener()
    async def on_member_join(self, member):
        s = self.bot.registry.get(member.guild.id, "rules")
        if member.bot or not (s.get("enabled") and s.get("remind_new")):
            return
        channel = self.bot.registry.channel(member.guild, "rules", "channel")
        await ui.safe_dm(member, f"👋 Welcome to **{member.guild.name}**! Please read the rules" + (f" in {channel.mention}" if channel else " (`!rules`)") + " and press **I accept** there.")

    # ---------------------------------------------------------------- commands
    @commands.group(name="rules", invoke_without_command=True)
    @commands.guild_only()
    async def rules(self, ctx):
        """Show the current rules (with an accept button)."""
        await ui.reply(ctx, embed=self.rules_embed(ctx.guild), view=self.accept_view(), ephemeral=False)

    @rules.command(name="draft")
    @checks.admin_only()
    async def rules_draft(self, ctx):
        d = self.draft(ctx.guild.id)
        await ui.reply(ctx, embed=self.rules_embed(ctx.guild, d, "📝 Draft rules (not published)") if d else ui.err("No draft. Use `!rulesedit`."))

    @rules.command(name="history")
    @checks.admin_only()
    async def rules_history(self, ctx):
        rows = self.db.fetchall("SELECT version, status, created_ts, author_id FROM rules_versions WHERE guild_id=? ORDER BY id DESC", (ctx.guild.id,))
        await ui.reply(ctx, embed=ui.embed("📜 Rules history", "\n".join(f"v{r['version']} · {r['status']} · <t:{int(r['created_ts'])}:d>" + (f" · <@{r['author_id']}>" if r["author_id"] else " · seeded from !postrules text") for r in rows) or "None."))

    @commands.command(name="rule")
    @commands.guild_only()
    async def rule(self, ctx, number: int):
        """Look up a single rule by number."""
        row = self.published(ctx.guild.id)
        lines = row["content"].splitlines() if row else []
        if not 1 <= number <= len(lines):
            return await ui.reply(ctx, embed=ui.err(f"There are {len(lines)} rules."))
        await ui.reply(ctx, embed=discord.Embed(title=f"📜 Rule {number}", description=lines[number - 1], colour=ui.PINK), ephemeral=False)

    @commands.command(name="rulesedit")
    @commands.guild_only()
    @checks.admin_only()
    async def rulesedit(self, ctx, *, text: str = None):
        """Edit the rules: `!rulesedit` opens an editor (button), or pass the full text with one rule per line."""
        if text:
            self.save_draft(ctx.guild.id, text, ctx.author.id)
            return await ui.reply(ctx, embed=ui.ok("Draft saved. `!rules draft` to review, `!rulespublish` to publish."))
        current = (self.draft(ctx.guild.id) or self.published(ctx.guild.id) or {"content": ""})["content"]
        view = discord.ui.View(timeout=120)
        button = discord.ui.Button(label="Open editor", emoji="✏️", style=discord.ButtonStyle.primary)

        async def open_editor(interaction):
            if interaction.user.id != ctx.author.id or not checks.is_admin(interaction.user):
                return await interaction.response.send_message("Only the admin who ran the command can use this.", ephemeral=True)
            await interaction.response.send_modal(EditRules(self, current))
        button.callback = open_editor
        view.add_item(button)
        await ui.reply(ctx, content="Press the button to edit the rules in a form:", view=view)

    @commands.command(name="rulespublish")
    @commands.guild_only()
    @checks.admin_only()
    async def rulespublish(self, ctx):
        """Publish the draft as a new rules version (members can re-accept)."""
        d = self.draft(ctx.guild.id)
        if not d:
            return await ui.reply(ctx, embed=ui.err("No draft to publish. Use `!rulesedit` first."))
        if not await ui.confirm(ctx, f"Publish rules version {d['version']}? Members will be asked to accept the new version.", confirm_label="Publish", danger=False):
            return
        self.db.execute("UPDATE rules_versions SET status='archived' WHERE guild_id=? AND status='published'", (ctx.guild.id,))
        self.db.execute("UPDATE rules_versions SET status='published', created_ts=? WHERE id=?", (time.time(), d["id"]))
        s = self.bot.registry.get(ctx.guild.id, "rules")
        channel = self.bot.registry.channel(ctx.guild, "rules", "channel")
        if isinstance(channel, discord.TextChannel):
            await ui.safe_send(channel, embed=self.rules_embed(ctx.guild), view=self.accept_view())
        if s.get("announce_updates", True):
            dest = self.bot.registry.channel(ctx.guild, "rules", "announce_channel") or channel
            if dest and dest != channel:
                await ui.safe_send(dest, embed=ui.embed("📜 Rules updated", f"The server rules were updated to version {d['version']}. Please read them" + (f" in {channel.mention}" if channel else " with `!rules`") + " and accept again."))
        await ui.reply(ctx, embed=ui.ok(f"Rules version {d['version']} published."))

    @commands.command(name="rulesstatus")
    @commands.guild_only()
    @checks.mod_only()
    async def rulesstatus(self, ctx):
        """How many members accepted the current rules."""
        current = self.published(ctx.guild.id)
        if not current:
            return await ui.reply(ctx, embed=ui.err("No published rules."))
        accepted = self.db.scalar("SELECT count(*) FROM rules_acceptance WHERE guild_id=? AND version=?", (ctx.guild.id, current["version"]), 0)
        humans = sum(1 for m in ctx.guild.members if not m.bot)
        await ui.reply(ctx, embed=ui.embed("📜 Rules acceptance", f"Version {current['version']}: **{accepted}** of {humans} members accepted."))


async def setup(bot):
    await bot.add_cog(Rules(bot))
