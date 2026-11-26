"""Starboard, featured message of the day, and private bookmarks.

Respects privacy: messages from age-restricted channels never go to a non-age-restricted starboard,
excluded channels/roles are skipped, members can opt out with `!prefs`, and bookmarks are only ever
sent to the person who saved them (by DM).
"""
import asyncio
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import ui
from common.config import Module, Option
from common.diag import user_prefs
from common.timeparse import get_tz


class Starboard(commands.Cog, name="Starboard"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.locks = {}
        bot.db.migrate("starboard_001", """
            CREATE TABLE IF NOT EXISTS starboard_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL, author_id INTEGER, star_channel_id INTEGER, star_message_id INTEGER, stars INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_star_msg ON starboard_posts(guild_id, message_id);
            CREATE TABLE IF NOT EXISTS bookmarks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, guild_id INTEGER, channel_id INTEGER,
                message_id INTEGER, author_id INTEGER, excerpt TEXT, note TEXT, created_ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id);
            CREATE TABLE IF NOT EXISTS motd_posts (guild_id INTEGER NOT NULL, day TEXT NOT NULL, PRIMARY KEY (guild_id, day))""")
        bot.registry.register(Module("starboard", "Starboard", "⭐", "Highlights messages that get enough star reactions.", [
            Option("channel", "Starboard channel", "channel", None), Option("emoji", "Emoji", "emoji", "⭐", max_length=60),
            Option("threshold", "Reactions needed", "int", 3, "", 1, 100), Option("self_star", "Count self-stars", "bool", False),
            Option("remove_below", "Remove when below threshold", "bool", True),
            Option("excluded_channels", "Excluded channels", "channels", []), Option("excluded_roles", "Excluded roles", "roles", []),
            Option("message_of_day", "Featured message of the day", "bool", False, "Posts yesterday's most-starred message at 12:00"),
            Option("timezone", "Timezone", "tz", "Asia/Kolkata"),
        ]))
        self.bookmark_menu = app_commands.ContextMenu(name="Bookmark message", callback=self.bookmark_menu_cb)

    async def cog_load(self):
        self.bot.tree.add_command(self.bookmark_menu)
        self.motd_loop.start()

    async def cog_unload(self):
        self.bot.tree.remove_command(self.bookmark_menu.name, type=self.bookmark_menu.type)
        self.motd_loop.cancel()

    def s(self, guild_id):
        return self.bot.registry.get(guild_id, "starboard")

    def eligible(self, message, s, star_channel):
        if message.author.bot or message.channel.id == star_channel.id:
            return False
        excluded = {int(c) for c in s.get("excluded_channels") or []}
        if message.channel.id in excluded or getattr(message.channel, "parent_id", None) in excluded:
            return False
        if getattr(message.channel, "nsfw", False) and not getattr(star_channel, "nsfw", False):
            return False
        # The starboard must not leak messages from channels that everyone can't see.
        everyone = message.guild.default_role
        if not message.channel.permissions_for(everyone).view_channel and star_channel.permissions_for(everyone).view_channel:
            return False
        roles = {int(r) for r in s.get("excluded_roles") or []}
        if isinstance(message.author, discord.Member) and any(r.id in roles for r in message.author.roles):
            return False
        if user_prefs(self.db, message.author.id).get("starboard_optout"):
            return False
        return True

    async def count(self, message, emoji, self_star):
        for reaction in message.reactions:
            if str(reaction.emoji) == emoji:
                n = 0
                async for user in reaction.users(limit=500):
                    if user.bot or (not self_star and user.id == message.author.id):
                        continue
                    n += 1
                return n
        return 0

    def star_embed(self, message, stars, emoji):
        e = discord.Embed(description=ui.clip(message.content, 3800) or None, colour=discord.Colour.gold(), timestamp=message.created_at)
        e.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        e.add_field(name="Source", value=f"[Jump to message]({message.jump_url}) · {message.channel.mention}")
        image = next((a for a in message.attachments if (a.content_type or "").startswith("image")), None)
        if image:
            e.set_image(url=image.url)
        elif message.embeds and message.embeds[0].image:
            e.set_image(url=message.embeds[0].image.url)
        e.set_footer(text=f"{stars} {emoji}")
        return e

    async def update(self, payload):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        s = self.s(payload.guild_id)
        if guild is None or not s.get("enabled") or not s.get("channel"):
            return
        emoji = s.get("emoji") or "⭐"
        if payload.emoji is not None and str(payload.emoji) != emoji:
            return
        star_channel = guild.get_channel(int(s["channel"]))
        source = guild.get_channel_or_thread(payload.channel_id)
        if not isinstance(star_channel, discord.TextChannel) or source is None:
            return
        lock = self.locks.setdefault(payload.message_id, asyncio.Lock())
        async with lock:
            try:
                message = await source.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            if not self.eligible(message, s, star_channel):
                return
            stars = await self.count(message, emoji, s.get("self_star"))
            row = self.db.fetchone("SELECT * FROM starboard_posts WHERE guild_id=? AND message_id=?", (guild.id, message.id))
            threshold = int(s.get("threshold") or 3)
            if stars >= threshold:
                if row and row["star_message_id"]:
                    try:
                        post = await star_channel.fetch_message(row["star_message_id"])
                        await post.edit(embed=self.star_embed(message, stars, emoji))
                        self.db.execute("UPDATE starboard_posts SET stars=? WHERE id=?", (stars, row["id"]))
                        return
                    except discord.NotFound:
                        pass
                    except discord.HTTPException:
                        return
                post = await ui.safe_send(star_channel, content=f"{emoji} **{stars}** · {message.channel.mention}", embed=self.star_embed(message, stars, emoji))
                if post is None:
                    return
                self.db.execute("INSERT INTO starboard_posts(guild_id, channel_id, message_id, author_id, star_channel_id, star_message_id, stars, created_ts) "
                                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(guild_id, message_id) DO UPDATE SET star_message_id=excluded.star_message_id, "
                                "star_channel_id=excluded.star_channel_id, stars=excluded.stars",
                                (guild.id, message.channel.id, message.id, message.author.id, star_channel.id, post.id, stars, time.time()))
            elif row and row["star_message_id"]:
                if s.get("remove_below", True):
                    try:
                        post = await star_channel.fetch_message(row["star_message_id"])
                        await post.delete()
                    except discord.HTTPException:
                        pass
                    self.db.execute("UPDATE starboard_posts SET star_message_id=NULL, stars=? WHERE id=?", (stars, row["id"]))
                else:
                    self.db.execute("UPDATE starboard_posts SET stars=? WHERE id=?", (stars, row["id"]))
        if len(self.locks) > 500:
            self.locks.clear()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.update(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.update(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload):
        payload.emoji = None
        await self.update(payload)

    # ---------------------------------------------------------------- message of the day
    @tasks.loop(minutes=5)
    async def motd_loop(self):
        for guild in self.bot.guilds:
            s = self.s(guild.id)
            if not (s.get("enabled") and s.get("message_of_day") and s.get("channel")):
                continue
            try:
                tz = get_tz(s.get("timezone") or "Asia/Kolkata")
            except ValueError:
                continue
            now = datetime.now(tz)
            if now.hour < 12:
                continue
            try:
                self.db.execute("INSERT INTO motd_posts(guild_id, day) VALUES (?,?)", (guild.id, now.date().isoformat()))
            except Exception:
                continue
            row = self.db.fetchone("SELECT * FROM starboard_posts WHERE guild_id=? AND created_ts>=? ORDER BY stars DESC LIMIT 1", (guild.id, time.time() - 86400))
            if row:
                channel = guild.get_channel(int(s["channel"]))
                await ui.safe_send(channel, f"🌟 **Featured message of the day** — {row['stars']} stars, by <@{row['author_id']}>: {ui.jump(guild.id, row['channel_id'], row['message_id'])}")

    @motd_loop.before_loop
    async def before_motd(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------------- bookmarks
    async def save_bookmark(self, user, message, note=""):
        self.db.insert("INSERT INTO bookmarks(user_id, guild_id, channel_id, message_id, author_id, excerpt, note, created_ts) VALUES (?,?,?,?,?,?,?,?)",
                       (user.id, message.guild.id if message.guild else None, message.channel.id, message.id, message.author.id, (message.content or "")[:300], note[:200], time.time()))
        e = discord.Embed(description=ui.clip(message.content, 1500) or "*attachment/embed*", colour=ui.PINK, timestamp=message.created_at)
        e.set_author(name=f"🔖 Bookmark · {message.author.display_name}", icon_url=message.author.display_avatar.url)
        e.add_field(name="Link", value=f"[Jump to message]({message.jump_url})")
        return await ui.safe_dm(user, embed=e)

    async def bookmark_menu_cb(self, interaction: discord.Interaction, message: discord.Message):
        sent = await self.save_bookmark(interaction.user, message)
        await interaction.response.send_message("🔖 Saved! I sent you a copy by DM." if sent else "🔖 Saved to `!bookmarks` (I couldn't DM you a copy).", ephemeral=True)

    @commands.command(name="save", aliases=["bookmark"])
    @commands.guild_only()
    async def save(self, ctx, link: str = None, *, note: str = ""):
        """Bookmark a message (reply to it, or give its link). The copy is DM'd only to you."""
        message = None
        if ctx.message.reference and ctx.message.reference.message_id:
            try:
                message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except discord.HTTPException:
                message = None
            note = f"{link or ''} {note}".strip()
        elif link and self.bot.get_cog("Utility"):
            message = await self.bot.get_cog("Utility").resolve_link(ctx, link)
        if message is None:
            return await ui.reply(ctx, embed=ui.err("Reply to a message with `!save`, or use `!save <message link>`."))
        sent = await self.save_bookmark(ctx.author, message, note)
        await ui.reply(ctx, embed=ui.ok("Bookmarked! Check your DMs." if sent else "Bookmarked (enable DMs to receive copies). See `!bookmarks`."))

    @commands.command(name="bookmarks")
    async def bookmarks(self, ctx):
        """Your saved messages (sent by DM)."""
        rows = self.db.fetchall("SELECT * FROM bookmarks WHERE user_id=? ORDER BY id DESC LIMIT 50", (ctx.author.id,))
        lines = [f"`{r['id']}` [{ui.clip(r['excerpt'] or 'message', 60)}]({ui.jump(r['guild_id'], r['channel_id'], r['message_id'])})" + (f" — {r['note']}" if r["note"] else "") for r in rows]
        text = "\n".join(lines) or "No bookmarks yet. Right-click a message → Apps → **Bookmark message**."
        if await ui.safe_dm(ctx.author, embed=ui.embed("🔖 Your bookmarks", ui.clip(text, 4000))):
            if ctx.guild:
                await ui.reply(ctx, embed=ui.ok("Sent to your DMs."))
        else:
            await ui.reply(ctx, embed=ui.err("I couldn't DM you."))

    @commands.command(name="unbookmark")
    async def unbookmark(self, ctx, bookmark_id: int):
        n = self.db.execute("DELETE FROM bookmarks WHERE id=? AND user_id=?", (bookmark_id, ctx.author.id)).rowcount
        await ui.reply(ctx, embed=ui.ok("Removed.") if n else ui.err("No bookmark of yours with that ID."))

    @commands.command(name="starboardtop", aliases=["startop"])
    @commands.guild_only()
    async def starboardtop(self, ctx):
        rows = self.db.fetchall("SELECT * FROM starboard_posts WHERE guild_id=? ORDER BY stars DESC LIMIT 10", (ctx.guild.id,))
        await ui.reply(ctx, embed=ui.embed("⭐ Most-starred messages", "\n".join(f"**{r['stars']}** · <@{r['author_id']}> · [jump]({ui.jump(ctx.guild.id, r['channel_id'], r['message_id'])})" for r in rows) or "Nothing starred yet."))


async def setup(bot):
    await bot.add_cog(Starboard(bot))
