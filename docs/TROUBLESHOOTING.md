# Troubleshooting

Run `python scripts/check_setup.py --tts` first; in Discord use `!diag`, `!vdiag` (ATLAS) / `!mdiag`, `!mvdiag` (NOVA).

**Voice errors "Disconnected from voice… Reconnecting" + a long traceback, "Shard has stopped responding"**: a network blip. discord.py reconnects by itself; both bots log it at ERROR level. Only worry if it never recovers: use a stable connection, disable VPN/sleep.

**`Local AI error: WinError 10061`**: LM Studio is not running. Start its server (Developer tab) and load the model named in `AI_MODEL`, or run `!aioff`.

## TTS (known issue)
Affected components, in order: (1) message gate in `bot.py` (fixed: sentences starting with `k`, `?`, `.` were skipped), (2) Piper model/engine, (3) FFmpeg Opus encode, (4) Discord voice connection.
- Nothing is spoken for words like "kya…": update to this release.
- `⚠️ TTS failed` message: any TTS exception shows it (not only a missing model). Check the console for `TTS error:`; run `python scripts/check_setup.py --tts` to see which stage fails.
- Silent but no error: bot not in voice (`!join`), wrong text channel (`!ttschannel`), no Speak permission, volume (`!volume`), or the voice link is down (`!vdiag`).
- NOVA: `!mtts`, `!mjoin`; first use downloads voices (needs internet).
Please report the exact symptom and console output in an issue.

**Intents errors / NOVA restarts once**: enable Server Members Intent, delete `.nova_no_members`. **Commands ignored**: Message Content Intent off, or wrong prefix (`!`). **`!kick`/`!ban` say unauthorized**: set `MODERATION_OWNER_ID`.
