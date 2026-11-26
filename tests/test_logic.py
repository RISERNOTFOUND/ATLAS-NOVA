"""Pure-logic and fake-object tests. Run from a scratch copy: pytest -q tests/ --rootdir <copy> (SRC env var)."""
import asyncio
import json
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

SRC = Path(os.environ["SRC"])
sys.path.insert(0, str(SRC))

from common import db as dbmod, timeparse, voice as voicemod, checks  # noqa: E402
from common.config import Module, Option, Registry  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ timeparse
def test_durations():
    assert timeparse.parse_duration("10") == timedelta(minutes=10)
    assert timeparse.parse_duration("1h30m") == timedelta(minutes=90)
    assert timeparse.parse_duration("2d") == timedelta(days=2)
    assert timeparse.parse_duration("abc") is None
    assert timeparse.parse_duration("5m extra") is None
    assert timeparse.parse_duration("30s", minimum=60) is None


def test_parse_when_variants():
    tz = timeparse.get_tz("Asia/Kolkata")
    now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)  # 15:30 IST
    assert timeparse.parse_when("in 2h", tz, now) == now + timedelta(hours=2)
    t = timeparse.parse_when("18:00", tz, now).astimezone(tz)
    assert (t.day, t.hour) == (24, 18)
    t = timeparse.parse_when("09:00", tz, now).astimezone(tz)          # already past today -> tomorrow
    assert (t.day, t.hour) == (25, 9)
    t = timeparse.parse_when("tomorrow 9am", tz, now).astimezone(tz)
    assert (t.day, t.hour) == (25, 9)
    t = timeparse.parse_when("2026-12-31 20:00", tz, now).astimezone(tz)
    assert (t.month, t.day, t.hour) == (12, 31, 20)
    with pytest.raises(ValueError):
        timeparse.parse_when("today 9am", tz, now)                     # explicit past -> error, not silently tomorrow
    with pytest.raises(ValueError):
        timeparse.parse_when("2020-01-01 10:00", tz, now)


def test_timezones():
    assert timeparse.tz_name(timeparse.get_tz("ist")) == "Asia/Kolkata"
    assert timeparse.get_tz("+05:30").utcoffset(None) == timedelta(hours=5, minutes=30)
    with pytest.raises(ValueError):
        timeparse.get_tz("Mars/Olympus")


# ------------------------------------------------------------------ database
def test_db_migrate_backup_claim_isolation(tmp_path):
    d = dbmod.Database(tmp_path / "t.db", tmp_path / "b")
    assert d.migrate("m1", "CREATE TABLE x (id INTEGER PRIMARY KEY, status TEXT)")
    assert not d.migrate("m1", "CREATE TABLE x (id INTEGER PRIMARY KEY)")      # applied once only
    d.migrate("m2", "CREATE TABLE y (a INTEGER)")                              # takes a validated backup first
    assert len(d.list_backups()) == 1
    rid = d.insert("INSERT INTO x(status) VALUES ('pending')")
    assert d.claim("x", rid) is True
    assert d.claim("x", rid) is False                                          # second deliverer loses -> no duplicates
    d.set_setting(1, "mod", "enabled", True)
    d.set_setting(2, "mod", "enabled", False)
    assert d.get_settings(1, "mod")["enabled"] is True and d.get_settings(2, "mod")["enabled"] is False
    with pytest.raises(RuntimeError):
        with d.transaction() as conn:
            conn.execute("INSERT INTO y VALUES (1)")
            raise RuntimeError("boom")
    assert d.scalar("SELECT count(*) FROM y") == 0                            # rolled back


def test_registry_modules_independent(tmp_path):
    d = dbmod.Database(tmp_path / "t.db", tmp_path / "b")
    bot = types.SimpleNamespace(dispatch=lambda *a, **k: None)
    r = Registry(bot, d)
    r.register(Module("a", "A", "a", "", [Option("x", "X", "int", 5)], default_enabled=True))
    r.register(Module("b", "B", "b", "", [], default_enabled=True))
    g = types.SimpleNamespace(id=42)
    r.set(g, None, "a", "x", 9)
    r.set(g, None, "a", "enabled", False)
    assert r.enabled(42, "b") and not r.enabled(42, "a")
    assert r.get(42, "a")["x"] == 9 and r.get(43, "a")["x"] == 5               # other guild untouched


# ------------------------------------------------------------------ automod
def test_automod_rules():
    from atlas_ext import automod
    assert automod.word_hit("you are a b4dword!", ["badword"]) == "badword"
    assert automod.word_hit("classic assessment", ["ass"]) is None             # no substring false positive for short words
    cog = automod.AutoMod.__new__(automod.AutoMod)
    cog.recent = automod.defaultdict(lambda: automod.deque(maxlen=30))
    cog.words = lambda gid: ["slur"]
    s = {"blocked_words": True, "mentions": True, "mention_limit": 5, "invites": True, "links": True,
         "allowed_domains": "youtube.com", "spam": True, "spam_count": 4, "spam_seconds": 8, "duplicates": True, "duplicate_count": 3}
    def msg(text, uid=1, mentions=0):
        return types.SimpleNamespace(content=text, guild=types.SimpleNamespace(id=1), author=types.SimpleNamespace(id=uid),
                                     raw_mentions=list(range(mentions)), raw_role_mentions=[], mention_everyone=False)
    assert cog.check(msg("hello friend", uid=10), s) is None
    assert cog.check(msg("watch https://youtube.com/x", uid=11), s) is None
    assert cog.check(msg("visit https://evil.example/x", uid=12), s)[0] == "link"
    assert cog.check(msg("join discord.gg/abcdef", uid=13), s)[0] == "invite link"
    assert cog.check(msg("hi", uid=14, mentions=6), s)[0] == "mention spam"
    r = [cog.check(msg(f"line {i}", uid=15), s) for i in range(4)]
    assert r[-1][0] == "spam" and r[0] is None


# ------------------------------------------------------------------ games
def test_tictactoe_and_connect4_logic():
    from atlas_ext import games

    async def go():
        p1, p2 = types.SimpleNamespace(id=1, mention="a"), types.SimpleNamespace(id=2, mention="b")
        t = games.TicTacToe(None, 1, p1, p2)
        t.board = [0, 0, 0, None, 1, 1, None, None, None]
        assert t.winner() == 0
        t.board = [0, 1, 0, 0, 1, 1, 1, 0, 0]
        assert t.winner() == "draw"
        c = games.ConnectFour(None, 1, p1, p2)
        for r in range(2, 6):
            c.grid[r][3] = 1
        assert c.wins(2, 3, 1)
        c2 = games.ConnectFour(None, 1, p1, p2)
        for i in range(4):
            c2.grid[5 - i][i] = 0
        assert c2.wins(5, 0, 0)
    run(go())


# ------------------------------------------------------------------ announcements / feeds / feedback / reminders
def test_announcement_schedule_and_feeds():
    from atlas_ext import announce
    base = datetime(2026, 11, 24, 3, 0, tzinfo=timezone.utc).timestamp()      # 08:30 IST (Tuesday)
    row = {"kind": "daily", "at_time": "10:00", "tz": "Asia/Kolkata", "weekday": None, "interval_minutes": None, "next_ts": None}
    nxt = datetime.fromtimestamp(announce.next_run(row, base), timeparse.get_tz("Asia/Kolkata"))
    assert (nxt.day, nxt.hour, nxt.minute) == (24, 10, 0)
    row.update(kind="weekly", weekday="mon")
    nxt = datetime.fromtimestamp(announce.next_run(row, base), timeparse.get_tz("Asia/Kolkata"))
    assert nxt.weekday() == 0 and nxt.day == 30
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>yt:video:1</id><title>New vid</title><link href="https://y/1"/></entry></feed>"""
    assert announce.parse_feed(atom) == [("yt:video:1", "New vid", "https://y/1")]
    rss = "<rss><channel><item><guid>g1</guid><title>Post</title><link>https://b/1</link></item></channel></rss>"
    assert announce.parse_feed(rss) == [("g1", "Post", "https://b/1")]


def test_feedback_similarity_and_reminder_split():
    from atlas_ext import feedback, reminders
    assert feedback.similar("Add a music channel please", "please add a music channel") > 0.6
    assert feedback.similar("Add a music channel", "Host a movie night") < 0.5
    tz = timeparse.get_tz("UTC")
    due, rest = reminders.split_when("10 stretch your legs", tz)
    assert rest == "stretch your legs" and 590 < due.timestamp() - time.time() < 610
    due, rest = reminders.split_when("tomorrow 9am standup call", tz)
    assert rest == "standup call"
    with pytest.raises(ValueError):
        reminders.split_when("standup", tz)


# ------------------------------------------------------------------ voice disconnect classification
class FakeVoice:
    def __init__(self, connected):
        self._c = connected

    def is_connected(self):
        return self._c


def fake_guild(voice, audit_entries=None, audit_perm=True):
    async def audit_logs(**kwargs):
        for e in audit_entries or []:
            yield e
    me = types.SimpleNamespace(guild_permissions=types.SimpleNamespace(view_audit_log=audit_perm))
    return types.SimpleNamespace(voice_client=voice, me=me, audit_logs=audit_logs)


def fake_client(healthy=True):
    return types.SimpleNamespace(user=types.SimpleNamespace(id=999), latency=0.1 if healthy else float("inf"),
                                 is_ready=lambda: True, is_closed=lambda: False)


def test_classify_reconnect_is_transient():
    g = fake_guild(FakeVoice(True))
    assert run(voicemod.classify_self_disconnect(fake_client(), g, wait=0)) == "transient"


def test_classify_moderator_confirmed_by_audit_log():
    entry = types.SimpleNamespace(user=types.SimpleNamespace(id=5))
    g = fake_guild(None, [entry])
    assert run(voicemod.classify_self_disconnect(fake_client(), g, wait=0)) == "moderator"


def test_classify_no_audit_entry_is_transient():
    g = fake_guild(None, [])
    assert run(voicemod.classify_self_disconnect(fake_client(), g, wait=0)) == "transient"


def test_classify_without_audit_permission():
    assert run(voicemod.classify_self_disconnect(fake_client(), fake_guild(None, audit_perm=False), wait=0)) == "moderator-unconfirmed"
    assert run(voicemod.classify_self_disconnect(fake_client(), fake_guild(FakeVoice(False), audit_perm=False), wait=0)) == "unknown"
    assert run(voicemod.classify_self_disconnect(fake_client(healthy=False), fake_guild(None, audit_perm=False), wait=0)) == "transient"


def test_voice_health_backoff():
    h = voicemod.VoiceHealth()
    assert h.ready(1)
    assert h.fail(1, "x") == 30 and not h.ready(1)
    assert h.fail(1, "x") == 60
    h.ok(1)
    assert h.ready(1) and 1 not in h.failures


# ------------------------------------------------------------------ permission helpers
def test_role_problem_and_dangerous():
    perms = lambda **kw: types.SimpleNamespace(**{p: kw.get(p, False) for p in checks.DANGEROUS_PERMS})

    class R:
        def __init__(self, pos, managed=False, default=False, **p):
            self.position, self.managed, self._d, self.permissions = pos, managed, default, perms(**p)

        def is_default(self):
            return self._d

        def __ge__(self, o):
            return self.position >= o.position

    me = types.SimpleNamespace(top_role=R(10), guild_permissions=types.SimpleNamespace(manage_roles=True))
    assert checks.role_problem(R(5), me) is None
    assert "above" in checks.role_problem(R(10), me)
    assert "managed" in checks.role_problem(R(3, managed=True), me)
    assert checks.dangerous_perms(R(3, ban_members=True)) == ["ban_members"]
