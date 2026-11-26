# Contributing

Thanks for helping improve ATLAS & NOVA (built by RISERNOTFOUND).

1. Fork, create a venv, `pip install -r requirements.txt pytest pytest-asyncio`.
2. Run tests: `pytest -q tests` (they work on a temporary copy; your data is never touched).
3. Keep changes small; don't rename internal modules (`atlas_ext`, `nova_ext`, DB/file names) without a migration.
4. Never include real IDs, tokens or server data. Run `python scripts/scan_secrets.py`.
5. Update `FEATURES.md` (`python scripts/command_inventory.py` lists commands) and `CHANGELOG.md`.
