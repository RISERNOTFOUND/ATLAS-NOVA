"""Decide whether a chat message is a bot command (pure Python, no Discord imports, unit-testable).

Why this exists: ATLAS's default prefixes include the *letters* "k"/"K" (plus "?" and "."). The TTS gate used to treat
every message that merely STARTED with a prefix as a command, so ordinary sentences such as "kya haal hai", "kal milte
hain" or "kaise ho" were silently never spoken. Symbol prefixes keep the old rule; letter/digit prefixes only count
when they are followed directly by a real command name (that is exactly when discord.py would run a command).
"""


def message_is_command(content, prefixes, command_exists):
    """content: message text. prefixes: iterable of prefix strings (may include mention prefixes).
    command_exists: callable(name) -> truthy when `name` is a registered command/alias."""
    text = content or ""
    folded = text.casefold()
    for prefix in prefixes:
        if not prefix or not folded.startswith(prefix.casefold()):
            continue
        if prefix.startswith("<@") or not prefix[-1].isalnum():
            return True                      # mention or symbol prefix: same behaviour as before
        rest = text[len(prefix):]
        if rest and not rest[0].isspace() and command_exists(rest.split()[0]):
            return True                      # e.g. "kjoin" -> prefix "k" + command "join"
    return False
