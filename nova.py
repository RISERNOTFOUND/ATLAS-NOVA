"""NOVA - the second bot. Runs as its OWN program (start_nova.bat), separate from ATLAS.

1) Watches #vibe-check. When ATLAS posts a vibe, NOVA replies to it with the matching answer.
2) Handles the !pfp command: `!pfp @member-or-bot` (or an ID, or nothing for your own).
6) /clear: slash command that deletes messages (Manage Messages needed).
5) Colour roles: `!colours` posts a picker (dropdown + custom RGB/hex); she creates and assigns the role.
4) Her own TTS: `!mtts` creates #nova-tts; she reads it aloud in Hindi (Devanagari) + English with female Piper voices.
3) Stays in a voice channel 24/7 (`!mjoin` / `!mleave`) until a moderator disconnects her.
Needs "Message Content Intent" enabled for NOVA in the Discord Developer Portal.
"Server Members Intent" is recommended (role tools); without it NOVA restarts in a limited mode automatically.
7) Newer features live in nova_ext/ (role menus & tools, temporary voice channels, voice controls, channel tools).
   Their prefix commands start with "m" (e.g. !mrole, !mvc) so they never clash with ATLAS's commands.
"""
import asyncio
import getpass
import json
import os
import re
import time
import secrets
import socket
import sys
import difflib
from datetime import datetime, timedelta, timezone
import importlib.util
import shutil
import subprocess
import urllib.request
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LIBS_DIR = ROOT / "nova_libs"  # NOVA downloads her own copies here if the main Python lacks a working one
if LIBS_DIR.exists():
    sys.path.insert(0, str(LIBS_DIR))


def ensure_package(test_import, pip_name):
    """Make sure `test_import` REALLY imports. If not, download the package into nova_libs and use that."""
    try:
        importlib.import_module(test_import)
        return True
    except Exception as first_error:
        print(f"[NOVA] {pip_name}: import failed here ({type(first_error).__name__}: {first_error}). Python in use: {sys.executable}")
    print(f"[NOVA] downloading {pip_name} into {LIBS_DIR.name} (first run only)...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "--target", str(LIBS_DIR), pip_name])
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"[NOVA] ERROR: download of {pip_name} failed: {exc}")
        return False
    if str(LIBS_DIR) not in sys.path:
        sys.path.insert(0, str(LIBS_DIR))
    importlib.invalidate_caches()
    try:
        importlib.import_module(test_import)
        print(f"[NOVA] {pip_name} ready.")
        return True
    except Exception as exc:
        print(f"[NOVA] ERROR: {pip_name} still can't be imported: {type(exc).__name__}: {exc}")
        return False


ensure_package("nacl.secret", "PyNaCl")   # discord.py's voice encryption
ensure_package("davey", "davey")          # Discord DAVE protocol for voice connectivity
ensure_package("piper", "piper-tts")      # text-to-speech engine
try:
    import config as _cfg, install_assets as _ia
    if not _cfg.FFMPEG_PATH.is_file():
        _ia.install_ffmpeg()
except Exception as _exc:
    print(f"[NOVA] Asset check warning: {_exc}")
ensure_package("tzdata", "tzdata")        # timezone data for reminders/events (Windows has none built in)

import discord
from discord import app_commands
from discord.ext import commands, tasks

from hinglish import hinglish_to_devanagari  # typed Hindi (Hinglish) -> Devanagari for the Hindi voice

from vibes import VIBE_DIALOGUES, VIBE_PREFIX
from priority import PRIORITY_SERVERS, guild_rank, sort_guilds
from common.db import Database, backup_file
from common.config import Registry
from common.errors import setup_logging, report_error, record_problem
from common.voice import VoiceHealth, classify_self_disconnect, connect_to, describe_error

try:
    import config as settings  # same config.py ATLAS uses (for the FFmpeg path)
    FFMPEG_PATH = getattr(settings, "FFMPEG_PATH", "ffmpeg") or "ffmpeg"
except ImportError:
    settings = None
    FFMPEG_PATH = "ffmpeg"

AUTHORIZED_COLOUR_OWNER_ID = int(getattr(settings, "MODERATION_OWNER_ID", 0) or 0)  # set MODERATION_OWNER_ID in .env
TOKEN_FILE = ROOT / "token2.txt"
STATE_FILE = ROOT / "nova_state.json"  # NOVA's own file, so she never clashes with ATLAS's config.json
VIBE_CHANNEL_ID = int(getattr(settings, "VIBE_CHANNEL_ID", 0) or 0)
VIBE_CHANNEL_NAME = str(getattr(settings, "VIBE_CHANNEL_NAME", "vibe-check") or "vibe-check")
FEATURE_VIBE = bool(getattr(settings, "FEATURE_VIBE", True))
def norm_key(text):
    return re.sub(r"[\ufe0f\u200d\s]+", "", text).casefold()


NORMALIZED_ANSWERS = {norm_key(prompt): reply for prompt, reply in VIBE_DIALOGUES}
PENDING = {}  # server rank -> vibes still waiting for NOVA's reply (lets priority servers go first)


def find_answer(content):
    body = re.sub(r"^\s*\u2600\ufe0f?\s*", "", content)
    key = norm_key(body)
    if key in NORMALIZED_ANSWERS:
        return NORMALIZED_ANSWERS[key]
    close = difflib.get_close_matches(key, NORMALIZED_ANSWERS.keys(), n=1, cutoff=0.9)
    return NORMALIZED_ANSWERS[close[0]] if close else None


intents = discord.Intents.default()
intents.message_content = True  # required to read which vibe ATLAS posted
MEMBERS_MARKER = ROOT / ".nova_no_members"  # written automatically if Server Members Intent is off in the Developer Portal
intents.members = not MEMBERS_MARKER.exists()  # role tools (bulk roles, role history, expiring roles) need the member list


def command_prefix(bot, message):
    """NOVA's own commands: '!' plus ATLAS's custom prefix for this server (same rule as her original commands)."""
    return commands.when_mentioned_or(*prefixes_for(message.guild) if message.guild else "!")(bot, message)


# A commands.Bot IS a discord.Client: every existing @client.event handler below works unchanged.
client = commands.Bot(command_prefix=command_prefix, intents=intents, allowed_mentions=discord.AllowedMentions.none(),
                      help_command=None, case_insensitive=True)
client.db = Database(ROOT / "nova.db", ROOT / "backups")
client.registry = Registry(client, client.db)
client.bot_name = "NOVA"
client.started_at = datetime.now(timezone.utc)
VOICE_HEALTH = VoiceHealth()
PENDING_DISCONNECT_CHECKS = set()
handled = set()


def read_token():
    token = os.getenv("DISCORD_BOT_TOKEN_2", "").strip()
    if token:
        return token
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = getpass.getpass("Paste NOVA's bot token (input hidden): ").strip()
    if not token:
        raise SystemExit("No token supplied.")
    TOKEN_FILE.write_text(token, encoding="utf-8")
    print("Token saved to token2.txt. Keep it private.")
    return token


@client.event
async def on_ready():
    if not getattr(client, "_colour_view_added", False):
        client.add_view(ColourView())
        client._colour_view_added = True
    if not voice_watchdog.is_running():
        voice_watchdog.start()
    for guild in sort_guilds(client.guilds):
        await sync_commands(guild)
    asyncio.create_task(ensure_voices())
    print(f"[NOVA] Python: {sys.executable} | discord.py {discord.__version__} | voice encryption available: {discord.voice_client.has_nacl}")
    print(f"[NOVA] online as {client.user} ({client.user.id}) in {len(client.guilds)} server(s). Waiting for ATLAS's vibes...")


def prefixes_for(guild):
    """'!' always works; also honours the custom prefix ATLAS uses in this server (read from config.json)."""
    found = {"!"}
    try:
        custom = json.loads((ROOT / "config.json").read_text(encoding="utf-8")).get("prefixes", {}).get(str(guild.id))
        if custom:
            found.add(custom.lower())
    except (OSError, ValueError, AttributeError):
        pass
    return found


# ---------------------------------------------------------------- 24/7 voice
def load_state():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state():
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(voice_state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


voice_state = load_state()  # guild_id -> {"channel_id": str, "paused": bool}
INTENTIONAL = set()


def migrate_voice_state():
    """One-time reset: the old code marked ordinary voice reconnects as 'removed by a moderator', which
    stopped the 24/7 rejoin forever. Entries paused by !mleave (no 'removed' flag) stay paused."""
    if voice_state.get("_voice_fix_version") == 2:
        return
    if any(isinstance(e, dict) and e.get("removed") for e in voice_state.values()):
        backup_file(STATE_FILE, ROOT / "backups", "pre-voice-fix")
    for entry in voice_state.values():
        if isinstance(entry, dict) and entry.get("paused") and entry.pop("removed", None):
            entry["paused"] = False
            entry["auto_resumed"] = "voice fix v2: earlier 'removal' was most likely a reconnect"
    voice_state["_voice_fix_version"] = 2
    save_state()


migrate_voice_state()


def mark_intentional(guild_id, seconds=20):
    """The voice-state event arrives after disconnect() returns, so keep the flag for a few seconds."""
    INTENTIONAL.add(guild_id)
    asyncio.get_running_loop().call_later(seconds, INTENTIONAL.discard, guild_id)


def home_channel(guild):
    me = guild.me
    voices = [c for c in guild.voice_channels if me and c.permissions_for(me).connect]
    return next((c for c in voices if c.name.casefold() == "chill vc"), voices[0] if voices else None)


_DOWN_SINCE = {}


async def keep_in_voice(guild):
    if guild.id in PENDING_DISCONNECT_CHECKS or not VOICE_HEALTH.ready(guild.id):
        return
    entry = voice_state.get(str(guild.id))
    if entry is None:
        home = home_channel(guild)
        if home is None:
            return
        entry = voice_state[str(guild.id)] = {"channel_id": str(home.id), "paused": False}
        save_state()
    if entry.get("paused"):
        return
    channel = guild.get_channel(int(entry["channel_id"])) if entry.get("channel_id") else None
    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        channel = home_channel(guild)
        if channel is None:
            return
        entry["channel_id"] = str(channel.id)
        save_state()
    voice = guild.voice_client
    if voice and voice.is_connected():
        _DOWN_SINCE.pop(guild.id, None)
        VOICE_HEALTH.ok(guild.id)
        if voice.channel.id != channel.id:
            await voice.move_to(channel)
        return
    if voice:  # may just be reconnecting: give it 45 seconds before treating it as stale
        first = _DOWN_SINCE.setdefault(guild.id, time.monotonic())
        if time.monotonic() - first < 45:
            return
        _DOWN_SINCE.pop(guild.id, None)
        mark_intentional(guild.id)
        await voice.disconnect(force=True)
    perms = channel.permissions_for(guild.me) if guild.me else None
    if perms is None or not (perms.connect and perms.view_channel):
        VOICE_HEALTH.fail(guild.id, f"Missing Connect/View Channel in {channel.name}")
        return
    await connect_to(channel)
    VOICE_HEALTH.ok(guild.id)
    print(f"[NOVA] joined voice: {channel.name} ({guild.name})")


@tasks.loop(minutes=1)
async def voice_watchdog():
    for guild in sort_guilds(client.guilds):
        try:
            await keep_in_voice(guild)
        except (discord.Forbidden, discord.ClientException, discord.HTTPException, asyncio.TimeoutError, RuntimeError) as exc:
            delay = VOICE_HEALTH.fail(guild.id, describe_error(exc))
            record_problem("voice", f"NOVA voice connect failed in {guild.name}: {describe_error(exc)} (retry in {delay}s)")
            print(f"[NOVA] voice connect failed in {guild.name}: {describe_error(exc)} (retrying in {delay}s)")


@voice_watchdog.before_loop
async def before_voice_watchdog():
    await client.wait_until_ready()


# NOVA's personality: cool, dry and unbothered - deliberately NOT ATLAS's cute-angry style.
NOVA_REMOVED_MESSAGES = (
    "🙄 Noted. Someone disconnected me. I'll just sit in the corner and judge quietly. `!mjoin` when you're ready to apologise.",
    "🕶️ Oh, so we're doing this now. Fine. I was only holding the silence together. Ring `!mjoin` if you miss me.",
    "📉 NOVA has left the voice channel (against her will). Atmosphere quality: down 40%. `!mjoin` to restore it.",
    "🥀 Removed from VC. Interesting choice. Bold. Wrong, but bold. I'll be here when you need me back: `!mjoin`.",
    "🫗 *sets down imaginary coffee* ...A mod kicked me out of voice. I'm not mad. I'm just writing this down in my notes. `!mjoin`.",
    "🎭 And just like that, the main character got cut from the scene. Disconnected by moderation. Encore? `!mjoin`.",
)


NOVA_MISSED_ME_MESSAGES = (
    "😏 So... you missed me. Awww. 🥹 It's okay, say it louder for the people in the back.",
    "💅 Oh look who came crawling back. You missed me, didn't you? Awww, how sweet. 🥰",
    "🙃 Back so soon? Aww, you missed me! Don't worry, I won't tell anyone. (I will.)",
    "😌 Look at you, typing `!mjoin` like you didn't miss me. Awww. I'm in. 🎧",
    "🎤 *dramatic return* ...so you missed me. Awww. Fine, I forgive you. Barely.",
    "🫶 Aww, someone couldn't handle the silence without me. Missed me that much? Cute.",
)


@client.event
async def on_voice_state_update(member, before, after):
    if client.user is None or member.id != client.user.id:
        return
    guild = member.guild
    entry = voice_state.get(str(guild.id))
    if after.channel is not None:
        VOICE_HEALTH.ok(guild.id)
    if before.channel and after.channel and before.channel.id != after.channel.id:  # moved by a mod: stay there
        if entry and not entry.get("paused"):
            entry["channel_id"] = str(after.channel.id)
            save_state()
        return
    if before.channel is None or after.channel is not None:
        return
    if guild.id in INTENTIONAL or not entry or entry.get("paused"):
        VOICE_HEALTH.gone(guild.id, "intentional")
        return  # left on purpose via !mleave
    if guild.id in PENDING_DISCONNECT_CHECKS:
        return
    # discord.py's automatic reconnect produces this same event. Wait, then decide.
    PENDING_DISCONNECT_CHECKS.add(guild.id)
    try:
        verdict = await classify_self_disconnect(client, guild)
    finally:
        PENDING_DISCONNECT_CHECKS.discard(guild.id)
    VOICE_HEALTH.gone(guild.id, verdict)
    if verdict not in ("moderator", "moderator-unconfirmed"):
        print(f"[NOVA] voice dropped in {guild.name} ({verdict}); the watchdog will reconnect.")
        return
    if guild.id in INTENTIONAL or entry.get("paused"):
        return
    entry["paused"] = True  # a moderator disconnected her by hand
    entry["removed"] = True  # remembered so !mjoin can mock the person who calls her back
    entry["removal_evidence"] = verdict
    save_state()
    channel = discord.utils.find(lambda c: "general" in c.name.casefold(), guild.text_channels) or guild.system_channel
    if channel:
        try:
            await channel.send(secrets.choice(NOVA_REMOVED_MESSAGES))
        except discord.HTTPException as exc:
            print(f"[NOVA] could not post the removal message: {type(exc).__name__}: {exc}")


async def handle_mjoin(message, argument):
    voice_channel = message.author.voice.channel if getattr(message.author, "voice", None) else None
    if voice_channel is None:
        return await message.reply("Join a voice channel first. I'm good, but not psychic. 🙄", mention_author=False)
    perms = voice_channel.permissions_for(message.guild.me)
    if not (perms.connect and perms.speak):
        return await message.reply("I'm missing Connect/Speak in that channel. Fix the permissions and try again.", mention_author=False)
    try:
        await connect_to(voice_channel)
    except (discord.ClientException, discord.HTTPException, asyncio.TimeoutError, RuntimeError) as exc:
        print(f"[NOVA] join failed: {type(exc).__name__}: {exc}")
        record_problem("voice", f"!mjoin failed in {message.guild.name}: {describe_error(exc)}")
        return await message.reply(f"Couldn't join: {describe_error(exc)} Try `!mvdiag`.", mention_author=False)
    VOICE_HEALTH.ok(message.guild.id)
    was_removed = bool(voice_state.get(str(message.guild.id), {}).get("removed"))
    voice_state[str(message.guild.id)] = {"channel_id": str(voice_channel.id), "paused": False}
    save_state()
    if was_removed:
        await message.reply(f"{secrets.choice(NOVA_MISSED_ME_MESSAGES)}\n*(Joined **{voice_channel.name}**.)*", mention_author=False)
    else:
        await message.reply(f"🎧 Fine, I'm in **{voice_channel.name}**. I'm staying 24/7 unless someone removes me.", mention_author=False)


async def handle_mleave(message, argument):
    voice = message.guild.voice_client
    if not voice or not voice.is_connected():
        return await message.reply("I'm not in voice.", mention_author=False)
    entry = voice_state.setdefault(str(message.guild.id), {"channel_id": str(voice.channel.id)})
    entry["paused"] = True
    save_state()
    mark_intentional(message.guild.id)
    queue = TTS_QUEUES.get(message.guild.id)
    while queue and not queue.empty():
        queue.get_nowait()
        queue.task_done()
    await voice.disconnect(force=True)
    await message.reply("Leaving. Don't miss me too much. 😌", mention_author=False)


# ---------------------------------------------------------------- NOVA's own TTS (Hindi + English, female)
TTS_FILE = ROOT / "nova_tts.json"          # guild_id -> NOVA's TTS channel id (separate from ATLAS's)
TTS_CACHE_DIR = ROOT / "nova_tts_cache"
VOICE_DIR = ROOT / "nova_voices"
HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
PIPER_VOICES = {  # both are female Piper voices
    "hi": "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium",
    "en": "en/en_US/amy/medium/en_US-amy-medium",
}
TTS_CHANNEL_NAME = "nova-tts"
TTS_MAX_CHARS = 300
DEVANAGARI = re.compile(r"[\u0900-\u097F]")
SCRIPT_RUNS = re.compile(r"[\u0900-\u097F][\u0900-\u097F\s\u0964\u0965,.!?]*|[^\u0900-\u097F]+")

try:
    tts_channels = json.loads(TTS_FILE.read_text(encoding="utf-8"))
except (OSError, ValueError):
    tts_channels = {}
TTS_QUEUES = {}
TTS_WORKERS = {}
TTS_NOW = {}  # guild_id -> text being spoken (for !mqueue / !mvstatus)
_PIPER_CACHE = {}
_voices_lock = None
_voices_ok = False


def tts_volume(guild_id):
    try:
        return max(10, min(200, int(client.registry.get(guild_id, "voice").get("volume") or 100)))
    except Exception:
        return 100


def save_tts():
    TTS_FILE.write_text(json.dumps(tts_channels, indent=2), encoding="utf-8")


def voice_files(lang):
    base = PIPER_VOICES[lang].split("/")[-1]
    return VOICE_DIR / (base + ".onnx"), VOICE_DIR / (base + ".onnx.json")


def download_voices_blocking():
    VOICE_DIR.mkdir(exist_ok=True)
    for lang, rel in PIPER_VOICES.items():
        for target, ext in zip(voice_files(lang), (".onnx", ".onnx.json")):
            if target.exists() and target.stat().st_size > 0:
                continue
            print(f"[NOVA] downloading voice file {target.name} (one time, can take a minute)...")
            part = target.with_name(target.name + ".part")
            request = urllib.request.Request(HF_BASE + rel + ext, headers={"User-Agent": "NOVA-Bot"})
            with urllib.request.urlopen(request, timeout=120) as response, open(part, "wb") as handle:
                shutil.copyfileobj(response, handle)
            part.replace(target)
    print("[NOVA] Hindi + English voices ready.")


async def ensure_voices():
    global _voices_lock, _voices_ok
    if _voices_ok:
        return True
    if _voices_lock is None:
        _voices_lock = asyncio.Lock()
    async with _voices_lock:
        if _voices_ok:
            return True
        try:
            await asyncio.to_thread(download_voices_blocking)
            _voices_ok = True
        except Exception as exc:
            print(f"[NOVA] ERROR: could not download the voices ({type(exc).__name__}: {exc}). Check your internet and try again.")
    return _voices_ok


def synthesize(lang, text, out_path):
    from piper import PiperVoice
    voice = _PIPER_CACHE.get(lang)
    if voice is None:
        voice = _PIPER_CACHE[lang] = PiperVoice.load(str(voice_files(lang)[0]))
    with wave.open(out_path, "wb") as wav_file:
        voice.synthesize_wav(text, wav_file)


def split_by_script(text):
    """Devanagari text -> Hindi voice, everything else -> English voice (handles mixed sentences)."""
    parts = []
    for match in SCRIPT_RUNS.finditer(text):
        chunk = match.group().strip()
        if re.search(r"\w", chunk):
            parts.append(("hi" if DEVANAGARI.search(chunk) else "en", chunk))
    return parts


def speech_text(message):
    text = message.content
    text = re.sub(r"<@!?([0-9]+)>", lambda m: (message.guild.get_member(int(m.group(1))).display_name if message.guild.get_member(int(m.group(1))) else "member"), text)
    text = re.sub(r"<#[0-9]+>", "channel", text)
    text = re.sub(r"<@&[0-9]+>", "role", text)
    text = re.sub(r"<a?:[A-Za-z0-9_]+:[0-9]+>", "emoji", text)
    text = re.sub(r"https?://\S+", "link", text)
    text = re.sub(r"([*_~`|>#])", "", text)
    return re.sub(r"\s+", " ", text).strip()[:TTS_MAX_CHARS]


async def tts_worker(guild_id):
    queue = TTS_QUEUES[guild_id]
    loop = asyncio.get_running_loop()
    TTS_CACHE_DIR.mkdir(exist_ok=True)
    while True:
        text = await queue.get()
        try:
            guild = client.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            if not voice or not voice.is_connected() or not await ensure_voices():
                continue
            TTS_NOW[guild_id] = text[:120]
            volume = tts_volume(guild_id)
            ff_options = "-vn" if volume == 100 else f"-vn -filter:a volume={volume / 100:.2f}"
            for lang, chunk in split_by_script(hinglish_to_devanagari(text)):
                path = TTS_CACHE_DIR / f"{guild_id}-{secrets.token_hex(6)}.wav"
                try:
                    await asyncio.to_thread(synthesize, lang, chunk, str(path))
                    done = asyncio.Event()

                    def after(error, done=done):
                        if error:
                            print(f"[NOVA] playback error: {error}")
                        loop.call_soon_threadsafe(done.set)

                    for attempt in range(3):
                        voice = guild.voice_client
                        for _ in range(10):  # up to 5s for a reconnecting voice link
                            if voice and voice.is_connected():
                                break
                            await asyncio.sleep(0.5)
                            voice = guild.voice_client
                        if not voice or not voice.is_connected():
                            print(f"[NOVA] voice link in {guild.name} is down, skipped one line.")
                            break
                        try:
                            voice.play(discord.FFmpegPCMAudio(str(path), executable=str(FFMPEG_PATH), options=ff_options), after=after)
                        except discord.ClientException as exc:
                            print(f"[NOVA] voice not ready in {guild.name} ({exc}); retrying...")
                            await asyncio.sleep(2)
                            continue
                        await done.wait()
                        break
                finally:
                    path.unlink(missing_ok=True)
        except Exception as exc:
            print(f"[NOVA] TTS error: {type(exc).__name__}: {exc}")
            report_error("tts", exc, f"guild {guild_id}")
        finally:
            TTS_NOW.pop(guild_id, None)
            queue.task_done()


async def queue_speech(message):
    voice = message.guild.voice_client
    if not voice or not voice.is_connected():
        try:
            await message.add_reaction("🔇")
        except discord.HTTPException:
            pass
        return
    text = speech_text(message)
    if not text:
        return
    queue = TTS_QUEUES.setdefault(message.guild.id, asyncio.Queue(maxsize=10))
    worker = TTS_WORKERS.get(message.guild.id)
    if worker is None or worker.done():
        TTS_WORKERS[message.guild.id] = asyncio.create_task(tts_worker(message.guild.id))
    try:
        queue.put_nowait(text)
    except asyncio.QueueFull:
        await message.reply("My speech queue is full, give me a moment 😮\u200d💨", mention_author=False)


async def handle_mtts(message, argument):
    if not message.author.guild_permissions.manage_guild:
        return await message.reply("You need **Manage Server** to set up my TTS channel.", mention_author=False)
    guild = message.guild
    channel = guild.get_channel(int(tts_channels[str(guild.id)])) if str(guild.id) in tts_channels else None
    if not isinstance(channel, discord.TextChannel):
        channel = discord.utils.find(lambda c: c.name == TTS_CHANNEL_NAME, guild.text_channels)
    created = False
    if channel is None:
        try:
            channel = await guild.create_text_channel(TTS_CHANNEL_NAME, topic="Type here and NOVA reads it out loud in voice (Hindi + English).", reason="NOVA TTS channel")
            created = True
        except discord.Forbidden:
            return await message.reply("I need the **Manage Channels** permission to create my TTS channel.", mention_author=False)
    tts_channels[str(guild.id)] = str(channel.id)
    save_tts()
    asyncio.create_task(ensure_voices())  # start the one-time voice download now
    if created:
        await channel.send("🎙️ **NOVA TTS.** Join a voice channel, call me with `!mjoin`, then type here and I'll say it out loud.\nWrite in English, Hindi (हिंदी) or **Hinglish** (like `mai aise baat kar rha hu`) and I'll pronounce it properly. Mixed sentences work too.")
    await message.reply(f"{'Created' if created else 'Using'} {channel.mention} as my TTS channel. ATLAS's TTS is untouched. ✅", mention_author=False)


async def handle_mttsdisable(message, argument):
    if not message.author.guild_permissions.manage_guild:
        return await message.reply("You need **Manage Server** for that.", mention_author=False)
    tts_channels.pop(str(message.guild.id), None)
    save_tts()
    await message.reply("My TTS channel is switched off (the channel itself is still there).", mention_author=False)


# ---------------------------------------------------------------- colour roles (name colours)
COLOURS_FILE = ROOT / "nova_colours.json"   # guild_id -> {"channel_id", "message_id"} of the picker embed
COLOUR_CHANNEL_NAME = "choose-your-colours"
COLOUR_ROLE_PREFIX = "🎨 #"                  # roles NOVA makes look like  "🎨 #FF69B4"
COLOUR_ROLE_RE = re.compile(r"^🎨 #[0-9A-F]{6}$")
MAX_COLOUR_ROLES = 200                        # Discord's hard limit is 250 roles per server
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")
COLOUR_PRESETS = [
    ("Red", "FF4D4D", "🔴"), ("Crimson", "DC143C", "❤️"), ("Rose", "FF6B9D", "🌹"), ("Hot Pink", "FF69B4", "💗"),
    ("Magenta", "E040FB", "🟣"), ("Purple", "9B59B6", "🔮"), ("Violet", "7C4DFF", "💜"), ("Indigo", "5C6BC0", "🫐"),
    ("Blue", "3B82F6", "🔵"), ("Sky", "5CC8FF", "🩵"), ("Cyan", "00E5FF", "🧊"), ("Teal", "1ABC9C", "🌊"),
    ("Mint", "7FFFD4", "🍃"), ("Green", "2ECC71", "🟢"), ("Lime", "A8E063", "🍏"), ("Yellow", "FFD93D", "🌟"),
    ("Gold", "F1C40F", "🏆"), ("Orange", "FF9F43", "🍊"), ("Coral", "FF7F50", "🪸"), ("Peach", "FFB997", "🍑"),
    ("Brown", "A0522D", "🤎"), ("Silver", "C0C0C0", "🩶"), ("White", "FFFFFF", "🤍"), ("Lavender", "C3B1E1", "🪻"),
]


def load_colours():
    try:
        data = json.loads(COLOURS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


colour_panels = load_colours()


def parse_colour(hex_text, r_text, g_text, b_text):
    """Return 'RRGGBB' from either a hex code or R/G/B numbers, or raise ValueError with a friendly message."""
    hex_text = (hex_text or "").strip()
    if hex_text:
        match = HEX_RE.match(hex_text)
        if not match:
            raise ValueError("That hex code isn't valid. Use something like `#FF69B4`.")
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        value = digits.upper()
    else:
        try:
            r, g, b = (int((t or "").strip()) for t in (r_text, g_text, b_text))
        except ValueError:
            raise ValueError("Fill in a hex code, or all three of Red, Green and Blue as numbers 0-255.")
        if not all(0 <= n <= 255 for n in (r, g, b)):
            raise ValueError("Red, Green and Blue must each be between 0 and 255.")
        value = f"{r:02X}{g:02X}{b:02X}"
    return "010101" if value == "000000" else value  # pure black means 'no colour' on Discord


def is_colour_role(role):
    return bool(COLOUR_ROLE_RE.match(role.name))


async def apply_colour(interaction, hex_value):
    guild, member = interaction.guild, interaction.user
    me = guild.me
    if not me.guild_permissions.manage_roles:
        return await interaction.followup.send("I need the **Manage Roles** permission to do that. Ask an admin to give it to me. 🙏", ephemeral=True)
    name = f"{COLOUR_ROLE_PREFIX}{hex_value}"
    role = discord.utils.get(guild.roles, name=name)
    try:
        if role is None:
            if sum(1 for r in guild.roles if is_colour_role(r)) >= MAX_COLOUR_ROLES:
                return await interaction.followup.send("This server has hit my colour-role limit. Pick one of the preset colours instead! 🎨", ephemeral=True)
            role = await guild.create_role(name=name, colour=discord.Colour(int(hex_value, 16)), reason=f"NOVA colour role for {member}")
        target = max(1, me.top_role.position - 1)  # just under NOVA's top role so the colour wins over other roles
        if role.position != target:
            try:
                await role.edit(position=target)
            except discord.HTTPException:
                pass
        old = [r for r in member.roles if is_colour_role(r) and r.id != role.id]
        if old:
            await member.remove_roles(*old, reason="NOVA colour change")
        if role not in member.roles:
            await member.add_roles(role, reason="NOVA colour role")
    except discord.Forbidden:
        return await interaction.followup.send("I couldn't do that. My role must be **above** the colour roles and have **Manage Roles**. Ask an admin to drag my role higher in Server Settings > Roles. 🙏", ephemeral=True)
    except discord.HTTPException as exc:
        return await interaction.followup.send(f"Discord refused: `{exc}`", ephemeral=True)
    embed = discord.Embed(title="🎨 Your name colour is set!", description=f"You now have **#{hex_value}**. Your name will show in this colour.", colour=discord.Colour(int(hex_value, 16)))
    if role.position < max((r.position for r in member.roles if r.colour.value and r.id != role.id), default=0):
        embed.set_footer(text="Heads up: another coloured role of yours sits higher, so ask an admin to move my role up.")
    await interaction.followup.send(embed=embed, ephemeral=True)
    print(f"[NOVA] colour #{hex_value} -> {member} in {guild.name}")


class ColourModal(discord.ui.Modal, title="🎨 Custom colour"):
    hex_code = discord.ui.TextInput(label="Hex code (optional, e.g. #FF69B4)", required=False, max_length=7)
    red = discord.ui.TextInput(label="Red (0-255)", required=False, max_length=3, placeholder="255")
    green = discord.ui.TextInput(label="Green (0-255)", required=False, max_length=3, placeholder="105")
    blue = discord.ui.TextInput(label="Blue (0-255)", required=False, max_length=3, placeholder="180")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = parse_colour(str(self.hex_code), str(self.red), str(self.green), str(self.blue))
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await apply_colour(interaction, value)


class ColourView(discord.ui.View):
    """Persistent: keeps working after restarts."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(custom_id="nova:colour:preset", placeholder="🎨 Pick a ready-made colour…",
                       options=[discord.SelectOption(label=n, value=h, emoji=e, description=f"#{h}") for n, h, e in COLOUR_PRESETS])
    async def preset(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        await apply_colour(interaction, select.values[0])

    @discord.ui.button(label="Custom RGB / Hex", emoji="🌈", style=discord.ButtonStyle.primary, custom_id="nova:colour:custom")
    async def custom(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ColourModal())

    @discord.ui.button(label="Remove my colour", emoji="🧼", style=discord.ButtonStyle.secondary, custom_id="nova:colour:remove")
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        old = [r for r in interaction.user.roles if is_colour_role(r)]
        if not old:
            return await interaction.response.send_message("You don't have a colour from me yet.", ephemeral=True)
        try:
            await interaction.user.remove_roles(*old, reason="NOVA colour removed")
        except discord.HTTPException:
            return await interaction.response.send_message("I couldn't remove it. Check my role is above the colour roles.", ephemeral=True)
        await interaction.response.send_message("Colour removed. 🧼", ephemeral=True)


def colour_embed():
    embed = discord.Embed(
        title="🎨 Choose your colours!",
        description=("Make your name stand out in the member list and chat.\n\n"
                     "**1.** Pick a ready-made colour from the dropdown, **or**\n"
                     "**2.** Press **Custom RGB / Hex** and enter your own `R G B` numbers (0-255) or a hex code like `#FF69B4`.\n\n"
                     "I'll create the colour role and give it to you automatically. Pick again any time to change it."),
        colour=discord.Colour.from_rgb(255, 105, 180))
    embed.set_footer(text="NOVA • colour roles • Created by RISERNOTFOUND")
    return embed


async def handle_colours(message, argument):
    if not message.author.guild_permissions.manage_guild:
        return await message.reply("You need **Manage Server** to set up the colour picker.", mention_author=False)
    guild = message.guild
    if not guild.me.guild_permissions.manage_roles:
        return await message.reply("Give me the **Manage Roles** permission first (and put my role near the top), then run this again.", mention_author=False)
    panel = colour_panels.get(str(guild.id), {})
    channel = guild.get_channel(int(panel["channel_id"])) if panel.get("channel_id") else None
    if not isinstance(channel, discord.TextChannel):
        channel = discord.utils.find(lambda c: c.name == COLOUR_CHANNEL_NAME, guild.text_channels)
    if channel is None:
        try:
            channel = await guild.create_text_channel(
                COLOUR_CHANNEL_NAME, topic="Pick the colour of your name here!",
                overwrites={guild.default_role: discord.PermissionOverwrite(send_messages=False, add_reactions=False),
                            guild.me: discord.PermissionOverwrite(send_messages=True, embed_links=True)},
                reason="NOVA colour picker")
        except discord.Forbidden:
            channel = message.channel  # no Manage Channels: post right here instead
    posted = None
    if panel.get("message_id") and panel.get("channel_id") == str(channel.id):
        try:  # refresh the existing picker instead of making a duplicate
            posted = await channel.fetch_message(int(panel["message_id"]))
            await posted.edit(embed=colour_embed(), view=ColourView())
        except discord.HTTPException:
            posted = None
    if posted is None:
        posted = await channel.send(embed=colour_embed(), view=ColourView())
    colour_panels[str(guild.id)] = {"channel_id": str(channel.id), "message_id": str(posted.id)}
    COLOURS_FILE.write_text(json.dumps(colour_panels, indent=2), encoding="utf-8")
    await message.reply(f"Colour picker is ready in {channel.mention} 🎨", mention_author=False)


# ---------------------------------------------------------------- Owner-only Role Colour Reset
class ConfirmClearColourView(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.confirmed = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("You are not authorized to use this confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="⏳ Reset confirmed. Processing role colours...", view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        self.stop()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Role colour reset cancelled.", view=self)


async def handle_clear_role_colour(message, argument):
    # 1. Verify invoking user's exact Discord ID
    if message.author.id != AUTHORIZED_COLOUR_OWNER_ID:
        return await message.reply("⛔ Unauthorized: This command can only be executed by the authorized owner.", mention_author=False)

    guild = message.guild
    if not guild:
        return await message.reply("This command can only be used in a server.", mention_author=False)

    # 2. Verify bot has Manage Roles permission
    me = guild.me
    if not me.guild_permissions.manage_roles:
        return await message.reply("⛔ I need the **Manage Roles** permission to modify role colours.", mention_author=False)

    # 3-6. Inspect roles, exclude @everyone, exclude default colours, identify custom colours
    roles_with_custom_colour = []
    eligible_roles = []
    skipped_roles = []  # (role, reason)

    for role in guild.roles:
        if role.is_default():  # @everyone
            continue
        gradient = bool(getattr(role, "secondary_colour", None) or getattr(role, "tertiary_colour", None))
        if role.colour.value == 0 and not gradient:  # already Discord's default colour (no gradient either)
            continue

        roles_with_custom_colour.append(role)

        # 7-8. Check bot's role hierarchy and bot's own managed roles
        if role.managed:
            skipped_roles.append((role, "Managed by integration/bot"))
        elif role >= me.top_role:
            skipped_roles.append((role, "Higher than or equal to NOVA's highest role in hierarchy"))
        else:
            eligible_roles.append(role)

    # 9. Display preview
    preview_desc = (
        f"**Role Colour Reset Preview for {guild.name}**\n\n"
        f"• **Roles with custom colour:** {len(roles_with_custom_colour)}\n"
        f"• **Eligible for reset:** {len(eligible_roles)}\n"
        f"• **Skipped:** {len(skipped_roles)}\n\n"
    )

    if skipped_roles:
        skip_preview = "\n".join(f"- `{r.name}` ({reason})" for r, reason in skipped_roles[:10])
        if len(skipped_roles) > 10:
            skip_preview += f"\n- ... and {len(skipped_roles) - 10} more"
        preview_desc += f"**Skipped Reasons Preview:**\n{skip_preview}\n\n"

    preview_desc += "⚠️ **Warning:** This will reset ALL eligible role colours to Discord's default (no colour). Only role colours are changed. No names, permissions, or memberships will be touched.\n\nClick **Confirm Reset** below within 60 seconds to execute."

    embed = discord.Embed(
        title="🎨 Clear Role Colours - Confirmation Required",
        description=preview_desc,
        colour=discord.Colour.orange()
    )
    embed.set_footer(text="Authorized owner only")

    view = ConfirmClearColourView(message.author.id)
    confirm_msg = await message.reply(embed=embed, view=view, mention_author=False)

    # 10. Require explicit confirmation
    await view.wait()
    if not view.confirmed:
        if view.confirmed is None:
            for child in view.children:
                child.disabled = True
            try:
                await confirm_msg.edit(content="⏱️ Timed out waiting for confirmation. No changes were made.", view=view)
            except discord.HTTPException:
                pass
        return

    # Execute colour reset safely
    success_count = 0
    failed_roles = []  # (role, error_message)

    for role in eligible_roles:
        try:
            # Change ONLY the colour (and gradient colours, if the role has them) to default
            colour_only = {"colour": discord.Colour.default()}
            if getattr(role, "secondary_colour", None) or getattr(role, "tertiary_colour", None):
                colour_only.update(secondary_colour=None, tertiary_colour=None)
            await role.edit(**colour_only, reason=f"NOVA !mclearrolecolour executed by owner {message.author.id}")
            success_count += 1
            await asyncio.sleep(0.35)  # Respect Discord rate limits
        except discord.Forbidden as exc:
            failed_roles.append((role, f"Forbidden: {exc}"))
        except discord.HTTPException as exc:
            failed_roles.append((role, f"HTTP {exc.status}: {exc.text}"))
        except Exception as exc:
            failed_roles.append((role, f"{type(exc).__name__}: {exc}"))

    # Report results
    result_desc = (
        f"**Role Colour Reset Completed**\n\n"
        f"✅ **Successfully reset:** {success_count}\n"
        f"⏭️ **Skipped (hierarchy/managed):** {len(skipped_roles)}\n"
        f"❌ **Failed:** {len(failed_roles)}\n"
    )
    if failed_roles:
        fail_preview = "\n".join(f"- `{r.name}`: {err}" for r, err in failed_roles[:10])
        result_desc += f"\n**Failure Details:**\n{fail_preview}\n"

    result_embed = discord.Embed(
        title="🎨 Role Colour Reset Report",
        description=result_desc,
        colour=discord.Colour.green() if not failed_roles else discord.Colour.gold()
    )
    await message.reply(embed=result_embed, mention_author=False)



# ---------------------------------------------------------------- /clear (slash command)
tree = client.tree  # commands.Bot already owns a CommandTree
BULK_MAX_AGE = timedelta(days=14) - timedelta(minutes=2)  # Discord can only bulk-delete messages younger than 14 days


class ConfirmClear(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.confirmed = None

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This confirmation isn't yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, clear it", emoji="🧹", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        await interaction.response.edit_message(content="Clearing… 🧹", view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


@tree.command(name="clear", description="NOVA deletes messages in this channel (members' messages by default)")
@app_commands.describe(
    amount="How many recent messages to check (1-1000). Ignored when 'everything' is on.",
    member="Only delete messages from this member",
    include_bots="Also delete messages sent by bots (default: only users' messages)",
    everything="Clear the whole channel (every message younger than 14 days)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
async def clear_command(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1000] = 100,
                        member: discord.Member = None, include_bots: bool = False, everything: bool = False):
    channel = interaction.channel
    if not interaction.permissions.manage_messages:
        return await interaction.response.send_message("You need the **Manage Messages** permission to use this.", ephemeral=True)
    if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
        return await interaction.response.send_message("I can't clear messages in this kind of channel.", ephemeral=True)
    mine = channel.permissions_for(interaction.guild.me)
    if not (mine.manage_messages and mine.read_message_history):
        return await interaction.response.send_message("I need **Manage Messages** and **Read Message History** in this channel first. 🙏", ephemeral=True)

    if everything or amount > 200:
        view = ConfirmClear(interaction.user.id)
        scope = "**every message** in this channel" if everything else f"the last **{amount}** messages"
        who = f"from {member.mention}" if member else ("(users and bots)" if include_bots else "sent by users (not bots)")
        await interaction.response.send_message(f"Delete {scope} {who}? Pinned messages are kept.", view=view, ephemeral=True)
        await view.wait()
        if not view.confirmed:
            if view.confirmed is None:
                await interaction.edit_original_response(content="Timed out, nothing was deleted.", view=None)
            return
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)

    def should_delete(msg):
        if msg.pinned:
            return False
        if member is not None:
            return msg.author.id == member.id
        return include_bots or not msg.author.bot

    try:
        deleted = await channel.purge(limit=None if everything else amount, check=should_delete, bulk=True,
                                      after=discord.utils.utcnow() - BULK_MAX_AGE, reason=f"/clear by {interaction.user}")
    except discord.Forbidden:
        return await interaction.followup.send("I'm not allowed to delete messages here. Check my permissions.", ephemeral=True)
    except discord.HTTPException as exc:
        return await interaction.followup.send(f"Discord stopped me: `{exc}`", ephemeral=True)
    print(f"[NOVA] /clear by {interaction.user} removed {len(deleted)} messages in #{channel.name} ({interaction.guild.name}).")
    await interaction.followup.send(f"🧹 Deleted **{len(deleted)}** message{'s' if len(deleted) != 1 else ''}. (Discord only lets me bulk-delete messages younger than 14 days, and I keep pinned ones.)", ephemeral=True)


@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[NOVA] slash command error: {type(error).__name__}: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("Something went wrong with that command.", ephemeral=True)
        else:
            await interaction.response.send_message("Something went wrong with that command.", ephemeral=True)
    except discord.HTTPException:
        pass


async def sync_commands(guild):
    """Register the / commands per server (appears instantly, unlike global sync)."""
    try:
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    except discord.Forbidden:
        print(f"[NOVA] could not register / commands in {guild.name}: re-invite NOVA with the 'applications.commands' scope ticked.")
    except discord.HTTPException as exc:
        print(f"[NOVA] / command sync failed in {guild.name}: {type(exc).__name__}: {exc}")


@client.event
async def on_guild_join(guild):
    await sync_commands(guild)


PFP_COOLDOWNS = {}


async def handle_pfp(message, argument):
    now = time.monotonic()
    if now - PFP_COOLDOWNS.get(message.author.id, 0) < 4:
        return
    PFP_COOLDOWNS[message.author.id] = now

    target = next((u for u in message.mentions if u.id != client.user.id), None)
    if target is None and message.mentions:  # they mentioned only NOVA herself
        target = client.user
    if target is None:
        match = re.search(r"\d{15,20}", argument)
        if match:
            try:
                target = await client.fetch_user(int(match.group()))
            except discord.NotFound:
                return await message.reply("I couldn't find anyone with that ID 🥺", mention_author=False)
    if target is None:
        target = message.author

    member = message.guild.get_member(target.id) if message.guild else None
    shown = member or target
    avatar = shown.display_avatar
    global_avatar = target.display_avatar if member is None else (member._user.display_avatar if hasattr(member, "_user") else target.display_avatar)
    embed = discord.Embed(title=f"{shown.display_name}'s profile picture", colour=discord.Colour.from_rgb(255, 150, 200))
    embed.set_image(url=avatar.replace(size=1024).url)
    formats = ["png", "jpg", "webp"] + (["gif"] if avatar.is_animated() else [])
    embed.description = " • ".join(f"[{f.upper()}]({avatar.replace(size=1024, format=f).url})" for f in formats)
    if avatar.url != global_avatar.url:
        embed.add_field(name="Global avatar", value=f"[Open]({global_avatar.replace(size=1024).url})", inline=False)
    embed.set_footer(text=f"{'Bot' if target.bot else 'Member'} • ID {target.id} • asked by {message.author.display_name}")
    await message.reply(embed=embed, mention_author=False)
    print(f"[NOVA] pfp of {target} sent in #{getattr(message.channel, 'name', 'dm')}.")


@client.event
async def on_message(message):
    if not message.author.bot and message.guild and message.content:
        lowered = message.content.lower()
        for prefix in prefixes_for(message.guild):
            for name, handler in (
                ("pfp", handle_pfp),
                ("mjoin", handle_mjoin),
                ("mleave", handle_mleave),
                ("mtts", handle_mtts),
                ("mttsdisable", handle_mttsdisable),
                ("colours", handle_colours),
                ("colors", handle_colours),
                ("mclearrolecolour", handle_clear_role_colour),
                ("mclearrolecolor", handle_clear_role_colour),
            ):
                head = prefix + name
                if lowered == head or lowered.startswith(head + " "):
                    try:
                        await handler(message, message.content[len(head):])
                    except discord.Forbidden as exc:
                        print(f"[NOVA] ERROR: missing permission in #{message.channel.name}: {exc}")
                    except discord.HTTPException as exc:
                        print(f"[NOVA] ERROR: command failed: {type(exc).__name__}: {exc}")
                    return
        ctx = await client.get_context(message)
        if ctx.valid:  # NOVA's newer commands (role tools, temp voice, channel tools...)
            await client.invoke(ctx)
            return
        tts_id = tts_channels.get(str(message.guild.id))
        if tts_id and message.channel.id == int(tts_id):
            await queue_speech(message)
            return
    if not message.author.bot or message.author.id == client.user.id or message.id in handled:
        return
    if not message.content.lstrip().startswith("\u2600"):  # ATLAS's vibes start with the sun emoji
        return
    answer = find_answer(message.content)
    if answer is None:
        print(f"[NOVA] saw a vibe in #{getattr(message.channel, 'name', '?')} ({message.guild.name}) but it matches none of my 200: {message.content[:80]!r}")
        return
    handled.add(message.id)
    rank = guild_rank(message.guild)
    PENDING[rank] = PENDING.get(rank, 0) + 1
    try:
        if rank > 0:  # not the top-priority server: let higher-priority servers get their reply first
            await asyncio.sleep(12)
            waited = 0
            while any(count > 0 for r, count in PENDING.items() if r < rank) and waited < 90:
                await asyncio.sleep(1)
                waited += 1
        async with message.channel.typing():
            await asyncio.sleep(secrets.choice(range(4, 10)))
        await message.reply(answer, mention_author=False)
        print(f"[NOVA] replied in #{message.channel.name} ({message.guild.name}).")
    except discord.Forbidden as exc:
        print(f"[NOVA] ERROR: missing permission in #{message.channel.name} ({message.guild.name}): View Channel / Send Messages / Read Message History. {exc}")
    except discord.HTTPException as exc:
        print(f"[NOVA] ERROR: Discord rejected the reply in {message.guild.name}: {type(exc).__name__}: {exc}")
    finally:
        PENDING[rank] -= 1


NOVA_EXTENSIONS = ("nova_ext.core", "nova_ext.voice", "nova_ext.roles", "nova_ext.tempvoice", "nova_ext.channels")


async def nova_setup_hook():
    """Share NOVA's internals with her extensions, then load them. A failing extension is reported and skipped."""
    import types
    client.nova = types.SimpleNamespace(
        voice_state=voice_state, save_state=save_state, tts_queues=TTS_QUEUES, tts_now=TTS_NOW, voice_health=VOICE_HEALTH,
        mark_intentional=mark_intentional, owner_id=AUTHORIZED_COLOUR_OWNER_ID, pending_disconnects=PENDING_DISCONNECT_CHECKS,
        is_colour_role=is_colour_role)
    client.failed_extensions = {}
    _feature_gates = {
        "nova_ext.voice": bool(getattr(settings, "FEATURE_VOICE", True)),
        "nova_ext.tempvoice": bool(getattr(settings, "FEATURE_TEMPVOICE", True)),
        "nova_ext.roles": bool(getattr(settings, "FEATURE_ROLES", True)),
    }
    for name in NOVA_EXTENSIONS:
        if not _feature_gates.get(name, True):
            continue
        try:
            await client.load_extension(name)
        except Exception as exc:
            client.failed_extensions[name] = report_error("startup", exc, f"loading {name}")
            print(f"[NOVA] extension {name} failed to load (error {client.failed_extensions[name]}); her other features continue.")


client.setup_hook = nova_setup_hook


def single_instance_lock(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        print("NOVA is ALREADY running in another window. Close it first.")
        sys.exit(3)
    return sock


if __name__ == "__main__":
    _lock = single_instance_lock(47632)
    setup_logging(ROOT, "nova")
    try:
        client.run(read_token(), log_handler=None)
    except discord.LoginFailure:
        print("[NOVA] LOGIN FAILED - token in token2.txt is wrong or was reset. Delete token2.txt and run again.")
        sys.exit(3)
    except discord.PrivilegedIntentsRequired:
        if intents.members:
            MEMBERS_MARKER.write_text("server members intent was not enabled", encoding="utf-8")
            print("[NOVA] Server Members Intent is OFF for NOVA in the Developer Portal, so role tools that need the member list are limited.\n"
                  "NOVA will restart without it. To enable them: Developer Portal > Bot > turn ON 'Server Members Intent', then delete .nova_no_members.")
            sys.exit(1)  # start_nova.bat restarts her automatically
        print("[NOVA] ERROR: turn ON 'Message Content Intent' for NOVA in the Developer Portal (Bot tab), then run again.")
        sys.exit(3)
