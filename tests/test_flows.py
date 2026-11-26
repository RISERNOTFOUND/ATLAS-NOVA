"""Flow tests with fake Discord objects (no network). SRC must point at a scratch copy (imports run data migrations)."""
import ast
import asyncio
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

SRC = Path(os.environ["SRC"])
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.modules.setdefault("piper", types.ModuleType("piper"))

import discord  # noqa: E402

OWNER = 111111111111111111  # fake ID; conftest exports it as MODERATION_OWNER_ID


def run(coro):
    return asyncio.run(coro)


class Sink:
    def __init__(self):
        self.sent = []

    async def __call__(self, *a, **k):
        self.sent.append((a, k))
        return types.SimpleNamespace(edit=Sink(), id=1, jump_url="https://x")


# ------------------------------------------------------------------ fake roles
class FakeRole:
    def __init__(self, name, pos, colour=0, managed=False, default=False, secondary=None, perms=None):
        self.name, self.position, self.managed, self._default = name, pos, managed, default
        self.colour = discord.Colour(colour)
        self.secondary_colour, self.tertiary_colour = secondary, None
        self.id = 1000 + pos
        self.mention = f"<@&{self.id}>"
        self.permissions = discord.Permissions(perms or 0)
        self.edits = []

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position

    def __gt__(self, other):
        return self.position > other.position

    def __lt__(self, other):
        return self.position < other.position

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


# ------------------------------------------------------------------ NOVA owner-only colour reset
@pytest.fixture(scope="module")
def nova():
    import nova as m
    return m


def colour_setup(author_id, manage_roles=True):
    top = FakeRole("NOVA", 10)
    roles = [FakeRole("@everyone", 0, default=True), FakeRole("Red", 2, 0xFF0000), FakeRole("Plain", 4, 0),
             FakeRole("Bot integration", 5, 0x00FF00, managed=True), FakeRole("Admin", 12, 0x0000FF),
             FakeRole("Gradient", 3, 0, secondary=discord.Colour(0x123456)), top]
    me = types.SimpleNamespace(top_role=top, guild_permissions=types.SimpleNamespace(manage_roles=manage_roles))
    guild = types.SimpleNamespace(name="Test", roles=roles, me=me)
    reply = Sink()
    message = types.SimpleNamespace(author=types.SimpleNamespace(id=author_id), guild=guild, reply=reply)
    return message, roles, reply


def test_clear_colour_rejects_everyone_but_owner(nova):
    for intruder in (1, 123456789, OWNER + 1):
        message, roles, reply = colour_setup(intruder)
        run(nova.handle_clear_role_colour(message, ""))
        assert "Unauthorized" in reply.sent[0][0][0]
        assert all(not r.edits for r in roles)


def test_clear_colour_requires_manage_roles(nova):
    message, roles, reply = colour_setup(OWNER, manage_roles=False)
    run(nova.handle_clear_role_colour(message, ""))
    assert "Manage Roles" in reply.sent[0][0][0] and all(not r.edits for r in roles)


def test_clear_colour_owner_confirmed_changes_colour_only(nova, monkeypatch):
    async def auto_confirm(self):
        self.confirmed = True
    monkeypatch.setattr(nova.ConfirmClearColourView, "wait", auto_confirm)
    monkeypatch.setattr(nova.asyncio, "sleep", lambda s: asyncio.sleep(0))
    message, roles, reply = colour_setup(OWNER)
    run(nova.handle_clear_role_colour(message, ""))
    by = {r.name: r for r in roles}
    assert by["Red"].edits[0].keys() == {"colour", "reason"} and by["Red"].edits[0]["colour"] == discord.Colour.default()
    assert set(by["Gradient"].edits[0]) == {"colour", "secondary_colour", "tertiary_colour", "reason"}
    for skipped in ("@everyone", "Plain", "Bot integration", "Admin", "NOVA"):
        assert not by[skipped].edits, skipped
    preview = reply.sent[0][1]["embed"].description
    assert "Eligible for reset:** 2" in preview and "Skipped:** 2" in preview
    report = reply.sent[1][1]["embed"].description
    assert "Successfully reset:** 2" in report


def test_clear_colour_cancel_changes_nothing(nova, monkeypatch):
    async def cancel(self):
        self.confirmed = False
    monkeypatch.setattr(nova.ConfirmClearColourView, "wait", cancel)
    message, roles, reply = colour_setup(OWNER)
    run(nova.handle_clear_role_colour(message, ""))
    assert all(not r.edits for r in roles)


def test_clear_colour_not_registered_as_slash(nova):
    assert not any("colour" in c.name or "color" in c.name for c in nova.client.tree.get_commands())


# ------------------------------------------------------------------ ATLAS voice: reconnect vs moderator
@pytest.fixture(scope="module")
def atlas():
    import bot as k
    k.bot._connection.user = types.SimpleNamespace(id=999)
    return k


def voice_event(k, monkeypatch, verdict):
    async def fake_classify(client, guild, wait=0):
        return verdict
    monkeypatch.setattr(k, "classify_self_disconnect", fake_classify)
    sent = Sink()
    general = types.SimpleNamespace(name="general", send=sent)
    guild = types.SimpleNamespace(id=777, name="Test", text_channels=[general], system_channel=None)
    k.config.setdefault("voice_sessions", {})["777"] = {"channel_id": "1", "paused": False}
    member = types.SimpleNamespace(id=999, guild=guild)
    before, after = types.SimpleNamespace(channel=types.SimpleNamespace(id=1)), types.SimpleNamespace(channel=None)
    run(k.on_voice_state_update(member, before, after))
    return k.config["voice_sessions"]["777"], sent


def test_atlas_reconnect_does_not_pause(atlas, monkeypatch):
    for verdict in ("transient", "unknown"):
        session, sent = voice_event(atlas, monkeypatch, verdict)
        assert session["paused"] is False and not sent.sent


def test_atlas_moderator_disconnect_pauses(atlas, monkeypatch):
    session, sent = voice_event(atlas, monkeypatch, "moderator")
    assert session["paused"] is True and session["removal_evidence"] == "moderator" and len(sent.sent) == 1


def test_atlas_voice_isolated_to_own_account(atlas, monkeypatch):
    called = []

    async def fake_classify(*a, **k):
        called.append(1)
        return "moderator"
    monkeypatch.setattr(atlas, "classify_self_disconnect", fake_classify)
    other_bot = types.SimpleNamespace(id=555, guild=types.SimpleNamespace(id=777))   # e.g. NOVA leaving voice
    run(atlas.on_voice_state_update(other_bot, types.SimpleNamespace(channel=1), types.SimpleNamespace(channel=None)))
    assert not called


def test_nova_reconnect_does_not_pause(nova, monkeypatch):
    async def fake_classify(client, guild, wait=0):
        return "transient"
    monkeypatch.setattr(nova, "classify_self_disconnect", fake_classify)
    nova.client._connection.user = types.SimpleNamespace(id=888)
    guild = types.SimpleNamespace(id=4242, name="Test", text_channels=[], system_channel=None)
    nova.voice_state["4242"] = {"channel_id": "1", "paused": False}
    run(nova.on_voice_state_update(types.SimpleNamespace(id=888, guild=guild), types.SimpleNamespace(channel=types.SimpleNamespace(id=1)), types.SimpleNamespace(channel=None)))
    assert nova.voice_state["4242"]["paused"] is False


# ------------------------------------------------------------------ polls & role menus with a real DB
def fake_bot(tmp_path):
    from common.db import Database
    from common.config import Registry
    b = types.SimpleNamespace(add_dynamic_items=lambda *a: None, dispatch=lambda *a, **k: None, intents=types.SimpleNamespace(members=True))
    b.db = Database(tmp_path / "x.db", tmp_path / "bk")
    b.registry = Registry(b, b.db)
    return b


def fake_interaction(user_id, guild):
    resp = types.SimpleNamespace(edit_message=Sink(), send_message=Sink(), defer=Sink(), is_done=lambda: False)
    return types.SimpleNamespace(user=types.SimpleNamespace(id=user_id), guild_id=guild.id, guild=guild, response=resp, followup=types.SimpleNamespace(send=Sink()))


def test_poll_votes_change_and_dedupe(tmp_path):
    from atlas_ext.polls import Polls
    b = fake_bot(tmp_path)
    cog = Polls(b)
    guild = types.SimpleNamespace(id=5)
    pid = b.db.insert("INSERT INTO polls(guild_id, author_id, question, options, created_ts) VALUES (5, 1, 'Q', ?, 0)", (json.dumps(["A", "B", "C"]),))

    async def flow():
        await cog.vote(fake_interaction(10, guild), pid, 0)
        await cog.vote(fake_interaction(10, guild), pid, 0)          # same option again -> removed
        assert cog.counts(cog.get(pid))[1] == 0
        await cog.vote(fake_interaction(10, guild), pid, 0)
        await cog.vote(fake_interaction(10, guild), pid, 2)          # change vote (single choice)
        counts, voters = cog.counts(cog.get(pid))
        assert counts == {2: 1} and voters == 1
        b.db.execute("UPDATE polls SET status='closed' WHERE id=?", (pid,))
        i = fake_interaction(11, guild)
        await cog.vote(i, pid, 1)
        assert "closed" in i.response.send_message.sent[0][0][0]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(discord.ui.View, "__init__", lambda self, timeout=None: None)
        mp.setattr(discord.ui.View, "add_item", lambda self, item: None)
        mp.setattr("atlas_ext.polls.PollButton.__init__", lambda self, *a, **k: None)
        run(flow())


class FakeMember:
    def __init__(self, uid, roles):
        self.id, self.roles, self.mention, self.guild = uid, list(roles), f"<@{uid}>", None

    async def add_roles(self, *roles, reason=None):
        self.roles.extend(roles)

    async def remove_roles(self, *roles, reason=None):
        self.roles = [r for r in self.roles if r not in roles]


def test_role_menu_unique_mode_and_staff_role_block(tmp_path):
    from nova_ext.roles import Roles
    b = fake_bot(tmp_path)
    cog = Roles(b)
    top = FakeRole("NOVA", 10)
    red, blue = FakeRole("Red", 2), FakeRole("Blue", 3)
    mod = FakeRole("Mods", 4, perms=discord.Permissions(ban_members=True).value)
    me = types.SimpleNamespace(top_role=top, guild_permissions=types.SimpleNamespace(manage_roles=True))
    roles = {r.id: r for r in (red, blue, mod)}
    guild = types.SimpleNamespace(id=9, me=me, get_role=roles.get, get_channel=lambda i: None)
    mid = b.db.insert("INSERT INTO role_menus(guild_id, title, mode, created_ts) VALUES (9, 'Colours', 'unique', 0)")
    for r in (red, blue, mod):
        b.db.execute("INSERT INTO role_menu_items(menu_id, role_id) VALUES (?,?)", (mid, r.id))
    member = FakeMember(50, [])
    member.guild = guild
    i = fake_interaction(50, guild)
    i.user = member

    async def flow():
        await cog.menu_pick(i, mid, [red.id])
        assert red in member.roles
        cog.last_pick.clear()
        await cog.menu_pick(i, mid, [blue.id])                     # unique: swaps red -> blue
        assert blue in member.roles and red not in member.roles
        cog.last_pick.clear()
        await cog.menu_pick(i, mid, [mod.id])                      # staff-permission role is refused
        assert mod not in member.roles
        msg = i.followup.send.sent[-1][0][0]
        assert "staff permissions" in msg
        hist = b.db.fetchall("SELECT action FROM role_history WHERE user_id=50")
        assert [h["action"] for h in hist] == ["add", "remove", "add"]
    run(flow())


def test_ai_approval_file_identical():
    a = hashlib.sha256((SRC / "ai_approval.py").read_bytes()).hexdigest()
    assert a == "e83f980d9ed9261c01307f438e889d5be7558fa864daaed68edd2ed6b2ee799a"  # sha256 of the original ai_approval.py


def test_nova_legacy_commands_still_dispatched():
    src = (SRC / "nova.py").read_text()
    for name in ("pfp", "mjoin", "mleave", "mtts", "mttsdisable", "colours", "colors", "mclearrolecolour", "mclearrolecolor"):
        assert f'("{name}", handle_' in src
