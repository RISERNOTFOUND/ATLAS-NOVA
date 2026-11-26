#!/usr/bin/env python3
"""ATLAS & NOVA setup / TTS self-check.   Run from the project folder:   python scripts/check_setup.py [--tts]

Checks, in order: Python version, required packages, FFmpeg, token/.env presence (never prints secrets), and with
--tts the whole speech pipeline stage by stage (Piper model -> WAV synthesis -> FFmpeg Opus encode). The first
stage that fails tells you exactly which component to fix. Nothing here connects to Discord.
"""
import importlib
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
problems = []


def ok(msg):
    print(f"  [ OK ] {msg}")


def bad(msg, hint=""):
    problems.append(msg)
    print(f"  [FAIL] {msg}" + (f"\n         -> {hint}" if hint else ""))


def warn(msg):
    print(f"  [WARN] {msg}")


print("1) Python")
if sys.version_info >= (3, 11):
    ok(f"Python {sys.version.split()[0]}")
else:
    bad(f"Python {sys.version.split()[0]} is too old", "Install Python 3.11 or newer.")

print("2) Packages")
for module, pip_name in (("discord", "discord.py[voice]"), ("nacl", "PyNaCl (comes with discord.py[voice])"),
                         ("davey", "davey (comes with discord.py[voice])"), ("piper", "piper-tts"), ("tzdata", "tzdata")):
    try:
        mod = importlib.import_module(module)
        ok(f"{module} {getattr(mod, '__version__', '')}".strip())
    except Exception as exc:  # noqa: BLE001 - we want the reason, whatever it is
        bad(f"{module} cannot be imported ({type(exc).__name__}: {exc})", f"pip install -r requirements.txt   (needs {pip_name})")

print("3) Configuration")
try:
    import config
    ok("config.py loaded")
except Exception as exc:  # noqa: BLE001
    config = None
    bad(f"config.py failed to load: {exc}")
if config:
    for label, value in (("MODERATION_OWNER_ID", config.MODERATION_OWNER_ID), ("BOT_OWNER_IDS", config.BOT_OWNER_IDS)):
        (ok if value else warn)(f"{label} is {'set' if value else 'NOT set (owner-only commands stay disabled)'}")
    for label, env_name, file_name in (("ATLAS token", "DISCORD_BOT_TOKEN", "token.txt"), ("NOVA token", "DISCORD_BOT_TOKEN_2", "token2.txt")):
        have = bool(os.environ.get(env_name, "").strip()) or (ROOT / file_name).is_file()
        (ok if have else warn)(f"{label}: {'found' if have else f'not found - the bot will ask for it on first start (or set {env_name})'}")

print("4) FFmpeg")
ffmpeg = str(config.FFMPEG_PATH) if config else "ffmpeg"
try:
    out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=20)
    ok(out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else f"ffmpeg exited with {out.returncode}")
    if out.returncode != 0:
        bad("FFmpeg did not run correctly", "Re-run setup.bat (Windows) or install ffmpeg with your package manager.")
except (OSError, subprocess.SubprocessError) as exc:
    bad(f"FFmpeg not runnable at '{ffmpeg}' ({type(exc).__name__})", "Run setup.bat (Windows) or `sudo apt install ffmpeg` (Linux).")

if "--tts" in sys.argv:
    print("5) TTS pipeline (offline, no Discord)")
    model = config.TTS_MODEL_PATH if config else None
    if not model or not Path(model).is_file() or not Path(str(model) + ".json").is_file():
        bad("ATLAS voice model files are missing", "Run: python install_assets.py   (needs internet once)")
    else:
        ok(f"voice model present: {Path(model).name}")
        try:
            from piper import PiperVoice, SynthesisConfig
            voice = PiperVoice.load(str(model))
            ok("PiperVoice.load succeeded")
            wav_path = Path(tempfile.gettempdir()) / "atlas_nova_tts_check.wav"
            with wave.open(str(wav_path), "wb") as handle:
                voice.synthesize_wav("This is a text to speech test.", handle,
                                     syn_config=SynthesisConfig(length_scale=max(0.5, min(2.0, 175 / config.TTS_RATE))))
            with wave.open(str(wav_path), "rb") as handle:
                seconds = handle.getnframes() / float(handle.getframerate() or 1)
            if seconds < 0.3:
                bad(f"synthesized audio is only {seconds:.2f}s long", "Piper produced (almost) no audio; reinstall piper-tts and the model.")
            else:
                ok(f"synthesized {seconds:.1f}s of audio")
                opus_path = wav_path.with_suffix(".opus")
                proc = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav_path), "-vn", "-map_metadata", "-1",
                                       "-f", "opus", "-c:a", "libopus", "-ar", "48000", "-ac", "2", str(opus_path)],
                                      capture_output=True, text=True, timeout=60)
                if proc.returncode == 0 and opus_path.is_file() and opus_path.stat().st_size > 200:
                    ok("FFmpeg encoded the speech to Opus (this is the format Discord playback needs)")
                else:
                    bad(f"FFmpeg could not encode to Opus: {proc.stderr.strip()[:200]}", "Use a full FFmpeg build that includes libopus.")
                opus_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            bad(f"speech synthesis failed ({type(exc).__name__}: {exc})",
                "This is the Piper stage: check the piper-tts version in requirements.txt and re-run install_assets.py.")
    print("   (Playback inside Discord also needs Connect + Speak permission and a working voice connection: use !vdiag / !mvdiag.)")

print()
if problems:
    print(f"{len(problems)} problem(s) found - fix the first FAIL above and run again.")
    sys.exit(1)
print("All checks passed." + ("" if "--tts" in sys.argv else "  Add --tts to test the speech pipeline."))
