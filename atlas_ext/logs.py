"""Configurable audit logging to private staff channels.

Message contents of edited/deleted messages are NOT logged unless an admin explicitly enables
"Include message text" for this server (off by default). Other modules post into the log through
`bot.dispatch("bot_log", guild, category, embed)`.
"""
import discord
from discord.ext import commands

from common import ui
from common.config import Module, Option

CATEGORIES = {"member": "member_channel", "message": "message_channel", "moderation": "mod_channel", "server": "server_channel",
              "voice": "voice_channel", "tickets": "mod_channel", "verification": "mod_channel", "config": "server_channel",
              "errors": "server_channel", "roles": "member_channel", "automod": "mod_channel", "reports": "mod_channel"}


class Logs(commands.Cog, name="Logging"):
    def __init__(self, bot):
        self.bot = bot
        bot.registry.register(Module("logging", "Logging & audit trail", "📜",
                                     "Posts server events to private staff channels. Keep log channels staff-only.", [
            Option("log_channel", "Default log channel", "channel", None, "Used when a category has no own channel"),
            Option("member_channel", "Member log channel", "channel", None, "Joins, leaves, roles, nicknames"),
            Option("message_channel", "Message log channel", "channel", None, "Edits and deletions"),
            Option("mod_channel", "Moderation log channel", "channel", None, "Mod actions, tickets, verification, automod"),
            Option("server_channel", "Server log channel", "channel", None, "Channels, roles, config changes, bot errors"),
            Option("voice_channel", "Voice log channel", "channel", None, "Voice joins/leaves and voice failures"),
            Option("joins", "Log joins", "bool", True), Option("leaves", "Log leaves", "bool", True),
            Option("roles", "Log role changes", "bool", True), Option("nicknames", "Log nickname changes", "bool", True),
            Option("message_delete", "Log deletions", "bool", True), Option("message_edit", "Log edits", "bool", True),
            Option("message_content", "Include message text", "bool", False, "Off = only author/channel/length are logged"),
            Option("channels", "Log channel changes", "bool", True), Option("permissions", "Log role/permission changes", "bool", True),
            Option("voice", "Log voice joins/leaves", "bool", False), Option("ignored_channels", "Ignored channels", "channels", []),
        ]))

    def settings(self, guild):
        return self.bot.registry.get(guild.id, "logging")

    async def post(self, guild, category, embed, flag=None):
        if guild is None:
            return
        s = self.settings(guild)
        if not s.get("enabled") or (flag and not s.get(flag, True)):
            return
        channel_id = s.get(CATEGORIES.get(category, "log_channel")) or s.get("log_channel")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        embed.timestamp = embed.timestamp or discord.utils.utcnow()
        await ui.safe_send(channel, embed=embed)

    def ignored(self, guild, channel_id):
        return int(channel_id or 0) in {int(c) for c in self.settings(guild).get("ignored_channels") or []}

    @commands.Cog.listener()
    async def on_bot_log(self, guild, category, embed):
        await self.post(guild, category, embed)

    @commands.Cog.listener()
    async def on_bot_config_change(self, guild, user, module, key, old, new):
        e = discord.Embed(title="⚙️ Configuration changed", colour=ui.INFO,
                          description=f"{user.mention} changed **{module}.{key}**\n`{ui.clip(old, 200)}` → `{ui.clip(new, 200)}`")
        await self.post(guild, "config", e)

    # ---------------------------------------------------------------- members
    @commands.Cog.listener()
    async def on_member_join(self, member):
        age = discord.utils.utcnow() - member.created_at
        e = discord.Embed(title="📥 Member joined", colour=ui.GOOD, description=f"{member.mention} `{member}` (`{member.id}`)")
        e.add_field(name="Account created", value=f"{discord.utils.format_dt(member.created_at, 'F')} ({age.days} days ago)")
        if age.days < 7:
            e.add_field(name="⚠️ New account", value="Created less than a week ago.")
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"Members: {member.guild.member_count}")
        await self.post(member.guild, "member", e, "joins")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        roles = [r.mention for r in member.roles if not r.is_default()]
        e = discord.Embed(title="📤 Member left", colour=ui.WARN, description=f"{member.mention} `{member}` (`{member.id}`)")
        if member.joined_at:
            e.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R"))
        if roles:
            e.add_field(name="Roles", value=ui.clip(" ".join(roles), 1000), inline=False)
        await self.post(member.guild, "member", e, "leaves")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.nick != after.nick:
            e = discord.Embed(title="🏷️ Nickname changed", colour=ui.INFO, description=f"{after.mention}\n`{before.nick or before.name}` → `{after.nick or after.name}`")
            await self.post(after.guild, "member", e, "nicknames")
        if before.roles != after.roles:
            added = [r.mention for r in after.roles if r not in before.roles]
            removed = [r.mention for r in before.roles if r not in after.roles]
            e = discord.Embed(title="🎭 Roles changed", colour=ui.INFO, description=after.mention)
            if added:
                e.add_field(name="Added", value=ui.clip(" ".join(added), 1000))
            if removed:
                e.add_field(name="Removed", value=ui.clip(" ".join(removed), 1000))
            await self.post(after.guild, "roles", e, "roles")
        if before.timed_out_until != after.timed_out_until:
            e = discord.Embed(title="⏳ Timeout " + ("applied" if after.timed_out_until else "removed"), colour=ui.WARN, description=after.mention)
            if after.timed_out_until:
                e.add_field(name="Until", value=discord.utils.format_dt(after.timed_out_until, "F"))
            await self.post(after.guild, "moderation", e)

    # ---------------------------------------------------------------- messages
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.guild is None or message.author.bot or self.ignored(message.guild, message.channel.id):
            return
        s = self.settings(message.guild)
        e = discord.Embed(title="🗑️ Message deleted", colour=ui.BAD,
                          description=f"By {message.author.mention} in {message.channel.mention}\nLength: {len(message.content)} characters · {len(message.attachments)} attachment(s)")
        if s.get("message_content") and message.content:
            e.add_field(name="Content", value=ui.clip(message.content, 1000), inline=False)
        e.set_footer(text=f"Author ID {message.author.id} · Message ID {message.id}")
        await self.post(message.guild, "message", e, "message_delete")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if guild is None or self.ignored(guild, payload.channel_id):
            return
        e = discord.Embed(title="🧹 Bulk delete", colour=ui.BAD, description=f"{len(payload.message_ids)} messages in <#{payload.channel_id}>")
        await self.post(guild, "message", e, "message_delete")

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if after.guild is None or after.author.bot or before.content == after.content or self.ignored(after.guild, after.channel.id):
            return
        s = self.settings(after.guild)
        e = discord.Embed(title="✏️ Message edited", colour=ui.INFO, description=f"By {after.author.mention} in {after.channel.mention} · [jump]({after.jump_url})")
        if s.get("message_content"):
            e.add_field(name="Before", value=ui.clip(before.content or "—", 1000), inline=False)
            e.add_field(name="After", value=ui.clip(after.content or "—", 1000), inline=False)
        await self.post(after.guild, "message", e, "message_edit")

    # ---------------------------------------------------------------- server
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        await self.post(channel.guild, "server", discord.Embed(title="➕ Channel created", colour=ui.GOOD, description=f"{channel.mention} ({channel.type})"), "channels")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        await self.post(channel.guild, "server", discord.Embed(title="➖ Channel deleted", colour=ui.BAD, description=f"#{channel.name} ({channel.type})"), "channels")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        changes = []
        if before.name != after.name:
            changes.append(f"name `{before.name}` → `{after.name}`")
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append("topic changed")
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append(f"slowmode {before.slowmode_delay}s → {after.slowmode_delay}s")
        if before.overwrites != after.overwrites:
            changes.append("permission overwrites changed")
        if changes:
            await self.post(after.guild, "server", discord.Embed(title="🔧 Channel updated", colour=ui.INFO, description=f"{after.mention}\n" + "\n".join(changes)), "channels")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        await self.post(role.guild, "server", discord.Embed(title="➕ Role created", colour=ui.GOOD, description=role.mention), "permissions")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        await self.post(role.guild, "server", discord.Embed(title="➖ Role deleted", colour=ui.BAD, description=f"@{role.name}"), "permissions")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        changes = []
        if before.name != after.name:
            changes.append(f"name `{before.name}` → `{after.name}`")
        if before.permissions != after.permissions:
            gained = [p for p, v in after.permissions if v and not getattr(before.permissions, p)]
            lost = [p for p, v in before.permissions if v and not getattr(after.permissions, p)]
            if gained:
                changes.append("➕ " + ", ".join(gained))
            if lost:
                changes.append("➖ " + ", ".join(lost))
        if before.colour != after.colour:
            changes.append(f"colour {before.colour} → {after.colour}")
        if changes:
            await self.post(after.guild, "server", discord.Embed(title="🔧 Role updated", colour=ui.INFO, description=f"{after.mention}\n" + ui.clip("\n".join(changes), 3500)), "permissions")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or before.channel == after.channel:
            return
        if after.channel and not before.channel:
            text = f"{member.mention} joined {after.channel.mention}"
        elif before.channel and not after.channel:
            text = f"{member.mention} left {before.channel.mention}"
        else:
            text = f"{member.mention} moved {before.channel.mention} → {after.channel.mention}"
        await self.post(member.guild, "voice", discord.Embed(description=f"🔈 {text}", colour=ui.INFO), "voice")


async def setup(bot):
    await bot.add_cog(Logs(bot))
