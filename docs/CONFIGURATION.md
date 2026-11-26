# Configuration

**Environment / `.env`** (see `.env.example`): `DISCORD_BOT_TOKEN`, `DISCORD_BOT_TOKEN_2`, `BOT_OWNER_IDS`, `MODERATION_OWNER_ID`, `DM_FORWARD_CHANNEL_ID`, `PRESERVED_CHANNEL_ID`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `COMMUNITY_TIMEZONE`. Empty = feature off (e.g. no owner ID → `!kick`/`!ban`/`!mclearrolecolour` unusable).

**`config.py`:** prefixes, TTS rate/limits, LM Studio endpoint/model (`AI_BASE_URL`, `AI_MODEL`), assignable AI roles.

**In Discord:** ATLAS `!config [module]`, NOVA `!mconfig [module]` (or `/config`) — every module is toggled independently. `!configexport` / `!configimport` / `!configbackups` manage settings. Option keys per module are listed in FEATURES.md.

**Runtime files (git-ignored):** `config.json`, `void_data.json`, `atlas_stats.json`, `atlas.db`, `nova.db`, `nova_state.json`, `nova_tts.json`, `nova_colours.json`, `backups/`, `logs/`. Names are historical; renaming would orphan existing data.
