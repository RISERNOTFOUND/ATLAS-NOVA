# Changelog

## 1.0.0 — first public release (2026-11-26) — built by RISERNOTFOUND
- Rebranded publicly: first bot **ATLAS**, second bot **NOVA**. Internal module/file/DB names unchanged for compatibility; command names unchanged.
- Removed all private server data, IDs and server-specific layout; personal values now come from `.env` (`BOT_OWNER_IDS`, `MODERATION_OWNER_ID`, `DM_FORWARD_CHANNEL_ID`, `PRESERVED_CHANNEL_ID`, `COMMUNITY_TIMEZONE`).
- `!rebuild` is disabled unless `PRESERVED_CHANNEL_ID` is set; its layout is now generic.
- TTS: ATLAS no longer silently skips messages that start with a letter prefix such as `k` ("kya", "kal") unless they are a real command (`common/cmdmatch.py`, unit-tested). End-to-end speech not verified by the release process.
- FFmpeg falls back to the system `ffmpeg` (Linux/macOS). Config exports from before the rename still import.
- NOVA's TTS channel is now named `nova-tts` (new installs); log files are `atlas`/`nova`.
- `!dmforwardtest` now says DM forwarding is OFF when unconfigured. Credits simplified.
- Added `scripts/verify_inventory.py`, `scripts/check_setup.py`, `scripts/scan_secrets.py`, `scripts/command_inventory.py`, docs and audit reports.
