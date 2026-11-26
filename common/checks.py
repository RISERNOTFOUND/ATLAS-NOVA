"""Centralised permission checks. Every command AND every button/menu callback goes through these.

Authorisation is always by Discord ID or real guild permissions, never by username or role name.
"""
import discord
from discord import app_commands
from discord.ext import commands


def is_guild_owner(member):
    return bool(getattr(member, "guild", None) and member.id == member.guild.owner_id)


def is_admin(member):
    """Server owner, Administrator, or Manage Server."""
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    return is_guild_owner(member) or perms.administrator or perms.manage_guild


def is_mod(member):
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    return is_admin(member) or perms.moderate_members or perms.kick_members or perms.ban_members or perms.manage_messages


def has_perm(member, name):
    if not isinstance(member, discord.Member):
        return False
    return is_guild_owner(member) or member.guild_permissions.administrator or bool(getattr(member.guild_permissions, name, False))


def is_staff_for(member, staff_role_ids):
    """Moderator permissions, or one of the configured staff role IDs."""
    if is_mod(member):
        return True
    ids = {int(r) for r in (staff_role_ids or [])}
    return any(role.id in ids for role in getattr(member, "roles", []))


class NotAuthorized(commands.CheckFailure):
    pass


def admin_only():
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not is_admin(ctx.author):
            raise NotAuthorized("You need **Manage Server** (or Administrator) for this.")
        return True
    return commands.check(predicate)


def mod_only():
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not is_mod(ctx.author):
            raise NotAuthorized("You need moderator permissions for this.")
        return True
    return commands.check(predicate)


def perm_only(name, label=None):
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not has_perm(ctx.author, name):
            raise NotAuthorized(f"You need the **{label or name.replace('_', ' ').title()}** permission for this.")
        return True
    return commands.check(predicate)


def owner_id_only(owner_id):
    """Strict owner check by exact Discord user ID (never by name or admin rights)."""
    async def predicate(ctx):
        if ctx.author.id != int(owner_id):
            raise NotAuthorized("This command is restricted to the bot owner.")
        return True
    return commands.check(predicate)


def hierarchy_problem(actor, target, me):
    """Return a human message if actor/bot can't act on target, else None."""
    if target.id == target.guild.owner_id:
        return "The server owner can't be targeted."
    if actor is not None and target.id == actor.id:
        return "You can't target yourself."
    if actor is not None and actor.id != actor.guild.owner_id and target.top_role >= actor.top_role:
        return "Their highest role is equal to or above yours."
    if me is None or target.top_role >= me.top_role:
        return "My highest role must be above their highest role."
    return None


def role_problem(role, me, actor=None):
    """Why the bot (or actor) can't assign this role, or None."""
    if role.is_default():
        return "@everyone can't be assigned."
    if role.managed:
        return "It is managed by an integration/bot."
    if me is None or not me.guild_permissions.manage_roles:
        return "I'm missing **Manage Roles**."
    if role >= me.top_role:
        return "It is at or above my highest role."
    if actor is not None and actor.id != actor.guild.owner_id and role >= actor.top_role:
        return "It is at or above your highest role."
    return None


DANGEROUS_PERMS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks", "kick_members",
                   "ban_members", "moderate_members", "manage_messages", "mention_everyone", "manage_nicknames",
                   "manage_expressions", "manage_events", "manage_threads", "view_audit_log")


def dangerous_perms(role):
    return [p for p in DANGEROUS_PERMS if getattr(role.permissions, p, False)]


def missing_channel_perms(channel, member, *names):
    perms = channel.permissions_for(member)
    return [n.replace("_", " ").title() for n in names if not getattr(perms, n, False)]


# app-command level helpers (backend re-checks still happen inside every callback)
def app_admin():
    async def predicate(interaction: discord.Interaction):
        if not is_admin(interaction.user):
            raise app_commands.CheckFailure("You need **Manage Server** (or Administrator) for this.")
        return True
    return app_commands.check(predicate)


def app_mod():
    async def predicate(interaction: discord.Interaction):
        if not is_mod(interaction.user):
            raise app_commands.CheckFailure("You need moderator permissions for this.")
        return True
    return app_commands.check(predicate)
