"""Voice reliability helpers shared by ATLAS and NOVA.

The bug this fixes
------------------
discord.py's automatic voice reconnect (on a dropped voice socket) briefly sets the bot's voice
channel to None before rejoining. Both bots used to treat *any* "left voice" event as "a moderator
disconnected me", flagged the session as paused, and the 24/7 watchdog then never rejoined.

Now a self-disconnect is only treated as a moderator removal when:
  * the bot did not reconnect within a grace period, AND
  * the audit log shows a recent "member disconnect" entry (when the bot can read the audit log), or
    the main gateway is healthy and the bot has no audit-log access (best effort, labelled 'unconfirmed').
"""
import asyncio
import math
import time
from datetime import timedelta

import discord

GRACE_SECONDS = 20


class VoiceHealth:
    """Per-guild connection bookkeeping for diagnostics and back-off."""

    def __init__(self):
        self.failures = {}        # guild_id -> consecutive failures
        self.next_try = {}        # guild_id -> monotonic time before which we don't retry
        self.last_error = {}      # guild_id -> (ts, text)
        self.connected_since = {} # guild_id -> wall time
        self.last_disconnect = {} # guild_id -> (ts, classification)
        self.down_since = {}      # guild_id -> monotonic

    def ready(self, guild_id):
        return time.monotonic() >= self.next_try.get(guild_id, 0)

    def fail(self, guild_id, text):
        n = self.failures.get(guild_id, 0) + 1
        self.failures[guild_id] = n
        delay = min(600, 30 * (2 ** min(n - 1, 5)))  # 30s, 60s, 2m, 4m, 8m, 10m cap
        self.next_try[guild_id] = time.monotonic() + delay
        self.last_error[guild_id] = (time.time(), text)
        return delay

    def ok(self, guild_id):
        self.failures.pop(guild_id, None)
        self.next_try.pop(guild_id, None)
        self.down_since.pop(guild_id, None)
        self.connected_since.setdefault(guild_id, time.time())

    def gone(self, guild_id, classification):
        self.connected_since.pop(guild_id, None)
        self.last_disconnect[guild_id] = (time.time(), classification)


def gateway_healthy(client):
    latency = getattr(client, "latency", float("inf"))
    return client.is_ready() and not client.is_closed() and math.isfinite(latency) and latency < 5


async def classify_self_disconnect(client, guild, wait=GRACE_SECONDS):
    """Return 'transient' (reconnected / network), 'moderator' (confirmed by audit log),
    'moderator-unconfirmed' (no audit access, gateway healthy, didn't come back) or 'unknown'."""
    event_time = discord.utils.utcnow()
    await asyncio.sleep(wait)
    voice = guild.voice_client
    if voice is not None and voice.is_connected():
        return "transient"
    me = guild.me
    if me is not None and me.guild_permissions.view_audit_log:
        try:
            async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.member_disconnect,
                                                after=event_time - timedelta(seconds=30)):
                if entry.user and entry.user.id != client.user.id:
                    return "moderator"
            return "transient"
        except (discord.Forbidden, discord.HTTPException):
            pass
    if not gateway_healthy(client):
        return "transient"
    return "moderator-unconfirmed" if voice is None else "unknown"


async def connect_to(channel, timeout=30.0):
    """Connect (or move) with sane options. self_deaf: the bots only speak, they never record."""
    guild = channel.guild
    voice = guild.voice_client
    if voice is not None and voice.is_connected():
        if voice.channel.id != channel.id:
            await voice.move_to(channel, timeout=timeout)
        return voice
    if voice is not None:
        try:
            await voice.disconnect(force=True)
        except Exception:
            pass
    return await channel.connect(reconnect=True, timeout=timeout, self_deaf=True)


def missing_voice_perms(channel, me):
    perms = channel.permissions_for(me)
    missing = [name for name, ok in (("View Channel", perms.view_channel), ("Connect", perms.connect), ("Speak", perms.speak)) if not ok]
    if channel.user_limit and len(channel.members) >= channel.user_limit and not perms.move_members:
        missing.append("room (channel is full; Move Members would bypass)")
    return missing


def describe_error(exc):
    if isinstance(exc, asyncio.TimeoutError):
        return "Timed out while connecting to Discord's voice server (network, firewall or UDP blocked)."
    if isinstance(exc, discord.Forbidden):
        return "Discord refused the connection (missing Connect/Speak permission)."
    if isinstance(exc, discord.ClientException):
        return f"Voice client error: {exc}"
    if isinstance(exc, RuntimeError) and "nacl" in str(exc).lower():
        return "PyNaCl is not installed, voice encryption unavailable. Run setup.bat."
    if isinstance(exc, RuntimeError) and "dave" in str(exc).lower():
        return "The 'davey' package (Discord DAVE voice encryption) is missing. Run setup.bat."
    return f"{type(exc).__name__}: {str(exc)[:160]}"


def library_report():
    """Which voice dependencies are importable (shown in diagnostics)."""
    out = {"discord.py": discord.__version__}
    try:
        import nacl  # noqa: F401
        out["PyNaCl"] = "ok"
    except Exception:
        out["PyNaCl"] = "MISSING"
    try:
        import davey  # noqa: F401
        out["davey (DAVE)"] = "ok"
    except Exception:
        out["davey (DAVE)"] = "MISSING"
    try:
        import piper  # noqa: F401
        out["piper-tts"] = "ok"
    except Exception:
        out["piper-tts"] = "MISSING"
    try:
        out["opus"] = "loaded" if discord.opus.is_loaded() else "not loaded yet (loads on first playback)"
    except Exception:
        out["opus"] = "unknown"
    return out
