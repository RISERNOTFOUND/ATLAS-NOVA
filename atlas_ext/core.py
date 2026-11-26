"""ATLAS: settings panel, diagnostics, preferences, configuration export/import."""
from common.diag import make_core_cog

EXPORT_TABLES = ("role_menus", "automod_words", "rules_versions", "announcements", "feeds", "trivia_custom", "ticket_categories")


async def setup(bot):
    def extra(guild):
        voice = guild.voice_client
        health = bot.atlas.voice_health
        state = "connected to " + voice.channel.name if voice and voice.is_connected() else ("reconnecting" if voice else "not connected")
        err = health.last_error.get(guild.id)
        return [("Voice subsystem", f"{state}" + (f"\nLast error: {err[1]} (<t:{int(err[0])}:R>)" if err else ""))]
    bot.diag_extra = extra
    await bot.add_cog(make_core_cog(bot, "", EXPORT_TABLES))
