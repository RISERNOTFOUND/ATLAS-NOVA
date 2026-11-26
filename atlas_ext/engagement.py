"""Community engagement: daily question, conversation starters, message milestones, join anniversaries,
recognition, featured messages, weekly highlights and community challenges.

Every piece is independently switchable in `!config engagement` (the whole module is off by default so
nothing new posts until an admin opts in). ATLAS's hourly vibes and birthday posts are untouched.
"""
import secrets
import time
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option
from common.diag import user_prefs
from common.timeparse import get_tz

QUESTIONS = [
    "What's a song you could listen to on repeat forever?", "What small thing made you smile today?", "If you could master any instrument overnight, which one?",
    "What's the best advice you've ever received?", "Which game would you recommend to a total beginner?", "What's a hobby you picked up recently?",
    "Describe your perfect weekend in five words.", "What's a movie everyone should watch at least once?", "Tea, coffee, or something else entirely?",
    "Which fictional character would be the best friend in real life?", "What's one skill you'd love to learn this year?", "What's the most underrated food?",
    "If our server had a theme song, what would it be?", "What's a place you'd love to visit and why?", "What's the last thing you created—art, music, code, anything?",
    "Night owl or early bird?", "What's a tiny habit that improved your life?", "Which album would you take to a desert island?",
    "What's your go-to comfort show?", "What's something you're proud of this month?", "What's your favourite way to unwind after a long day?",
    "If you could live in any era of music, which would it be?", "What's a game mechanic you love?", "Recommend one creator everyone should check out.",
    "What's the best concert or live event you've been to?", "What's a word or phrase you use way too much?", "Mountains or beach?",
    "What's a talent you have that surprises people?", "What would your walk-up song be?", "What's on your to-do list that you keep postponing?",
]
STARTERS = [
    "Hot take time: what's an opinion you'll defend forever? 🔥", "Share the last photo in your gallery you're willing to show 📸",
    "What's everyone listening to right now? 🎧", "Rate your day from 1-10 and tell us why!", "Two truths and a lie, go! 🤔",
    "If you could add one channel to this server, what would it be?", "What's a game you're playing this week? 🎮",
    "Drop your favourite emoji and explain yourself.", "Unpopular food combo that's actually great?", "What's your current obsession?",
]
MILESTONES = (100, 500, 1000, 2500, 5000, 10000, 25000, 50000)


class Engagement(commands.Cog, name="Engagement"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("engagement_001", """
            CREATE TABLE IF NOT EXISTS engagement_posts (guild_id INTEGER NOT NULL, kind TEXT NOT NULL, period TEXT NOT NULL, PRIMARY KEY (guild_id, kind, period));
            CREATE TABLE IF NOT EXISTS member_msgcount (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
                last_milestone INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id));
            CREATE TABLE IF NOT EXISTS recognitions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                by_id INTEGER NOT NULL, reason TEXT, created_ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS featured_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL, author_id INTEGER, by_id INTEGER, created_ts REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_featured_msg ON featured_messages(guild_id, message_id);
            CREATE TABLE IF NOT EXISTS custom_questions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS challenges (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', created_ts REAL NOT NULL, ends_ts REAL);
            CREATE TABLE IF NOT EXISTS challenge_entries (challenge_id INTEGER NOT NULL, user_id INTEGER NOT NULL, note TEXT, ts REAL NOT NULL,
                PRIMARY KEY (challenge_id, user_id))""")
        bot.registry.register(Module("engagement", "Community engagement", "🌟", "Daily question, milestones, anniversaries, recognition, highlights and challenges.", [
            Option("daily_question", "Daily question", "bool", False), Option("question_channel", "Question channel", "channel", None),
            Option("question_time", "Question time", "time", "18:00"), Option("timezone", "Timezone", "tz", "Asia/Kolkata"),
            Option("question_thread", "Open a thread per question", "bool", True),
            Option("milestones", "Message milestones", "bool", False), Option("anniversaries", "Join anniversaries", "bool", False),
            Option("highlights_channel", "Highlights channel", "channel", None, "Milestones, anniversaries, recognition, featured messages"),
            Option("weekly_highlights", "Weekly highlights (Sun 19:00)", "bool", False),
        ]))

    async def cog_load(self):
        self.scheduler.start()

    async def cog_unload(self):
        self.scheduler.cancel()

    def s(self, guild_id):
        return self.bot.registry.get(guild_id, "engagement")

    def highlights(self, guild):
        s = self.s(guild.id)
        return guild.get_channel(int(s["highlights_channel"])) if s.get("highlights_channel") else None

    def once(self, guild_id, kind, period):
        try:
            self.db.execute("INSERT INTO engagement_posts(guild_id, kind, period) VALUES (?,?,?)", (guild_id, kind, period))
            return True
        except Exception:
            return False

    def pick_question(self, guild_id):
        custom = [r["text"] for r in self.db.fetchall("SELECT text FROM custom_questions WHERE guild_id=?", (guild_id,))]
        pool = custom + QUESTIONS
        index = self.db.scalar("SELECT count(*) FROM engagement_posts WHERE guild_id=? AND kind='question'", (guild_id,), 0)
        return pool[index % len(pool)]

    # ---------------------------------------------------------------- scheduler
    @tasks.loop(minutes=1)
    async def scheduler(self):
        for guild in self.bot.guilds:
            s = self.s(guild.id)
            if not s.get("enabled"):
                continue
            try:
                tz = get_tz(s.get("timezone") or "Asia/Kolkata")
            except ValueError:
                continue
            now = datetime.now(tz)
            today = now.date().isoformat()
            if s.get("daily_question") and s.get("question_channel"):
                hh, mm = (int(x) for x in (s.get("question_time") or "18:00").split(":"))
                if (now.hour, now.minute) >= (hh, mm) and self.once(guild.id, "question", today):
                    channel = guild.get_channel(int(s["question_channel"]))
                    msg = await ui.safe_send(channel, embed=discord.Embed(title="❓ Question of the day", description=self.pick_question(guild.id), colour=ui.PINK))
                    if msg and s.get("question_thread", True):
                        try:
                            await msg.create_thread(name=f"Answers · {now:%d %b}", auto_archive_duration=1440)
                        except discord.HTTPException:
                            pass
            if s.get("anniversaries") and now.hour >= 10 and self.once(guild.id, "anniv-check", today):
                await self.post_anniversaries(guild, now)
            if s.get("weekly_highlights") and now.weekday() == 6 and now.hour >= 19 and self.once(guild.id, "weekly", now.strftime("%G-W%V")):
                await self.post_weekly(guild)

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    async def post_anniversaries(self, guild, now):
        channel = self.highlights(guild)
        if channel is None:
            return
        names = []
        for m in guild.members:
            if m.bot or not m.joined_at:
                continue
            joined = m.joined_at.astimezone(now.tzinfo)
            years = now.year - joined.year
            if years >= 1 and (joined.month, joined.day) == (now.month, now.day) and user_prefs(self.db, m.id).get("announce_anniversary", 1):
                names.append(f"**{discord.utils.escape_markdown(m.display_name)}** ({years} year{'s' if years != 1 else ''})")
        if names:
            await ui.safe_send(channel, embed=discord.Embed(title="🎉 Server anniversaries today", description="Thanks for being here: " + ", ".join(names[:40]), colour=ui.PINK))

    async def post_weekly(self, guild):
        channel = self.highlights(guild)
        if channel is None:
            return
        since = time.time() - 7 * 86400
        e = discord.Embed(title="🌟 This week in " + guild.name, colour=ui.PINK)
        try:
            stars = self.db.fetchall("SELECT * FROM starboard_posts WHERE guild_id=? AND created_ts>=? ORDER BY stars DESC LIMIT 3", (guild.id, since))
            if stars:
                e.add_field(name="⭐ Most-starred", value="\n".join(f"[{r['stars']}⭐ by <@{r['author_id']}>]({ui.jump(guild.id, r['channel_id'], r['message_id'])})" for r in stars), inline=False)
        except Exception:
            pass
        recs = self.db.fetchall("SELECT * FROM recognitions WHERE guild_id=? AND created_ts>=? ORDER BY id DESC LIMIT 5", (guild.id, since))
        if recs:
            e.add_field(name="🏅 Recognised", value="\n".join(f"<@{r['user_id']}> — {ui.clip(r['reason'], 80)}" for r in recs), inline=False)
        stats = self.bot.get_cog("StatsCog")
        if stats:
            totals = stats.store.all_totals(guild.id, "7d")
            msgs = sum(t["m"] for t in totals.values())
            top = sorted(((u, t["m"]) for u, t in totals.items()), key=lambda r: -r[1])[:3]
            e.add_field(name="💬 Activity", value=f"{msgs:,} messages from {len(totals)} active members" + ("\nTop chatters: " + ", ".join(stats.who(guild, u) for u, _ in top) if top else ""), inline=False)
        if len(e.fields) == 0:
            e.description = "A quiet week. Let's make the next one lively! 💛"
        await ui.safe_send(channel, embed=e)

    # ---------------------------------------------------------------- milestones
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        s = self.s(message.guild.id)
        if not (s.get("enabled") and s.get("milestones")):
            return
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO member_msgcount(guild_id, user_id, count) VALUES (?,?,1) ON CONFLICT(guild_id, user_id) DO UPDATE SET count=count+1",
                         (message.guild.id, message.author.id))
            row = conn.execute("SELECT count, last_milestone FROM member_msgcount WHERE guild_id=? AND user_id=?", (message.guild.id, message.author.id)).fetchone()
            reached = max((m for m in MILESTONES if row[0] >= m), default=0)
            if reached <= row[1]:
                return
            conn.execute("UPDATE member_msgcount SET last_milestone=? WHERE guild_id=? AND user_id=?", (reached, message.guild.id, message.author.id))
        if not user_prefs(self.db, message.author.id).get("announce_milestones", 1):
            return
        channel = self.highlights(message.guild) or message.channel
        await ui.safe_send(channel, f"🎊 {message.author.mention} just sent their **{reached:,}th** message here! (counted since milestones were switched on)",
                           allowed_mentions=discord.AllowedMentions(users=True))

    # ---------------------------------------------------------------- commands
    @commands.command(name="question", aliases=["qotd", "randomquestion"])
    @commands.cooldown(1, 15, commands.BucketType.channel)
    async def question(self, ctx):
        """A random conversation question."""
        await ui.reply(ctx, embed=discord.Embed(description=f"❓ {secrets.choice(QUESTIONS)}", colour=ui.PINK), ephemeral=False)

    @commands.command(name="topic", aliases=["starter"])
    @commands.cooldown(1, 15, commands.BucketType.channel)
    async def topic(self, ctx):
        """A conversation starter."""
        await ui.reply(ctx, embed=discord.Embed(description=f"💬 {secrets.choice(STARTERS)}", colour=ui.PINK), ephemeral=False)

    @commands.command(name="questionadd")
    @commands.guild_only()
    @checks.admin_only()
    async def questionadd(self, ctx, *, text: str):
        """Add a custom daily question."""
        self.db.insert("INSERT INTO custom_questions(guild_id, text) VALUES (?,?)", (ctx.guild.id, text[:300]))
        await ui.reply(ctx, embed=ui.ok("Question added to the daily rotation."))

    @commands.command(name="recognize", aliases=["recognise", "kudos"])
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def recognize(self, ctx, member: discord.Member, *, reason: str):
        """Publicly recognise a member for something great."""
        if member.bot or member.id == ctx.author.id:
            return await ui.reply(ctx, embed=ui.err("Recognise someone else (and not a bot)! 💛"))
        self.db.insert("INSERT INTO recognitions(guild_id, user_id, by_id, reason, created_ts) VALUES (?,?,?,?,?)", (ctx.guild.id, member.id, ctx.author.id, reason[:300], time.time()))
        count = self.db.scalar("SELECT count(*) FROM recognitions WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id), 0)
        e = discord.Embed(title="🏅 Member recognition", colour=ui.PINK, description=f"{member.mention} was recognised by {ctx.author.mention}:\n> {discord.utils.escape_mentions(reason[:300])}")
        e.set_footer(text=f"Recognised {count} time(s) in total")
        e.set_thumbnail(url=member.display_avatar.url)
        dest = self.highlights(ctx.guild) if self.s(ctx.guild.id).get("enabled") else None
        await ui.safe_send(dest or ctx.channel, embed=e)
        if dest and dest != ctx.channel:
            await ui.reply(ctx, embed=ui.ok(f"Posted in {dest.mention}."))

    @commands.command(name="feature")
    @commands.guild_only()
    @checks.mod_only()
    async def feature(self, ctx, link: str):
        """Feature a message in the highlights channel (staff). Use the message link."""
        message = await self.bot.get_cog("Utility").resolve_link(ctx, link) if self.bot.get_cog("Utility") else None
        if message is None:
            return await ui.reply(ctx, embed=ui.err("I couldn't open that message (check the link and my access to the channel)."))
        if getattr(message.channel, "nsfw", False):
            return await ui.reply(ctx, embed=ui.err("Messages from age-restricted channels can't be featured."))
        try:
            self.db.execute("INSERT INTO featured_messages(guild_id, channel_id, message_id, author_id, by_id, created_ts) VALUES (?,?,?,?,?,?)",
                            (ctx.guild.id, message.channel.id, message.id, message.author.id, ctx.author.id, time.time()))
        except Exception:
            return await ui.reply(ctx, embed=ui.err("That message is already featured."))
        e = discord.Embed(description=ui.clip(message.content, 3500) or "*attachment*", colour=ui.PINK, timestamp=message.created_at)
        e.set_author(name=f"✨ Featured · {message.author.display_name}", icon_url=message.author.display_avatar.url)
        e.add_field(name="Original", value=f"[Jump to message]({message.jump_url}) in {message.channel.mention}")
        if message.attachments and message.attachments[0].content_type and message.attachments[0].content_type.startswith("image"):
            e.set_image(url=message.attachments[0].url)
        await ui.safe_send(self.highlights(ctx.guild) or ctx.channel, embed=e)
        await ui.reply(ctx, embed=ui.ok("Featured!"))

    @commands.command(name="highlights")
    @commands.guild_only()
    async def highlights_cmd(self, ctx):
        """Recently featured messages and recognitions."""
        feats = self.db.fetchall("SELECT * FROM featured_messages WHERE guild_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id,))
        recs = self.db.fetchall("SELECT * FROM recognitions WHERE guild_id=? ORDER BY id DESC LIMIT 10", (ctx.guild.id,))
        e = discord.Embed(title="✨ Community highlights", colour=ui.PINK)
        e.add_field(name="Featured", value="\n".join(f"[message by <@{r['author_id']}>]({ui.jump(ctx.guild.id, r['channel_id'], r['message_id'])}) · <t:{int(r['created_ts'])}:R>" for r in feats) or "—", inline=False)
        e.add_field(name="Recognised", value="\n".join(f"<@{r['user_id']}> — {ui.clip(r['reason'], 60)}" for r in recs) or "—", inline=False)
        await ui.reply(ctx, embed=e)

    @commands.group(name="challenge", invoke_without_command=True)
    @commands.guild_only()
    async def challenge(self, ctx):
        """Community challenge: view, join. Staff: set, end."""
        row = self.db.fetchone("SELECT * FROM challenges WHERE guild_id=? AND status='active' ORDER BY id DESC", (ctx.guild.id,))
        if not row:
            return await ui.reply(ctx, embed=ui.embed(description="No active challenge. Staff can start one with `!challenge set <text> [| 7d]`."))
        entries = self.db.fetchall("SELECT user_id FROM challenge_entries WHERE challenge_id=?", (row["id"],))
        e = discord.Embed(title="🏆 Community challenge", description=row["text"], colour=ui.PINK)
        if row["ends_ts"]:
            e.add_field(name="Ends", value=f"<t:{int(row['ends_ts'])}:R>")
        e.add_field(name=f"Participants ({len(entries)})", value=ui.clip(" ".join(f"<@{r['user_id']}>" for r in entries) or "be the first! `!challenge join`", 1000), inline=False)
        await ui.reply(ctx, embed=e, ephemeral=False)

    @challenge.command(name="set")
    @checks.mod_only()
    async def challenge_set(self, ctx, *, spec: str):
        from common.timeparse import parse_duration
        text, _, dur = spec.partition("|")
        delta = parse_duration(dur.strip(), minimum=3600) if dur.strip() else None
        self.db.execute("UPDATE challenges SET status='ended' WHERE guild_id=? AND status='active'", (ctx.guild.id,))
        self.db.insert("INSERT INTO challenges(guild_id, text, created_ts, ends_ts) VALUES (?,?,?,?)", (ctx.guild.id, text.strip()[:1000], time.time(), time.time() + delta.total_seconds() if delta else None))
        await ui.safe_send(self.highlights(ctx.guild) or ctx.channel, embed=discord.Embed(title="🏆 New community challenge!", description=text.strip()[:1000] + "\n\nJoin with `!challenge join [note]`.", colour=ui.PINK))
        await ui.reply(ctx, embed=ui.ok("Challenge started."))

    @challenge.command(name="join", aliases=["done", "submit"])
    async def challenge_join(self, ctx, *, note: str = ""):
        row = self.db.fetchone("SELECT * FROM challenges WHERE guild_id=? AND status='active' ORDER BY id DESC", (ctx.guild.id,))
        if not row or (row["ends_ts"] and time.time() > row["ends_ts"]):
            return await ui.reply(ctx, embed=ui.err("There's no active challenge right now."))
        self.db.execute("INSERT INTO challenge_entries(challenge_id, user_id, note, ts) VALUES (?,?,?,?) ON CONFLICT(challenge_id, user_id) DO UPDATE SET note=excluded.note",
                        (row["id"], ctx.author.id, note[:300], time.time()))
        await ui.reply(ctx, embed=ui.ok("You're in! 💪"))

    @challenge.command(name="end")
    @checks.mod_only()
    async def challenge_end(self, ctx):
        row = self.db.fetchone("SELECT * FROM challenges WHERE guild_id=? AND status='active' ORDER BY id DESC", (ctx.guild.id,))
        if not row:
            return await ui.reply(ctx, embed=ui.err("No active challenge."))
        self.db.execute("UPDATE challenges SET status='ended' WHERE id=?", (row["id"],))
        entries = self.db.fetchall("SELECT user_id FROM challenge_entries WHERE challenge_id=?", (row["id"],))
        await ui.safe_send(self.highlights(ctx.guild) or ctx.channel, embed=discord.Embed(title="🏁 Challenge complete!", colour=ui.GOOD,
                           description=f"{row['text']}\n\nThanks to all **{len(entries)}** participants: " + ui.clip(" ".join(f"<@{r['user_id']}>" for r in entries), 3000)))
        await ui.reply(ctx, embed=ui.ok("Challenge ended."))


async def setup(bot):
    await bot.add_cog(Engagement(bot))
