# Test report (2026-11-26)

Environment: Linux sandbox, Python 3.12, no internet; discord.py, pytest and Piper not installed.

**Suite: 34 tests** = 29 existing (test_flows 13, test_logic 15, test_stats 1) + 5 new (test_tts_gate 5).
**Executed: 9, passed: 9, failed: 0. Not run: 25** (they import discord.py).

| Executed | Count | Result |
|---|---|---|
| test_logic: durations, parse_when, timezones, db migrate/backup/claim | 4 of 15 | PASS (test bodies run via extraction; `pytest.raises` shimmed) |
| test_tts_gate (new) | 5 of 5 | PASS |

| Not run | Count |
|---|---|
| test_flows | 13 |
| test_logic (the other tests) | 11 |
| test_stats | 1 |

Other checks: `compileall` PASS · AST undefined-name/import check (56 files) PASS · `scripts/verify_inventory.py` PASS (317 declarations = inventory) · private-data scan of the final ZIP: see PRIVACY_AUDIT · real bot startup: NOT RUN.
Two migration tests that needed a private backup folder were removed (not failing). Run `pip install pytest pytest-asyncio && pytest -q tests` before publishing; expected 34 collected.
