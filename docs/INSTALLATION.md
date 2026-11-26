# Installation

## Windows
1. Install Python 3.11+ (tick "Add to PATH").
2. Copy `.env.example` to `.env` and fill in your Discord user ID (`BOT_OWNER_IDS`, `MODERATION_OWNER_ID`).
3. Double-click `setup.bat` (creates `.venv`, installs packages, downloads FFmpeg and the voice once).
4. `start_atlas.bat` for ATLAS, `start_nova.bat` for NOVA (separate windows). On first start each asks for its token (saved to `token.txt` / `token2.txt`) or reads `DISCORD_BOT_TOKEN` / `DISCORD_BOT_TOKEN_2` from `.env`.
5. Check anything with `python scripts/check_setup.py --tts`.

## Linux / macOS (best effort, not tested by the maintainer)
```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg        # or brew install ffmpeg
python install_assets.py       # Piper voice
python bot.py                  # ATLAS     |   python nova.py   # NOVA
```
The separate AI-approval window used by `!ai` actions is Windows-only.

## Discord Developer Portal
Create **two applications** (one per bot). Bot tab → enable **Message Content Intent** (both), **Server Members Intent** (both; ATLAS requires it), **Presence Intent** (ATLAS, optional: game stats). Invite with scopes `bot` + `applications.commands`. Suggested permissions: View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Add Reactions, Manage Roles, Manage Channels, Manage Messages, Manage Threads, Moderate Members, Kick/Ban Members, Connect, Speak, Move Members, View Audit Log. Place each bot's top role above roles it must manage.
