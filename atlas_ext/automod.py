"""Automod: blocked words, spam, mention spam, duplicates, links and invites.

Safety design: automod only deletes the offending message, posts a short notice, logs it and adds a
review item for staff. Moderators/admins, bots, exempt roles and exempt channels are never touched.
An optional short timeout exists ONLY for unambiguous flooding/mention-spam and is OFF by default.
"""
import re
import time
from datetime import timedelta
from collections import defaultdict, deque

import discord
from discord.ext import commands

from common import checks, ui
from common.config import Module, Option

from .moderation import add_review_item

LINK_RE = re.compile(r"https?://([^\s/<>]+)", re.I)
INVITE_RE = re.compile(r"(discord\.gg|discord(?:app)?\.com/invite|dsc\.gg)/[A-Za-z0-9-]+", re.I)


def normalise(text):
    text = text.casefold()
    table = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
    return re.sub(r"[^a-z0-9\s]", "", text.translate(table))


def word_hit(content, words):
    norm = normalise(content)
    tokens = set(norm.split())
    squashed = norm.replace(" ", "")
    for w in words:
        w = normalise(w).strip()
        if not w:
            continue
        if " " in w:
            if w in norm:
                return w
        elif w in tokens or (len(w) >= 5 and w in squashed):
            return w
    return None


class AutoMod(commands.Cog, name="AutoMod"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.recent = defaultdict(lambda: deque(maxlen=30))   # (guild, user) -> deque[(ts, normalized content)]
        self.notice_cooldown = {}
        bot.db.migrate("automod_001", """
            CREATE TABLE IF NOT EXISTS automod_words (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, word TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_automod_words ON automod_words(guild_id, word)""")
        bot.registry.register(Module("automod", "AutoMod", "🤖", "Filters for blocked words, spam, mass mentions, duplicates, links and invites. Staff and bots are always exempt.", [
            Option("blocked_words", "Blocked-word filter", "bool", True, "Manage words with !automod words"),
            Option("spam", "Spam filter", "bool", True), Option("spam_count", "Spam: messages", "int", 6, "messages within the window", 3, 30),
            Option("spam_seconds", "Spam: window (s)", "int", 8, "", 2, 60),
            Option("mentions", "Mention-spam filter", "bool", True), Option("mention_limit", "Max mentions per message", "int", 6, "", 2, 50),
            Option("duplicates", "Duplicate filter", "bool", True), Option("duplicate_count", "Same message repeats", "int", 4, "", 2, 20),
            Option("links", "Block links", "bool", False, "Blocks all links except allowed domains"),
            Option("allowed_domains", "Allowed domains", "text", "youtube.com, youtu.be, spotify.com, soundcloud.com, tenor.com, giphy.com, discord.com", max_length=800),
            Option("invites", "Block other servers' invites", "bool", True),
            Option("exempt_roles", "Exempt roles", "roles", []), Option("exempt_channels", "Exempt channels", "channels", []),
            Option("timeout_floods", "Short timeout for floods", "bool", False, "5 min timeout for clear flooding/mass mentions only"),
        ]))

    def words(self, guild_id):
        return [r["word"] for r in self.db.fetchall("SELECT word FROM automod_words WHERE guild_id=?", (guild_id,))]

    def exempt(self, message, s):
        member = message.author
        if not isinstance(member, discord.Member) or member.bot or checks.is_mod(member):
            return True
        if message.channel.id in {int(c) for c in s.get("exempt_channels") or []}:
            return True
        parent = getattr(message.channel, "parent_id", None)
        if parent and parent in {int(c) for c in s.get("exempt_channels") or []}:
            return True
        roles = {int(r) for r in s.get("exempt_roles") or []}
        return any(r.id in roles for r in member.roles)

    def check(self, message, s):
        """Return (rule, detail, flood) for the first rule broken, else None. Pure logic, no side effects."""
        content = message.content or ""
        key = (message.guild.id, message.author.id)
        now = time.time()
        hist = self.recent[key]
        norm = normalise(content)
        hist.append((now, norm))
        if s.get("blocked_words"):
            hit = word_hit(content, self.words(message.guild.id))
            if hit:
                return "blocked word", f"matched `{hit}`", False
        if s.get("mentions"):
            count = len(message.raw_mentions) + len(message.raw_role_mentions) + (5 if message.mention_everyone else 0)
            if count >= int(s.get("mention_limit") or 6):
                return "mention spam", f"{count} mentions in one message", True
        if s.get("invites") and INVITE_RE.search(content):
            return "invite link", "invite to another server", False
        if s.get("links"):
            allowed = [d.strip().casefold() for d in str(s.get("allowed_domains") or "").split(",") if d.strip()]
            for host in LINK_RE.findall(content):
                host = host.casefold().split(":")[0]
                if not any(host == d or host.endswith("." + d) for d in allowed):
                    return "link", f"link to `{host}`", False
        if s.get("spam"):
            window = int(s.get("spam_seconds") or 8)
            if sum(1 for ts, _ in hist if now - ts <= window) >= int(s.get("spam_count") or 6):
                return "spam", f"{s.get('spam_count')}+ messages in {window}s", True
        if s.get("duplicates") and norm and len(norm) > 3:
            same = sum(1 for ts, c in hist if c == norm and now - ts <= 120)
            if same >= int(s.get("duplicate_count") or 4):
                return "duplicate messages", f"same message {same}× in 2 minutes", False
        return None

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot or not message.content:
            return
        s = self.bot.registry.get(message.guild.id, "automod")
        if not s.get("enabled") or self.exempt(message, s):
            return
        result = self.check(message, s)
        if not result:
            return
        rule, detail, flood = result
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        key = (message.guild.id, message.author.id)
        if time.time() - self.notice_cooldown.get(key, 0) > 20:
            self.notice_cooldown[key] = time.time()
            await ui.safe_send(message.channel, f"{message.author.mention} your message was removed by automod ({rule}).",
                               delete_after=8, allowed_mentions=discord.AllowedMentions(users=True))
        timed_out = False
        if flood and s.get("timeout_floods") and message.guild.me.guild_permissions.moderate_members and \
                checks.hierarchy_problem(None, message.author, message.guild.me) is None:
            try:
                await message.author.timeout(timedelta(minutes=5), reason=f"Automod: {rule} ({detail})")
                timed_out = True
                self.recent[key].clear()
            except discord.HTTPException:
                pass
        e = discord.Embed(title=f"🤖 Automod · {rule}", colour=ui.WARN, description=f"{message.author.mention} in {message.channel.mention}\n{detail}" + ("\n⏳ 5-minute timeout applied" if timed_out else ""))
        self.bot.dispatch("bot_log", message.guild, "automod", e)
        await add_review_item(self.bot, message.guild, f"automod: {rule}", f"{message.author} — {detail}" + (" (5-minute flood timeout applied)" if timed_out else ""),
                              target_id=message.author.id, link=message.channel.jump_url)

    # ---------------------------------------------------------------- commands
    @commands.group(name="automod", invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_only()
    async def automod(self, ctx):
        """Automod settings (opens the config panel)."""
        from common.config import open_panel
        await open_panel(ctx, self.bot.registry, "automod")

    @automod.group(name="words", invoke_without_command=True)
    async def words_group(self, ctx):
        words = self.words(ctx.guild.id)
        try:
            await ctx.author.send(f"Blocked words for **{ctx.guild.name}** ({len(words)}): " + (", ".join(f"`{w}`" for w in words) or "none"))
            await ui.reply(ctx, embed=ui.ok("Sent the list to your DMs (kept out of the channel)."))
        except discord.Forbidden:
            await ui.reply(ctx, embed=ui.err("I couldn't DM you the list."))

    @words_group.command(name="add")
    async def words_add(self, ctx, *, words: str):
        """Add comma-separated words/phrases."""
        added = 0
        for w in [x.strip().casefold() for x in words.split(",") if x.strip()][:100]:
            try:
                self.db.execute("INSERT INTO automod_words(guild_id, word) VALUES (?,?)", (ctx.guild.id, w[:100]))
                added += 1
            except Exception:
                pass
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ui.safe_send(ctx.channel, embed=ui.ok(f"Added {added} blocked word(s)."))

    @words_group.command(name="remove")
    async def words_remove(self, ctx, *, words: str):
        removed = 0
        for w in [x.strip().casefold() for x in words.split(",") if x.strip()]:
            removed += self.db.execute("DELETE FROM automod_words WHERE guild_id=? AND word=?", (ctx.guild.id, w)).rowcount
        await ui.reply(ctx, embed=ui.ok(f"Removed {removed} word(s)."))

    @automod.command(name="test")
    async def test(self, ctx, *, text: str):
        """Show which rule (if any) a sample message would trigger. Nothing is deleted."""
        s = self.bot.registry.get(ctx.guild.id, "automod")
        fake = type("Fake", (), {"content": text, "guild": ctx.guild, "author": type("A", (), {"id": -1})(),
                                 "raw_mentions": re.findall(r"<@!?(\d+)>", text), "raw_role_mentions": re.findall(r"<@&(\d+)>", text),
                                 "mention_everyone": "@everyone" in text or "@here" in text})()
        result = self.check(fake, s)
        self.recent.pop((ctx.guild.id, -1), None)
        await ui.reply(ctx, embed=ui.embed(description=f"Would trigger: **{result[0]}** ({result[1]})" if result else "No rule would trigger. ✅"))


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
