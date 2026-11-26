"""Server priority shared by ATLAS and NOVA. Highest priority first.
Use the server name (any capitalisation, partial match is fine) or the server ID."""

PRIORITY_SERVERS = []  # e.g. ["My Main Server"] - servers whose vibes are answered first


def guild_rank(guild):
    name = guild.name.casefold()
    for index, item in enumerate(PRIORITY_SERVERS):
        wanted = str(item).strip().casefold()
        if wanted and (wanted == str(guild.id) or wanted in name):
            return index
    return len(PRIORITY_SERVERS)


def sort_guilds(guilds):
    return sorted(guilds, key=guild_rank)  # stable: unlisted servers keep their normal order after the priority ones
