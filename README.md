<div align="center">

# ATLAS & NOVA

**Two modular Discord bots for community management, role automation, voice and analytics.**

### Created by RISERNOTFOUND

`Python 3.11+` · `discord.py 2.7` · `MIT License`

</div>

## What is this?
Two independent bots that run side by side (separate programs, tokens and databases):

| | **ATLAS** | **NOVA** |
|---|---|---|
| Role | Community hub | Roles, channels & voice |
| Highlights | Moderation, automod, verification, tickets, suggestions, polls, events, reminders, engagement, statistics, games, creator feeds (RSS/YouTube/Twitch), logging, starboard, rules, embed builder | Role menus, reaction roles, temporary roles, bulk role tools, role/hierarchy diagnostics, colour roles, temporary voice channels, channel & message tools |
| Voice | 24/7 presence + TTS (Piper) | 24/7 presence + Hindi/English TTS |
| Optional | Local AI via LM Studio | — |
| Start | `start_atlas.bat` / `python bot.py` | `start_nova.bat` / `python nova.py` |
| Settings | `!config` | `!mconfig` |

Full command list with syntax and permissions: **[FEATURES.md](FEATURES.md)** (320 entries extracted from the source).

## Quick start (Windows)
1. Install Python 3.11+. 2. Copy `.env.example` → `.env`, add your Discord user ID. 3. Run `setup.bat`. 4. Run `start_atlas.bat` and `start_nova.bat`, paste each bot token when asked.
Details, Discord Developer Portal setup, intents and permissions: **[docs/INSTALLATION.md](docs/INSTALLATION.md)**. Linux/macOS: best effort, same doc.

## Project layout
The flat layout is intentional so existing imports and data files keep working.
```
bot.py, atlas_stats*.py, atlas_ext/   → ATLAS        nova.py, nova_ext/ → NOVA
common/  shared code     config.py  settings     scripts/  helper tools
tests/   test suite      docs/      guides + audits
```
(Internal names keep the historical `atlas_`/`nova_` spelling; user-facing names are ATLAS/NOVA.)

## Bot Prefixes & NOVA TTS
- **ATLAS**: `!`, `?`, `.`
- **NOVA**: `!` (configurable via `NOVA_PREFIXES` in `.env`, commands start with `!m...`)
  - `!mhelp` — Show NOVA voice & TTS commands
  - `!msettts [#channel]` — Set the text channel NOVA reads TTS messages from
  - `!mdemo` — Test NOVA's complete TTS pipeline in the connected voice channel
  - `!mvstatus`, `!mvdiag`, `!mqueue`, `!mskip`, `!mstop`, `!mpause`, `!mresume`, `!mvolume`

## Docs
[Installation](docs/INSTALLATION.md) · [Configuration](docs/CONFIGURATION.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Privacy](docs/PRIVACY.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Release report](docs/DELIVERY_REPORT.md)

## Known limitations
- **TTS: unverified.** The maintainer reports an outstanding TTS issue in both bots. One cause (ATLAS skipping messages that start with `k`, `?` or `.`) was fixed and unit-tested, but Piper synthesis and Discord voice playback were **not** verified end to end for this release. See [Troubleshooting](docs/TROUBLESHOOTING.md).
- Most features were tested by the maintainer live; only part of the automated suite could be run for this release (see the test report).
- No screenshots are included.
- Windows is the primary platform; the AI approval window is Windows-only.
- DM forwarding is **off by default** (see [docs/PRIVACY.md](docs/PRIVACY.md)); `!visibility` and optional local AI also handle member data.

## Credits
- Created by RISERNOTFOUND.
- Developed with AI-assisted coding tools.
- Project direction, integration, debugging and testing by RISERNOTFOUND.

## License
MIT © 2026 RISERNOTFOUND. This repository does not include Piper (GPL-3.0); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistributing builds that bundle it.
