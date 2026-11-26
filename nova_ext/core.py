"""NOVA: settings panel (`!mconfig` / `/config`), diagnostics, preferences, configuration export/import."""
from common.diag import make_core_cog

EXPORT_TABLES = ("role_menus", "role_templates", "tempvoice_hubs", "sticky_messages", "autothread_channels")


async def setup(bot):
    def extra(guild):
        voice = guild.voice_client
        health = bot.nova.voice_health
        state = "connected to " + voice.channel.name if voice and voice.is_connected() else ("reconnecting" if voice else "not connected")
        err = health.last_error.get(guild.id)
        lines = [("Voice subsystem", state + (f"\nLast error: {err[1]} (<t:{int(err[0])}:R>)" if err else ""))]
        if not bot.intents.members:
            lines.append(("⚠️ Server Members Intent", "Off: bulk roles, role history and expiring-role cleanup are limited. Enable it in the Developer Portal and delete `.nova_no_members`."))
        return lines
    bot.diag_extra = extra
    await bot.add_cog(make_core_cog(bot, "m", EXPORT_TABLES))
