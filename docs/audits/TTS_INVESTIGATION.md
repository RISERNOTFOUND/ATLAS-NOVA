# TTS investigation

**Symptom was not described**, so the whole path was read statically. Path: command (`!join`, `!ttschannel`, `!mjoin`, `!mtts`) → permission checks → `connect_to` → `speech_text` → worker queue → `synthesize_speech`/`synthesize` (Piper `PiperVoice.load` + `synthesize_wav`) → `discord.FFmpegOpusAudio` → `voice.play` → cleanup (finally block deletes the WAV).

**Verified root cause of one defect (ATLAS, by code reading + unit test):** the gate `is_command = startswith(any prefix)` used default prefixes `! k K ? .`, so any TTS-channel message beginning with those characters ("kya haal hai", "kal milte hain", "?what") was silently dropped. Fixed in `common/cmdmatch.py` + one line in `bot.py`; 5 unit tests pass.
**Verified here:** FFmpeg Opus encode with discord.py-equivalent arguments works (ffmpeg 6.1.1, sandbox).
**Not verified (no discord.py, Piper or Discord in the sandbox):** Piper synthesis on your machine, voice playback, reconnect behaviour. The code paths for these looked consistent; no change was made. NOVA's gate was not changed (it only skips exact commands).
**Observation:** every TTS exception posts "Run setup.bat…", which is misleading; check the console. `scripts/check_setup.py --tts` tests each stage separately.
**Status:** not claimed fixed. Please send the exact symptom + console output.

## Remaining verification limitation
Piper synthesis on a real machine and Discord voice playback (both bots) were **not** executed or verified by this release. Until the maintainer confirms speech end to end, TTS is documented as **unverified / known issue** everywhere. To verify: `python scripts/check_setup.py --tts`, then `!join` + a TTS-channel message (ATLAS) and `!mjoin` + `!mtts` (NOVA); report the first failing stage.
