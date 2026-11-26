"""Tests always run on a TEMPORARY COPY of the project, because importing bot.py/nova.py runs the
one-time data migrations. Your real config.json, void_data.json and databases are never touched.

Run from the project folder:   .venv\\Scripts\\python.exe -m pip install pytest
                                .venv\\Scripts\\python.exe -m pytest -q tests
"""
import os
import shutil
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
_tmp = Path(tempfile.mkdtemp(prefix="nova-atlas-tests-"))
_copy = _tmp / "project"
shutil.copytree(PROJECT, _copy, ignore=shutil.ignore_patterns(".venv", "tests", "__pycache__", "backups", "logs", "*.db*",
                                                              "ffmpeg", "tts_models", "nova_voices", "nova_libs", "atlas_libs", ".env", "token*.txt"))
os.environ["SRC"] = str(_copy)
os.environ["MODERATION_OWNER_ID"] = "111111111111111111"  # fake owner for the owner-only tests
