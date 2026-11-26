"""Channel and message tools: info, topic, slowmode, lock/unlock (timed), cleanup with preview,
threads (create/archive/auto-thread), forum posts, pins, search, jump links and sticky messages.

Destructive actions always show a preview and need confirmation. NOVA never deletes channels here.
Search runs only over channels the person asking can read, and results go to them by DM.
"""
import asyncio
import json
import re
import time
from datetime import timedelta

import discord
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option
from common.timeparse import fmt_delta, parse_duration

BULK_LIMIT = timedelta(days=14) - timedelta(minutes=2)


class Channels(commands.Cog, name="Channels"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.sticky_tasks = {}
        bot.db.migrate("channels_001", """
            CREATE TABLE IF NOT EXISTS channel_locks (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, unlock_ts REAL, previous TEXT, actor_id INTEGER);
            CREATE TABLE IF NOT EXISTS sticky_messages (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, content TEXT NOT NULL, last_message_id INTEGER,
                author_id INTEGER);
            CREATE TABLE IF NOT EXISTS autothread_channels (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, name_template TEXT NOT NULL DEFAULT '{author}: {preview}',
                archive_minutes INTEGER DEFAULT 1440)""")
        bot.registry.register(Module("channels", "Channel tools", "🧰", "Sticky messages, auto-threads and channel/message utilities.", [
            Option("sticky_delay", "Sticky repost delay (s)", "int", 8, "Waits for chat to settle before reposting", 3, 120),
            Option("slowmode_notice", "Post a notice when slowmode changes", "bool", True),
        ], default_enabled=True))

    async def cog_load(self):
        self.unlock_loop.start()

    async def cog_unload(self):
        self.unlock_loop.cancel()

    # ---------------------------------------------------------------- info
    @commands.command(name="mchannelinfo", aliases=["mchinfo"])
    @commands.guild_only()
    async def mchannelinfo(self, ctx, channel: discord.abc.GuildChannel = None):
        """Information about a channel."""
        channel = channel or ctx.channel
        e = discord.Embed(title=f"#{channel.name}", colour=ui.INFO)
        e.add_field(name="Type", value=str(channel.type).replace("_", " "))
        e.add_field(name="ID", value=f"`{channel.id}`")
        e.add_field(name="Created", value=discord.utils.format_dt(channel.created_at, "D"))
        if channel.category:
            e.add_field(name="Category", value=channel.category.name)
        if getattr(channel, "topic", None):
            e.add_field(name="Topic", value=ui.clip(channel.topic, 1000), inline=False)
        if isinstance(channel, discord.TextChannel):
            e.add_field(name="Slowmode", value=f"{channel.slowmode_delay}s" if channel.slowmode_delay else "off")
            e.add_field(name="Age-restricted", value="yes" if channel.nsfw else "no")
            e.add_field(name="Threads", value=str(len(channel.threads)))
        if isinstance(channel, discord.VoiceChannel):
            e.add_field(name="Members now", value=str(len(channel.members)))
            e.add_field(name="Limit", value=str(channel.user_limit or "none"))
        e.add_field(name="Permission overwrites", value=str(len(channel.overwrites)))
        if self.db.fetchone("SELECT 1 FROM sticky_messages WHERE channel_id=?", (channel.id,)):
            e.add_field(name="Sticky message", value="yes")
        if self.db.fetchone("SELECT 1 FROM autothread_channels WHERE channel_id=?", (channel.id,)):
            e.add_field(name="Auto-threads", value="on")
        await ui.reply(ctx, embed=e)

    @commands.command(name="mtopic", aliases=["mdescription"])
    @commands.guild_only()
    @checks.perm_only("manage_channels", "Manage Channels")
    async def mtopic(self, ctx, channel: discord.TextChannel = None, *, text: str = ""):
        """Set (or clear) a channel's description/topic."""
        channel = channel or ctx.channel
        await channel.edit(topic=text[:1024] or None, reason=f"Topic set by {ctx.author}")
        await ui.reply(ctx, embed=ui.ok("Topic updated." if text else "Topic cleared."))

    @commands.command(name="mslowmode")
    @commands.guild_only()
    @checks.perm_only("manage_channels", "Manage Channels")
    async def mslowmode(self, ctx, duration: str = "0", channel: discord.TextChannel = None):
        """Set slowmode: `!mslowmode 10s`, `!mslowmode 2m`, `!mslowmode off`."""
        channel = channel or ctx.channel
        seconds = 0 if duration.casefold() in ("0", "off", "none") else (parse_duration(duration if not duration.isdigit() else f"{duration}s", minimum=1, maximum=21600) or timedelta(seconds=-1)).total_seconds()
        if seconds < 0:
            return await ui.reply(ctx, embed=ui.err("Use e.g. `10s`, `2m`, `1h` (max 6h) or `off`."))
        await channel.edit(slowmode_delay=int(seconds), reason=f"Slowmode by {ctx.author}")
        if self.bot.registry.get(ctx.guild.id, "channels").get("slowmode_notice", True) and channel != ctx.channel:
            await ui.safe_send(channel, embed=ui.embed(description=f"🐢 Slowmode is now **{fmt_delta(seconds)}** per message." if seconds else "🐇 Slowmode is off."))
        await ui.reply(ctx, embed=ui.ok(f"Slowmode in {channel.mention}: **{fmt_delta(seconds) if seconds else 'off'}**."))

    # ---------------------------------------------------------------- lock
    async def set_lock(self, channel, locked, actor, unlock_ts=None):
        everyone = channel.guild.default_role
        overwrite = channel.overwrites_for(everyone)
        if locked:
            prev = {"send_messages": overwrite.send_messages, "send_messages_in_threads": overwrite.send_messages_in_threads,
                    "create_public_threads": overwrite.create_public_threads, "add_reactions": overwrite.add_reactions}
            self.db.execute("INSERT OR REPLACE INTO channel_locks(channel_id, guild_id, unlock_ts, previous, actor_id) VALUES (?,?,?,?,?)",
                            (channel.id, channel.guild.id, unlock_ts, json.dumps(prev), actor.id if actor else None))
            overwrite.update(send_messages=False, send_messages_in_threads=False, create_public_threads=False, add_reactions=False)
            me_ow = channel.overwrites_for(channel.guild.me)
            me_ow.update(send_messages=True)
            await channel.set_permissions(channel.guild.me, overwrite=me_ow, reason="Keep NOVA able to post in locked channel")
        else:
            row = self.db.fetchone("SELECT previous FROM channel_locks WHERE channel_id=?", (channel.id,))
            prev = json.loads(row["previous"]) if row and row["previous"] else {"send_messages": None, "send_messages_in_threads": None, "create_public_threads": None, "add_reactions": None}
            overwrite.update(**prev)
            self.db.execute("DELETE FROM channel_locks WHERE channel_id=?", (channel.id,))
        await channel.set_permissions(everyone, overwrite=None if overwrite.is_empty() else overwrite, reason=f"Channel {'locked' if locked else 'unlocked'} by {actor or 'timer'}")

    @commands.command(name="mlock")
    @commands.guild_only()
    @checks.perm_only("manage_channels", "Manage Channels")
    async def mlock(self, ctx, channel: discord.TextChannel = None, duration: str = None, *, reason: str = ""):
        """Lock a channel for @everyone (staff with their own permissions can still talk). Optional duration: `!mlock #ch 30m`."""
        channel = channel or ctx.channel
        delta = parse_duration(duration, minimum=60, maximum=30 * 86400) if duration else None
        if duration and delta is None:
            reason = f"{duration} {reason}".strip()
        if self.db.fetchone("SELECT 1 FROM channel_locks WHERE channel_id=?", (channel.id,)):
            return await ui.reply(ctx, embed=ui.err("That channel is already locked by NOVA."))
        unlock_ts = time.time() + delta.total_seconds() if delta else None
        await self.set_lock(channel, True, ctx.author, unlock_ts)
        await ui.safe_send(channel, embed=discord.Embed(description="🔒 This channel is temporarily locked." + (f" Unlocks <t:{int(unlock_ts)}:R>." if unlock_ts else "") + (f"\nReason: {reason}" if reason else ""), colour=ui.WARN))
        if channel != ctx.channel:
            await ui.reply(ctx, embed=ui.ok(f"Locked {channel.mention}."))

    @commands.command(name="munlock")
    @commands.guild_only()
    @checks.perm_only("manage_channels", "Manage Channels")
    async def munlock(self, ctx, channel: discord.TextChannel = None):
        """Unlock a channel (restores the previous @everyone settings)."""
        channel = channel or ctx.channel
        await self.set_lock(channel, False, ctx.author)
        await ui.safe_send(channel, embed=discord.Embed(description="🔓 Channel unlocked.", colour=ui.GOOD))
        if channel != ctx.channel:
            await ui.reply(ctx, embed=ui.ok(f"Unlocked {channel.mention}."))

    @tasks.loop(seconds=30)
    async def unlock_loop(self):
        for row in self.db.fetchall("SELECT * FROM channel_locks WHERE unlock_ts IS NOT NULL AND unlock_ts<=?", (time.time(),)):
            guild = self.bot.get_guild(row["guild_id"])
            channel = guild.get_channel(row["channel_id"]) if guild else None
            if channel is None:
                self.db.execute("DELETE FROM channel_locks WHERE channel_id=?", (row["channel_id"],))
                continue
            try:
                await self.set_lock(channel, False, None)
                await ui.safe_send(channel, embed=discord.Embed(description="🔓 Timed lock ended — channel unlocked.", colour=ui.GOOD))
            except discord.HTTPException:
                self.db.execute("UPDATE channel_locks SET unlock_ts=? WHERE channel_id=?", (time.time() + 300, row["channel_id"]))

    @unlock_loop.before_loop
    async def before_unlock(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------------- cleanup
    @commands.command(name="mcleanup", aliases=["mpurge"])
    @commands.guild_only()
    @checks.perm_only("manage_messages", "Manage Messages")
    async def mcleanup(self, ctx, amount: int = 50, *, filters: str = ""):
        """Preview and delete recent messages. Filters: bots · commands · @member · contains:<text> · links · attachments · humans
        Example: `!mcleanup 100 bots` or `!mcleanup 200 contains:spam`. Pinned messages are always kept."""
        if not 1 <= amount <= 1000:
            return await ui.reply(ctx, embed=ui.err("Choose 1–1000 messages to scan."))
        perms = ctx.channel.permissions_for(ctx.guild.me)
        if not (perms.manage_messages and perms.read_message_history):
            return await ui.reply(ctx, embed=ui.err("I need Manage Messages and Read Message History here."))
        f = filters.casefold()
        member_ids = {int(x) for x in re.findall(r"<@!?(\d+)>", filters)}
        contains = re.search(r"contains:(\S+)", filters, re.I)
        prefixes = tuple({"!", "?", ".", "$", "k"})

        def check(m):
            if m.pinned or m.id == ctx.message.id:
                return False
            if member_ids and m.author.id not in member_ids:
                return False
            if "bots" in f and not m.author.bot:
                return False
            if "humans" in f and m.author.bot:
                return False
            if "commands" in f and not (m.content.startswith(prefixes) or (m.author.bot and m.reference)):
                return False
            if "links" in f and not re.search(r"https?://", m.content):
                return False
            if "attachments" in f and not m.attachments:
                return False
            if contains and contains.group(1).casefold() not in m.content.casefold():
                return False
            return True

        cutoff = discord.utils.utcnow() - BULK_LIMIT
        matched = [m async for m in ctx.channel.history(limit=amount, after=cutoff) if check(m)]
        if not matched:
            return await ui.reply(ctx, embed=ui.ok("Nothing matches (Discord only allows bulk-deleting messages younger than 14 days)."))
        authors = {}
        for m in matched:
            authors[m.author.display_name] = authors.get(m.author.display_name, 0) + 1
        summary = ", ".join(f"{n} ({c})" for n, c in sorted(authors.items(), key=lambda kv: -kv[1])[:8])
        if not await ui.confirm(ctx, f"Delete **{len(matched)}** message(s) from the last {amount} scanned?\nBy: {summary}", confirm_label="Delete"):
            return
        ids = {m.id for m in matched}
        deleted = await ctx.channel.purge(limit=amount + 5, check=lambda m: m.id in ids, after=cutoff, bulk=True, reason=f"Cleanup by {ctx.author}")
        await ctx.send(embed=ui.ok(f"Deleted {len(deleted)} message(s)."), delete_after=6)

    # ---------------------------------------------------------------- threads & forums
    @commands.command(name="mthread")
    @commands.guild_only()
    async def mthread(self, ctx, *, name: str = ""):
        """Start a thread (reply to a message to thread it). `!mthread Topic name`"""
        if not isinstance(ctx.channel, discord.TextChannel):
            return await ui.reply(ctx, embed=ui.err("Threads can be started in text channels."))
        if not ctx.channel.permissions_for(ctx.author).create_public_threads:
            return await ui.reply(ctx, embed=ui.err("You can't create threads here."))
        base = None
        if ctx.message.reference and ctx.message.reference.message_id:
            try:
                base = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except discord.HTTPException:
                pass
        name = (name or (base.content[:80] if base and base.content else f"{ctx.author.display_name}'s thread"))[:100]
        try:
            thread = await (base.create_thread(name=name) if base else ctx.channel.create_thread(name=name, type=discord.ChannelType.public_thread))
        except discord.HTTPException as exc:
            return await ui.reply(ctx, embed=ui.err(f"Couldn't create the thread: {exc}"))
        await ui.reply(ctx, embed=ui.ok(f"Thread {thread.mention} created."))

    @commands.command(name="marchive")
    @commands.guild_only()
    @checks.perm_only("manage_threads", "Manage Threads")
    async def marchive(self, ctx, channel: discord.TextChannel = None, inactive: str = None):
        """Archive this thread, or `!marchive #channel 7d` to archive that channel's threads inactive for 7 days."""
        if channel is None and isinstance(ctx.channel, discord.Thread):
            await ui.reply(ctx, embed=ui.ok("Archiving this thread."))
            return await ctx.channel.edit(archived=True, reason=f"Archived by {ctx.author}")
        channel = channel or ctx.channel
        delta = parse_duration(inactive or "7d", minimum=3600)
        if delta is None or not isinstance(channel, discord.TextChannel):
            return await ui.reply(ctx, embed=ui.err("Use `!marchive #channel 7d`."))
        cutoff = discord.utils.utcnow() - delta
        stale = []
        for t in channel.threads:
            last = discord.utils.snowflake_time(t.last_message_id) if t.last_message_id else t.created_at
            if last and last < cutoff:
                stale.append(t)
        if not stale:
            return await ui.reply(ctx, embed=ui.ok("No inactive open threads."))
        if not await ui.confirm(ctx, f"Archive {len(stale)} thread(s) in {channel.mention} inactive for {fmt_delta(delta.total_seconds())}? (They stay readable and can be reopened.)", confirm_label="Archive", danger=False):
            return
        done = 0
        for t in stale:
            try:
                await t.edit(archived=True, reason=f"Stale thread archive by {ctx.author}")
                done += 1
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.4)
        await ui.reply(ctx, embed=ui.ok(f"Archived {done} thread(s)."))

    @commands.command(name="mautothread")
    @commands.guild_only()
    @checks.perm_only("manage_channels", "Manage Channels")
    async def mautothread(self, ctx, channel: discord.TextChannel = None, state: str = "on"):
        """Automatically open a thread on every new message in a channel: `!mautothread #channel on|off`"""
        channel = channel or ctx.channel
        if state.casefold() == "off":
            self.db.execute("DELETE FROM autothread_channels WHERE channel_id=?", (channel.id,))
            return await ui.reply(ctx, embed=ui.ok(f"Auto-threads off in {channel.mention}."))
        if not channel.permissions_for(ctx.guild.me).create_public_threads:
            return await ui.reply(ctx, embed=ui.err(f"I can't create threads in {channel.mention}."))
        self.db.execute("INSERT OR REPLACE INTO autothread_channels(channel_id, guild_id) VALUES (?,?)", (channel.id, ctx.guild.id))
        await ui.reply(ctx, embed=ui.ok(f"Every new message in {channel.mention} now gets its own thread."))

    @commands.command(name="mforumpost")
    @commands.guild_only()
    async def mforumpost(self, ctx, forum: discord.ForumChannel, *, text: str):
        """Create a forum post: `!mforumpost #forum Title | body`"""
        if not forum.permissions_for(ctx.author).send_messages:
            return await ui.reply(ctx, embed=ui.err("You can't post in that forum."))
        title, _, body = text.partition("|")
        if not body.strip():
            return await ui.reply(ctx, embed=ui.err("Use `Title | body`."))
        try:
            created = await forum.create_thread(name=title.strip()[:100], content=f"{body.strip()[:1900]}\n\n— posted for {ctx.author.mention}",
                                                allowed_mentions=discord.AllowedMentions.none(), reason=f"Forum post by {ctx.author}")
        except discord.HTTPException as exc:
            return await ui.reply(ctx, embed=ui.err(f"Couldn't create the post: {exc}"))
        await ui.reply(ctx, embed=ui.ok(f"Posted: {created.thread.mention}"))

    # ---------------------------------------------------------------- message utilities
    @commands.command(name="mpins")
    @commands.guild_only()
    async def mpins(self, ctx, channel: discord.TextChannel = None):
        """List pinned messages with jump links."""
        channel = channel or ctx.channel
        if not channel.permissions_for(ctx.author).read_message_history:
            return await ui.reply(ctx, embed=ui.err("You can't read that channel."))
        pins = await channel.pins()
        lines = [f"[{ui.clip(p.content or 'attachment/embed', 70)}]({p.jump_url}) — {p.author.display_name}" for p in pins]
        await ui.paginate(ctx, ui.pages_from_lines(f"📌 Pins in #{channel.name} ({len(pins)}/50)", lines, per_page=10))

    @commands.command(name="msearch")
    @commands.guild_only()
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def msearch(self, ctx, channel: discord.TextChannel = None, *, query: str):
        """Search recent messages (last 1000) in a channel you can read; results are DM'd to you."""
        channel = channel or ctx.channel
        if not (channel.permissions_for(ctx.author).read_message_history and channel.permissions_for(ctx.author).view_channel):
            return await ui.reply(ctx, embed=ui.err("You can't read that channel."))
        if len(query) < 2:
            return await ui.reply(ctx, embed=ui.err("Search for at least 2 characters."))
        q = query.casefold()
        hits = []
        async for m in channel.history(limit=1000):
            if q in m.content.casefold():
                hits.append(f"[{m.created_at:%d %b %H:%M}]({m.jump_url}) {m.author.display_name}: {ui.clip(m.content, 80)}")
                if len(hits) >= 25:
                    break
        text = "\n".join(hits) or "No matches in the last 1000 messages."
        if await ui.safe_dm(ctx.author, embed=ui.embed(f"🔎 “{ui.clip(query, 40)}” in #{channel.name}", ui.clip(text, 4000))):
            await ui.reply(ctx, embed=ui.ok(f"Found {len(hits)} match(es); sent to your DMs."))
        else:
            await ui.reply(ctx, embed=ui.err("I couldn't DM you the results."))

    @commands.command(name="mjump")
    @commands.guild_only()
    async def mjump(self, ctx, channel: discord.TextChannel = None, message_id: int = None):
        """Jump link to a message by ID, or to the first message of a channel."""
        channel = channel or ctx.channel
        if message_id:
            return await ui.reply(ctx, embed=ui.embed(description=f"[Jump to message]({ui.jump(ctx.guild.id, channel.id, message_id)})"))
        async for first in channel.history(limit=1, oldest_first=True):
            return await ui.reply(ctx, embed=ui.embed(description=f"[First message in #{channel.name}]({first.jump_url})"))
        await ui.reply(ctx, embed=ui.err("That channel is empty."))

    # ---------------------------------------------------------------- sticky messages
    @commands.command(name="msticky")
    @commands.guild_only()
    @checks.perm_only("manage_messages", "Manage Messages")
    async def msticky(self, ctx, *, text: str = ""):
        """Keep a message at the bottom of this channel: `!msticky <text>`, `!msticky off`."""
        if text.casefold() in ("off", "remove", "clear"):
            row = self.db.fetchone("SELECT last_message_id FROM sticky_messages WHERE channel_id=?", (ctx.channel.id,))
            self.db.execute("DELETE FROM sticky_messages WHERE channel_id=?", (ctx.channel.id,))
            if row and row["last_message_id"]:
                try:
                    await ctx.channel.get_partial_message(row["last_message_id"]).delete()
                except discord.HTTPException:
                    pass
            return await ui.reply(ctx, embed=ui.ok("Sticky message removed."))
        if not text:
            return await ui.reply(ctx, embed=ui.err("Give the sticky text, or `off`."))
        msg = await ctx.channel.send(embed=discord.Embed(description=f"📌 {text[:3900]}", colour=ui.PINK))
        self.db.execute("INSERT OR REPLACE INTO sticky_messages(channel_id, guild_id, content, last_message_id, author_id) VALUES (?,?,?,?,?)",
                        (ctx.channel.id, ctx.guild.id, text[:3900], msg.id, ctx.author.id))
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    async def repost_sticky(self, channel, delay):
        await asyncio.sleep(delay)
        row = self.db.fetchone("SELECT * FROM sticky_messages WHERE channel_id=?", (channel.id,))
        if not row:
            return
        if row["last_message_id"]:
            try:
                await channel.get_partial_message(row["last_message_id"]).delete()
            except discord.HTTPException:
                pass
        msg = await ui.safe_send(channel, embed=discord.Embed(description=f"📌 {row['content']}", colour=ui.PINK))
        if msg:
            self.db.execute("UPDATE sticky_messages SET last_message_id=? WHERE channel_id=?", (msg.id, channel.id))

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None:
            return
        if not message.author.bot and isinstance(message.channel, discord.TextChannel) and message.type == discord.MessageType.default:
            row = self.db.fetchone("SELECT * FROM autothread_channels WHERE channel_id=?", (message.channel.id,))
            if row and self.bot.registry.enabled(message.guild.id, "channels"):
                preview = (message.content or "attachment")[:60]
                name = row["name_template"].replace("{author}", message.author.display_name).replace("{preview}", preview)[:100]
                try:
                    await message.create_thread(name=name, auto_archive_duration=row["archive_minutes"] or 1440)
                except discord.HTTPException:
                    pass
        if message.author.id == self.bot.user.id:
            return
        if self.db.fetchone("SELECT 1 FROM sticky_messages WHERE channel_id=?", (message.channel.id,)) and self.bot.registry.enabled(message.guild.id, "channels"):
            task = self.sticky_tasks.get(message.channel.id)
            if task and not task.done():
                task.cancel()
            delay = int(self.bot.registry.get(message.guild.id, "channels").get("sticky_delay") or 8)
            self.sticky_tasks[message.channel.id] = asyncio.create_task(self.repost_sticky(message.channel, delay))


async def setup(bot):
    await bot.add_cog(Channels(bot))
