"""ATLAS & NOVA central configuration.

All server-specific values, feature toggles, and optional overrides are read from
environment variables or an optional `.env` file next to this file (copy `.env.example` to `.env`).
`.env` is git-ignored and must never be committed.
"""
import os
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from .env without overriding real environment variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    val = os.environ.get(name, "").strip()
    return val if val else default


def _int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw, 0)
    except ValueError:
        return default


def _bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if raw in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return default


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    items = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]
    return items or list(default)


def _id_set(name: str) -> set[int]:
    out: set[int] = set()
    for part in os.environ.get(name, "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


# ---- Branding & Attribution ----
CREDIT = "Created by RISERNOTFOUND."
REPO_NAME = "ATLAS-NOVA"
EMBED_COLOR = _int("EMBED_COLOR", 0x5865F2)

# ---- File Paths (with safe migration support) ----
TOKEN_FILE = BASE_DIR / "token.txt"
TOKEN2_FILE = BASE_DIR / "token2.txt"
LEGACY_DATA_FILE = BASE_DIR / "void_data.json"
DATA_FILE = BASE_DIR / "atlas_data.json"
if not DATA_FILE.exists() and LEGACY_DATA_FILE.exists():
    # Safe migration: preserve existing user data without deleting legacy file
    DATA_FILE.write_bytes(LEGACY_DATA_FILE.read_bytes())

ATLAS_LOG_FILE = BASE_DIR / "atlas.log"
NOVA_LOG_FILE = BASE_DIR / "nova.log"
LOG_FILE = ATLAS_LOG_FILE  # backward-compatible alias

# ---- Prefixes & Ownership ----
PREFIX = _str("ATLAS_PREFIX", "!")
DEFAULT_PREFIXES = _list("ATLAS_PREFIXES", ["!", "?", "."])
NOVA_PREFIXES = _list("NOVA_PREFIXES", ["!"])
OWNER_ID = _int("OWNER_ID", 0)
BOT_OWNER_IDS = _id_set("BOT_OWNER_IDS")
MODERATION_OWNER_ID = _int("MODERATION_OWNER_ID", 0)
SHUTDOWN_PHRASE = _str("SHUTDOWN_PHRASE", "bye good night")

# ---- Feature Toggles ----
FEATURE_VIBE = _bool("FEATURE_VIBE", True)
FEATURE_TTS = _bool("FEATURE_TTS", True)
FEATURE_TICKETS = _bool("FEATURE_TICKETS", True)
FEATURE_AUTOMOD = _bool("FEATURE_AUTOMOD", True)
FEATURE_TEMPVOICE = _bool("FEATURE_TEMPVOICE", True)
FEATURE_ANALYTICS = _bool("FEATURE_ANALYTICS", True)
FEATURE_ROLES = _bool("FEATURE_ROLES", True)
FEATURE_ONBOARDING = _bool("FEATURE_ONBOARDING", True)
FEATURE_AI = _bool("FEATURE_AI", True)
FEATURE_FEEDS = _bool("FEATURE_FEEDS", True)
FEATURE_LOGGING = _bool("FEATURE_LOGGING", True)
FEATURE_VOICE = _bool("FEATURE_VOICE", True)
FEATURE_ENGAGEMENT = _bool("FEATURE_ENGAGEMENT", True)
FEATURE_MODERATION = _bool("FEATURE_MODERATION", True)

# ---- Server-Specific Channel / Role / Category IDs (0 = disabled or auto-resolved) ----
VIBE_CHANNEL_ID = _int("VIBE_CHANNEL_ID", 0)
VIBE_CHANNEL_NAME = _str("VIBE_CHANNEL_NAME", "vibe-check")
DM_FORWARD_CHANNEL_ID = _int("DM_FORWARD_CHANNEL_ID", 0)
PRESERVED_CHANNEL_ID = _int("PRESERVED_CHANNEL_ID", 0)
LOG_CHANNEL_ID = _int("LOG_CHANNEL_ID", 0)
WELCOME_CHANNEL_ID = _int("WELCOME_CHANNEL_ID", 0)
VERIFICATION_CHANNEL_ID = _int("VERIFICATION_CHANNEL_ID", 0)
VERIFIED_ROLE_ID = _int("VERIFIED_ROLE_ID", 0)
TICKET_CATEGORY_ID = _int("TICKET_CATEGORY_ID", 0)
TEMPVOICE_CATEGORY_ID = _int("TEMPVOICE_CATEGORY_ID", 0)
TEMPVOICE_LOBBY_CHANNEL_ID = _int("TEMPVOICE_LOBBY_CHANNEL_ID", 0)

# ---- FFmpeg & TTS ----
_BUNDLED_FFMPEG = BASE_DIR / "ffmpeg" / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
FFMPEG_PATH = _BUNDLED_FFMPEG if _BUNDLED_FFMPEG.is_file() else Path(shutil.which("ffmpeg") or _BUNDLED_FFMPEG)

TTS_CACHE_DIR = BASE_DIR / "tts_cache"
TTS_MODEL_PATH = Path(_str("TTS_MODEL_PATH", str(BASE_DIR / "tts_models" / "en_US-lessac-medium.onnx")))
TTS_RATE = _int("TTS_RATE", 175)
TTS_MAX_CHARS = _int("TTS_MAX_CHARS", 400)
TTS_MAX_QUEUE = _int("TTS_MAX_QUEUE", 50)

# ---- Local AI (LM Studio / OpenAI-compatible) ----
AI_BASE_URL = _str("AI_BASE_URL", "http://127.0.0.1:1234/v1")
AI_MODEL = _str("AI_MODEL", "gemma-4-e4b-it-uncensored")
AI_CONTEXT_MESSAGES = _int("AI_CONTEXT_MESSAGES", 18)
AI_MAX_REPLY_CHARS = _int("AI_MAX_REPLY_CHARS", 900)
AI_MAX_CHANNELS = _int("AI_MAX_CHANNELS", 25)
AI_MESSAGES_PER_CHANNEL = _int("AI_MESSAGES_PER_CHANNEL", 4)
AI_MAX_CONTEXT_LINES = _int("AI_MAX_CONTEXT_LINES", 80)
AI_ASSIGNABLE_ROLE_NAMES = _list(
    "AI_ASSIGNABLE_ROLE_NAMES",
    ["Gamer", "Artist", "Creator", "Collaborator", "Music Lover"],
)

# ---- Visibility & Timezone ----
VISIBILITY_CHANNEL_LIMIT = _int("VISIBILITY_CHANNEL_LIMIT", 30)
VISIBILITY_MESSAGES_PER_CHANNEL = _int("VISIBILITY_MESSAGES_PER_CHANNEL", 3)
COMMUNITY_TIMEZONE = _str("COMMUNITY_TIMEZONE", "Asia/Kolkata")


def validate_config(bot_name: str = "ATLAS", require_token: bool = False) -> list[str]:
    """Validate configuration at startup. Returns a list of non-fatal warnings.

    Raises ConfigError if `require_token` is True and no token is configured,
    or if numeric limits are out of sane bounds.
    """
    warnings: list[str] = []

    if TTS_RATE <= 0 or TTS_MAX_CHARS <= 0 or TTS_MAX_QUEUE <= 0:
        raise ConfigError("TTS_RATE, TTS_MAX_CHARS, and TTS_MAX_QUEUE must be positive integers.")

    if require_token:
        env_key = "DISCORD_BOT_TOKEN" if bot_name.upper() == "ATLAS" else "DISCORD_BOT_TOKEN_2"
        token_file = TOKEN_FILE if bot_name.upper() == "ATLAS" else TOKEN2_FILE
        has_env = bool(os.environ.get(env_key, "").strip())
        has_file = token_file.is_file() and bool(token_file.read_text(encoding="utf-8").strip())
        if not has_env and not has_file:
            raise ConfigError(
                f"Missing {bot_name} bot token: set {env_key} in .env or provide {token_file.name}."
            )

    if FEATURE_TTS and not FFMPEG_PATH.is_file():
        warnings.append(f"FEATURE_TTS is enabled, but FFmpeg was not found at {FFMPEG_PATH}.")
    if FEATURE_TTS and not TTS_MODEL_PATH.is_file():
        warnings.append(f"FEATURE_TTS is enabled, but TTS model was not found at {TTS_MODEL_PATH}.")
    if not BOT_OWNER_IDS:
        warnings.append("BOT_OWNER_IDS is empty; owner-only shutdown and admin commands are disabled.")

    return warnings
