import asyncio, os, sys, time, types
from pathlib import Path
SRC = Path(os.environ["SRC"]); os.chdir(SRC); sys.path.insert(0, str(SRC))
import atlas_stats_core as core
from atlas_ext.statsx import StatsExtension, window_sum, trend

def test_stats_extension_reports(tmp_path):
    from common.db import Database
    from common.config import Registry
    store = core.StatStore(tmp_path / "atlas_stats.json")          # empty/missing file -> fresh store, never crashes
    now = time.time()
    for h in range(0, 24 * 20, 3):
        store.add_message(1, 11, 500, ts=now - h * 3600)
    store.add_message(1, 12, 501, is_reply=True, ts=now - 3600)
    store.add_voice(1, 11, 900, 1800, True)
    async def make_chart(func, *args):
        png = func(*args)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        return None, None
    stats_cog = types.SimpleNamespace(store=store, charts_ok=True, make_chart=make_chart,
                                      who=lambda g, u: f"user{u}", who_plain=lambda g, u: f"user{u}", chan=lambda g, c: f"#{c}")
    bot = types.SimpleNamespace(add_dynamic_items=lambda *a: None, dispatch=lambda *a, **k: None, get_cog=lambda n: stats_cog if n == "StatsCog" else None)
    bot.db = Database(tmp_path / "k.db", tmp_path / "bk"); bot.registry = Registry(bot, bot.db)
    cog = StatsExtension(bot)
    guild = types.SimpleNamespace(id=1, name="G")
    bot.db.insert("INSERT INTO member_flow(guild_id,user_id,kind,ts,is_bot) VALUES (1,12,'join',?,0)", (now - 3600,))
    async def go():
        for period in ("24h", "7d", "30d", "all"):
            e, f = await cog.overview_embed(guild, period)
            assert "Tracked since" in e.footer.text and "can't be recovered" in e.footer.text
        embeds, _ = await cog.build_report(guild, "7d")
        text = " ".join(f.value for f in embeds[1].fields)
        assert "1 of 1 newcomers" in text
    asyncio.run(go())
    users = store.guild(1)["users"]
    total, active = window_sum(users, core.hour_key(now - 7 * 86400))
    assert total["m"] == len([h for h in range(0, 24 * 7 + 1, 3)]) + 1 and active == {"11", "12"}
    assert trend(10, 5) == "▲ 100%" and trend(0, 0) == "—"
    from atlas_ext.statsx import flow_chart
    assert flow_chart(["1", "2"], [1, 0], [0, 1])[:4] == b"\x89PNG"
