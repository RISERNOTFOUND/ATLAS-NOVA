"""Pure-Python tests for the TTS command gate (no Discord needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.cmdmatch import message_is_command  # noqa: E402

PREFIXES = ["!", "k", "K", "?", ".", "<@123> ", "<@!123> "]
COMMANDS = {"join", "leave", "help", "ttschannel"}
exists = lambda name: name.lower() in COMMANDS  # noqa: E731


def test_hinglish_sentences_are_spoken():
    for text in ("kya haal hai", "Kal milte hain", "kaise ho", "kidhar ho", "kuch bhi"):
        assert not message_is_command(text, PREFIXES, exists), text


def test_real_commands_still_detected():
    for text in ("!join", "kjoin", "Kjoin", "k" + "leave now", "!anything", "?help", ".foo", "<@123> hi"):
        assert message_is_command(text, PREFIXES, exists), text


def test_k_space_command_is_not_a_command():
    assert not message_is_command("k join", PREFIXES, exists)      # discord.py needs no space after the prefix


def test_custom_symbol_prefix_unchanged():
    assert message_is_command("$join", ["$"], exists)
    assert not message_is_command("hello", ["$"], exists)


def test_empty_and_none():
    assert not message_is_command("", PREFIXES, exists)
    assert not message_is_command(None, PREFIXES, exists)
