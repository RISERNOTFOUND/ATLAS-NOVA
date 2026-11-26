#!/usr/bin/env python3
"""Pre-publish scan for secrets and Discord IDs.  Run from the project folder:  python scripts/scan_secrets.py

Flags: Discord bot-token shapes, webhook URLs, generic API-key assignments, and 17-20 digit numbers (Discord IDs).
Exit code 1 when something is found. Review each hit; test fixtures with obviously fake IDs are expected to be listed.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "backups", "logs", "ffmpeg", "tts_models", "nova_voices", "nova_libs", "atlas_libs"}
SKIP_FILES = {"scan_secrets.py"}
PATTERNS = {
    "discord token": re.compile(r"[MNO][A-Za-z0-9_-]{23,25}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}"),
    "webhook url": re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"),
    "api key": re.compile(r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    "discord id (17-20 digits)": re.compile(r"(?<!\d)\d{17,20}(?!\d)"),
}
hits = 0
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or path.name in SKIP_FILES or SKIP_DIRS & set(path.relative_to(ROOT).parts):
        continue
    if path.suffix.lower() in {".png", ".jpg", ".zip", ".db", ".onnx", ".wav", ".pyc", ".exe"}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for number, line in enumerate(text.splitlines(), 1):
        for label, rx in PATTERNS.items():
            if rx.search(line):
                hits += 1
                print(f"{path.relative_to(ROOT)}:{number}: {label}")
print(f"\n{hits} potential finding(s).")
sys.exit(1 if hits else 0)
