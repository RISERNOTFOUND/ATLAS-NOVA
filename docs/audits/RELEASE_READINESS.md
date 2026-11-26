# Release readiness

| Area | State |
|---|---|
| Branding (ATLAS/NOVA, credit "Created by RISERNOTFOUND" in README, docs, help footer, colour-picker footer, repo description) | Done |
| Privacy/secrets | Clean per scan |
| Docs, license, policies | Done |
| Command inventory (320 entries from source) | Done |
| Tests | 34 in suite; 9 executed/passed, 25 not run (need discord.py). Run `pytest -q tests` locally first. |
| Feature inventory | Verified: `scripts/verify_inventory.py` PASS |
| DM forwarding | Off by default; documented in PRIVACY.md |
| Licence | MIT appropriate for source-only repo (Piper GPL-3.0 reviewed; see THIRD_PARTY_NOTICES) |
| TTS | Unverified end to end; documented as known issue |
| Bot startup | Not run here: start both bots once locally |
| Decisions for owner | MIT vs GPL-3.0 (Piper); whether DM forwarding stays; GitHub publication |
Publishing: **not done**. Awaiting your explicit approval; I have no GitHub access in this session.
