"""Button verification, rules acceptance, waiting periods, manual review and join-spike alerts.

Lock-out protection: verification only ever ADDS the verified role. It never removes roles or
changes channel permissions; administrators and existing members are untouched. Run
`!verify grantall` before you restrict channels to the verified role. Suspicious joins and join
spikes only alert staff (human review); nobody is banned or kicked automatically.
"""
import time
from collections import defaultdict, deque

import discord
from discord.ext import commands

from common import checks, ui
from common.config import Module, Option

from .moderation import add_review_item


def settings_of(bot, guild_id):
    return bot.registry.get(guild_id, "verification")


async def grant(bot, guild, member, actor_id, how):
    s = settings_of(bot, guild.id)
    role = guild.get_role(int(s["verified_role"])) if s.get("verified_role") else None
    if role is None:
        return "No verified role is configured. An admin can set it with `!config verification`."
    problem = checks.role_problem(role, guild.me)
    if problem:
        return f"I can't give {role.mention}: {problem}"
    if role in member.roles:
        return "already"
    await member.add_roles(role, reason=f"Verification ({how}) by {actor_id}")
    bot.db.insert("INSERT INTO verification_log(guild_id, user_id, result, actor_id, how, ts) VALUES (?,?,?,?,?,?)",
                  (guild.id, member.id, "verified", actor_id, how, time.time()))
    bot.dispatch("bot_log", guild, "verification", discord.Embed(description=f"✅ {member.mention} verified ({how})", colour=ui.GOOD))
    return None


class VerifyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"verify:go"):
    def __init__(self):
        super().__init__(discord.ui.Button(label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id="verify:go"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls()

    async def callback(self, interaction):
        bot, guild, member = interaction.client, interaction.guild, interaction.user
        s = settings_of(bot, guild.id)
        if not s.get("enabled"):
            return await interaction.response.send_message("Verification is currently switched off here.", ephemeral=True)
        role = guild.get_role(int(s["verified_role"])) if s.get("verified_role") else None
        if role and role in member.roles:
            return await interaction.response.send_message("You're already verified. 💛", ephemeral=True)
        waited = (discord.utils.utcnow() - member.joined_at).total_seconds() / 60 if member.joined_at else 999
        if waited < int(s.get("wait_minutes") or 0):
            left = int(s["wait_minutes"]) - int(waited)
            bot.db.insert("INSERT INTO verification_log(guild_id, user_id, result, actor_id, how, ts) VALUES (?,?,?,?,?,?)",
                          (guild.id, member.id, "too_early", member.id, "button", time.time()))
            return await interaction.response.send_message(f"Please look around for **{left} more minute(s)**, then press Verify again.", ephemeral=True)
        if s.get("require_rules"):
            rules_cog = bot.get_cog("Rules")
            if rules_cog and not rules_cog.has_accepted(guild.id, member.id):
                return await interaction.response.send_message(embed=rules_cog.rules_embed(guild), view=rules_cog.accept_view(), ephemeral=True)
        age_days = (discord.utils.utcnow() - member.created_at).days
        if age_days < int(s.get("min_account_days") or 0):
            pending = bot.db.scalar("SELECT count(*) FROM verification_log WHERE guild_id=? AND user_id=? AND result='pending'", (guild.id, member.id), 0)
            if not pending:
                bot.db.insert("INSERT INTO verification_log(guild_id, user_id, result, actor_id, how, ts) VALUES (?,?,?,?,?,?)",
                              (guild.id, member.id, "pending", member.id, "account-age", time.time()))
                await add_review_item(bot, guild, "verification", f"{member} wants to verify; account is {age_days} day(s) old (minimum {s['min_account_days']}).",
                                      target_id=member.id, extra=f"Approve with `!verify approve {member.id}` or deny with `!verify deny {member.id}`.")
            return await interaction.response.send_message("Thanks! Because your account is very new, a staff member will verify you shortly. 💛", ephemeral=True)
        try:
            problem = await grant(bot, guild, member, member.id, "button")
        except discord.HTTPException:
            problem = "Discord refused the role change. Please ask staff."
        if problem and problem != "already":
            bot.db.insert("INSERT INTO verification_log(guild_id, user_id, result, actor_id, how, ts) VALUES (?,?,?,?,?,?)",
                          (guild.id, member.id, "failed", member.id, problem[:100], time.time()))
            bot.dispatch("bot_log", guild, "verification", discord.Embed(description=f"⚠️ Verification failed for {member.mention}: {problem}", colour=ui.BAD))
            return await interaction.response.send_message(f"⚠️ {problem}", ephemeral=True)
        await interaction.response.send_message("✅ You're verified. Welcome in!", ephemeral=True)


class Verification(commands.Cog, name="Verification"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.joins = defaultdict(lambda: deque(maxlen=200))
        self.last_alert = {}
        bot.db.migrate("verification_001", """
            CREATE TABLE IF NOT EXISTS verification_log (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                result TEXT NOT NULL, actor_id INTEGER, how TEXT, ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_verify_guild ON verification_log(guild_id, ts)""")
        bot.registry.register(Module("verification", "Verification & anti-raid", "🛂",
                                     "Members press a button to get the verified role. Only adds a role; never removes anything. Run `!verify grantall` before restricting channels.", [
            Option("verified_role", "Verified role", "role", None, "Role given after verification"),
            Option("panel_channel", "Verification channel", "channel", None),
            Option("require_rules", "Require rules acceptance", "bool", False, "Uses the Rules module"),
            Option("wait_minutes", "Waiting period (minutes)", "int", 0, "Time in the server before verifying", 0, 1440),
            Option("min_account_days", "Min. account age (days)", "int", 0, "Younger accounts go to manual review", 0, 365),
            Option("spike_joins", "Join-spike threshold", "int", 10, "Joins within the window that trigger an alert", 3, 200),
            Option("spike_seconds", "Join-spike window (s)", "int", 60, "", 10, 3600),
            Option("alert_channel", "Raid alert channel", "channel", None), Option("alert_role", "Role to ping on alerts", "role", None),
        ]))
        bot.add_dynamic_items(VerifyButton)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        s = settings_of(self.bot, member.guild.id)
        now = time.time()
        q = self.joins[member.guild.id]
        q.append((now, member.id, (discord.utils.utcnow() - member.created_at).days))
        window = int(s.get("spike_seconds") or 60)
        recent = [j for j in q if now - j[0] <= window]
        threshold = int(s.get("spike_joins") or 10)
        if len(recent) >= threshold and now - self.last_alert.get(member.guild.id, 0) > 600:
            self.last_alert[member.guild.id] = now
            young = sum(1 for j in recent if j[2] < 7)
            channel = member.guild.get_channel(int(s["alert_channel"])) if s.get("alert_channel") else None
            text = (f"🚨 **Join spike:** {len(recent)} members joined in the last {window}s ({young} with accounts under 7 days old). "
                    "No automatic action was taken. Consider enabling verification or slowmode, and review recent joins.")
            role = member.guild.get_role(int(s["alert_role"])) if s.get("alert_role") else None
            if isinstance(channel, discord.TextChannel):
                await ui.safe_send(channel, (role.mention + " " if role else "") + text, allowed_mentions=discord.AllowedMentions(roles=True))
            self.bot.dispatch("bot_log", member.guild, "verification", discord.Embed(description=text, colour=ui.BAD))
            await add_review_item(self.bot, member.guild, "join spike", text)

    def panel_embed(self, guild):
        s = settings_of(self.bot, guild.id)
        e = discord.Embed(title=f"🛂 Welcome to {guild.name}", colour=ui.PINK,
                          description="Press **Verify** below to unlock the rest of the server." +
                          ("\nYou'll be asked to accept the rules first." if s.get("require_rules") else "") +
                          (f"\nNew here? Take {s['wait_minutes']} minute(s) to look around first." if int(s.get("wait_minutes") or 0) else ""))
        e.set_footer(text="Verification only gives you a role. It never reads your messages or DMs.")
        return e

    @commands.group(name="verify", invoke_without_command=True)
    @commands.guild_only()
    async def verify(self, ctx):
        """Verification tools. Staff: panel, grantall, approve, deny, stats."""
        await ui.reply(ctx, embed=ui.embed(description="Use the **Verify** button in the verification channel. Staff: `!verify panel`, `!verify grantall`, `!verify approve @m`, `!verify deny @m`, `!verify stats`."))

    @verify.command(name="panel")
    @checks.admin_only()
    async def panel(self, ctx, channel: discord.TextChannel = None):
        """Post the verification button."""
        s = settings_of(self.bot, ctx.guild.id)
        if not s.get("verified_role"):
            return await ui.reply(ctx, embed=ui.err("Set the verified role first: `!config verification`."))
        channel = channel or (ctx.guild.get_channel(int(s["panel_channel"])) if s.get("panel_channel") else ctx.channel)
        view = discord.ui.View(timeout=None)
        view.add_item(VerifyButton())
        msg = await channel.send(embed=self.panel_embed(ctx.guild), view=view)
        self.bot.registry.set(ctx.guild, ctx.author, "verification", "panel_channel", channel.id)
        if not s.get("enabled"):
            self.bot.registry.set(ctx.guild, ctx.author, "verification", "enabled", True)
        await ui.reply(ctx, embed=ui.ok(f"Verification panel posted in {channel.mention}. Reminder: run `!verify grantall` so existing members aren't locked out when you restrict channels."))

    @verify.command(name="grantall")
    @checks.admin_only()
    async def grantall(self, ctx):
        """Give the verified role to every current human member (lock-out protection)."""
        s = settings_of(self.bot, ctx.guild.id)
        role = ctx.guild.get_role(int(s["verified_role"])) if s.get("verified_role") else None
        if role is None:
            return await ui.reply(ctx, embed=ui.err("Set the verified role first: `!config verification`."))
        problem = checks.role_problem(role, ctx.guild.me)
        if problem:
            return await ui.reply(ctx, embed=ui.err(problem))
        targets = [m for m in ctx.guild.members if not m.bot and role not in m.roles]
        if not targets:
            return await ui.reply(ctx, embed=ui.ok("Everyone already has the verified role."))
        if not await ui.confirm(ctx, f"Give {role.mention} to **{len(targets)}** current members? This only adds a role.", confirm_label="Grant", danger=False):
            return
        status = await ctx.send(f"Granting… 0/{len(targets)}")
        done = failed = 0
        for i, member in enumerate(targets, 1):
            try:
                await member.add_roles(role, reason=f"Verification grantall by {ctx.author}")
                done += 1
            except discord.HTTPException:
                failed += 1
            if i % 25 == 0:
                await status.edit(content=f"Granting… {i}/{len(targets)}")
            await ui.sleep_rate()
        await status.edit(content=f"✅ Granted to {done} member(s); {failed} failed.")

    @verify.command(name="approve")
    @checks.mod_only()
    async def approve(self, ctx, member: discord.Member):
        """Manually verify a member."""
        try:
            problem = await grant(self.bot, ctx.guild, member, ctx.author.id, "manual")
        except discord.HTTPException:
            problem = "Discord refused the role change."
        self.db.execute("UPDATE verification_log SET result='approved' WHERE guild_id=? AND user_id=? AND result='pending'", (ctx.guild.id, member.id))
        if problem and problem != "already":
            return await ui.reply(ctx, embed=ui.err(problem))
        await ui.safe_dm(member, f"✅ You've been verified in **{ctx.guild.name}**. Welcome!")
        await ui.reply(ctx, embed=ui.ok(f"{member.mention} is verified."))

    @verify.command(name="deny")
    @checks.mod_only()
    async def deny(self, ctx, member: discord.Member, *, reason: str = "Not approved"):
        """Record a denied verification (the member simply stays unverified; nothing else happens)."""
        self.db.execute("UPDATE verification_log SET result='denied' WHERE guild_id=? AND user_id=? AND result='pending'", (ctx.guild.id, member.id))
        self.db.insert("INSERT INTO verification_log(guild_id, user_id, result, actor_id, how, ts) VALUES (?,?,?,?,?,?)",
                       (ctx.guild.id, member.id, "denied", ctx.author.id, reason[:100], time.time()))
        await ui.reply(ctx, embed=ui.ok(f"Recorded. {member.mention} stays unverified (no kick or ban)."))

    @verify.command(name="stats")
    @checks.mod_only()
    async def stats(self, ctx, days: int = 30):
        since = time.time() - days * 86400
        rows = self.db.fetchall("SELECT result, count(*) c FROM verification_log WHERE guild_id=? AND ts>=? GROUP BY result", (ctx.guild.id, since))
        counts = {r["result"]: r["c"] for r in rows}
        pending = self.db.scalar("SELECT count(*) FROM verification_log WHERE guild_id=? AND result='pending'", (ctx.guild.id,), 0)
        e = discord.Embed(title=f"🛂 Verification · last {days} days", colour=ui.INFO,
                          description="\n".join(f"**{k}**: {v}" for k, v in counts.items()) or "No verification activity.")
        e.add_field(name="Waiting for manual review", value=str(pending))
        await ui.reply(ctx, embed=e)


async def setup(bot):
    await bot.add_cog(Verification(bot))
