"""Button polls: yes/no or up to 10 options, single or multiple choice, optional anonymity, expiry,
vote changes, duplicate-vote prevention (database primary key), result announcements and history.
ATLAS's original quick reaction poll (`!poll <question>`) is unchanged.
"""
import json
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option
from common.timeparse import fmt_delta, parse_duration

NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def bar(n, total, width=12):
    filled = round(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)


class PollButton(discord.ui.DynamicItem[discord.ui.Button], template=r"poll:(?P<id>\d+):(?P<opt>\d+|voters)"):
    def __init__(self, poll_id, opt, label=None, emoji=None, disabled=False):
        if opt == "voters":
            button = discord.ui.Button(label="Who voted?", emoji="👀", style=discord.ButtonStyle.secondary, custom_id=f"poll:{poll_id}:voters", disabled=disabled)
        else:
            button = discord.ui.Button(label=ui.clip(label or str(opt), 80), emoji=emoji, style=discord.ButtonStyle.primary, custom_id=f"poll:{poll_id}:{opt}", disabled=disabled)
        super().__init__(button)
        self.poll_id, self.opt = int(poll_id), opt

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        opt = match["opt"]
        return cls(match["id"], opt if opt == "voters" else int(opt))

    async def callback(self, interaction):
        await interaction.client.get_cog("Polls").vote(interaction, self.poll_id, self.opt)


class Polls(commands.Cog, name="Polls"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("polls_001", """
            CREATE TABLE IF NOT EXISTS polls (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER, message_id INTEGER,
                author_id INTEGER NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL, anonymous INTEGER DEFAULT 0, multi INTEGER DEFAULT 0,
                ends_ts REAL, status TEXT NOT NULL DEFAULT 'open', created_ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_polls_status ON polls(status, ends_ts);
            CREATE TABLE IF NOT EXISTS poll_votes (poll_id INTEGER NOT NULL, user_id INTEGER NOT NULL, option INTEGER NOT NULL, ts REAL NOT NULL,
                PRIMARY KEY (poll_id, user_id, option))""")
        bot.registry.register(Module("polls", "Polls", "📊", "Button polls with expiry and results.", [
            Option("default_hours", "Default duration (hours)", "int", 24, "0 = no expiry", 0, 720),
            Option("results_channel", "Results announcement channel", "channel", None, "Defaults to the poll's channel"),
            Option("who_can_create", "Who can create polls", "choice", "everyone", choices=("everyone", "staff")),
        ], default_enabled=True))
        bot.add_dynamic_items(PollButton)

    async def cog_load(self):
        self.expiry_loop.start()

    async def cog_unload(self):
        self.expiry_loop.cancel()

    def counts(self, poll):
        rows = self.db.fetchall("SELECT option, count(*) c FROM poll_votes WHERE poll_id=? GROUP BY option", (poll["id"],))
        counts = {r["option"]: r["c"] for r in rows}
        voters = self.db.scalar("SELECT count(DISTINCT user_id) FROM poll_votes WHERE poll_id=?", (poll["id"],), 0)
        return counts, voters

    def embed(self, poll):
        options = json.loads(poll["options"])
        counts, voters = self.counts(poll)
        total = sum(counts.values())
        closed = poll["status"] != "open"
        e = discord.Embed(title=("📊 " if not closed else "🏁 ") + ui.clip(poll["question"], 250), colour=ui.INFO if not closed else ui.GOOD)
        lines = []
        top = max(counts.values(), default=0)
        for i, opt in enumerate(options):
            n = counts.get(i, 0)
            crown = " 👑" if closed and n == top and n > 0 else ""
            lines.append(f"{NUMBERS[i]} **{ui.clip(opt, 80)}**{crown}\n`{bar(n, total)}` {n} vote{'s' if n != 1 else ''} ({round(100 * n / total) if total else 0}%)")
        e.description = "\n".join(lines)
        bits = [f"{voters} voter{'s' if voters != 1 else ''}", "anonymous" if poll["anonymous"] else "public votes", "multiple choice" if poll["multi"] else "one choice"]
        if poll["ends_ts"]:
            bits.append(("ended " if closed else "ends ") + f"<t:{int(poll['ends_ts'])}:R>")
        e.add_field(name="​", value=" · ".join(bits))
        e.set_footer(text=f"Poll #{poll['id']} · " + ("closed" if closed else "click to vote · click again to remove · pick another to change"))
        return e

    def view(self, poll):
        options = json.loads(poll["options"])
        closed = poll["status"] != "open"
        v = discord.ui.View(timeout=None)
        for i, opt in enumerate(options):
            v.add_item(PollButton(poll["id"], i, label=opt, emoji=NUMBERS[i], disabled=closed))
        if not poll["anonymous"]:
            v.add_item(PollButton(poll["id"], "voters"))
        return v

    def get(self, poll_id):
        row = self.db.fetchone("SELECT * FROM polls WHERE id=?", (poll_id,))
        return dict(row) if row else None

    async def vote(self, interaction, poll_id, opt):
        poll = self.get(poll_id)
        if poll is None or poll["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message("That poll no longer exists.", ephemeral=True)
        options = json.loads(poll["options"])
        if opt == "voters":
            if poll["anonymous"]:
                return await interaction.response.send_message("This poll is anonymous.", ephemeral=True)
            rows = self.db.fetchall("SELECT user_id, option FROM poll_votes WHERE poll_id=?", (poll_id,))
            by_opt = {}
            for r in rows:
                by_opt.setdefault(r["option"], []).append(f"<@{r['user_id']}>")
            text = "\n".join(f"{NUMBERS[i]} {ui.clip(', '.join(by_opt.get(i, [])) or '—', 900)}" for i in range(len(options)))
            return await interaction.response.send_message(embed=ui.embed("👀 Voters", text), ephemeral=True)
        if poll["status"] != "open" or (poll["ends_ts"] and time.time() >= poll["ends_ts"]):
            return await interaction.response.send_message("This poll has closed.", ephemeral=True)
        if not 0 <= opt < len(options):
            return await interaction.response.send_message("Unknown option.", ephemeral=True)
        uid = interaction.user.id
        with self.db.transaction() as conn:
            had = conn.execute("SELECT 1 FROM poll_votes WHERE poll_id=? AND user_id=? AND option=?", (poll_id, uid, opt)).fetchone()
            if had:
                conn.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=? AND option=?", (poll_id, uid, opt))
                note = f"Removed your vote for **{options[opt]}**."
            else:
                if not poll["multi"]:
                    conn.execute("DELETE FROM poll_votes WHERE poll_id=? AND user_id=?", (poll_id, uid))
                conn.execute("INSERT OR IGNORE INTO poll_votes(poll_id, user_id, option, ts) VALUES (?,?,?,?)", (poll_id, uid, opt, time.time()))
                note = f"Voted for **{options[opt]}**."
        await interaction.response.edit_message(embed=self.embed(poll), view=self.view(poll))
        await interaction.followup.send(note, ephemeral=True)

    async def create(self, target, guild, channel, author, question, options, *, anonymous=False, multi=False, duration=None):
        s = self.bot.registry.get(guild.id, "polls")
        if not s.get("enabled"):
            return await ui.reply(target, embed=ui.err("Polls are switched off here."))
        if s.get("who_can_create") == "staff" and not checks.is_mod(author):
            return await ui.reply(target, embed=ui.err("Only staff can create polls here."))
        options = [o.strip()[:80] for o in options if o.strip()]
        if len(options) < 2 or len(options) > 10:
            return await ui.reply(target, embed=ui.err("A poll needs 2–10 options."))
        if len(set(o.casefold() for o in options)) != len(options):
            return await ui.reply(target, embed=ui.err("Options must be different from each other."))
        if duration is None:
            hours = int(s.get("default_hours") or 0)
            ends = time.time() + hours * 3600 if hours else None
        else:
            ends = time.time() + duration.total_seconds() if duration.total_seconds() > 0 else None
        perms = channel.permissions_for(guild.me)
        if not (perms.send_messages and perms.embed_links):
            return await ui.reply(target, embed=ui.err(f"I can't post embeds in {channel.mention}."))
        pid = self.db.insert("INSERT INTO polls(guild_id, channel_id, author_id, question, options, anonymous, multi, ends_ts, created_ts) VALUES (?,?,?,?,?,?,?,?,?)",
                             (guild.id, channel.id, author.id, question[:250], json.dumps(options), int(anonymous), int(multi), ends, time.time()))
        poll = self.get(pid)
        msg = await channel.send(embed=self.embed(poll), view=self.view(poll))
        self.db.execute("UPDATE polls SET message_id=? WHERE id=?", (msg.id, pid))
        await ui.reply(target, embed=ui.ok(f"Poll #{pid} created" + (f" (ends in {fmt_delta(ends - time.time())})." if ends else ".")))

    async def close(self, poll, announce=True):
        if not self.db.claim("polls", poll["id"], expected="open", new="closed"):
            return False
        poll["status"] = "closed"
        guild = self.bot.get_guild(poll["guild_id"])
        channel = guild.get_channel(poll["channel_id"]) if guild else None
        if channel is None:
            return True
        try:
            msg = await channel.fetch_message(poll["message_id"])
            await msg.edit(embed=self.embed(poll), view=self.view(poll))
        except discord.HTTPException:
            msg = None
        if announce:
            options = json.loads(poll["options"])
            counts, voters = self.counts(poll)
            top = max(counts.values(), default=0)
            winners = [options[i] for i, n in counts.items() if n == top and n > 0]
            text = f"🏁 **Poll closed:** {ui.clip(poll['question'], 200)}\n" + (f"Winner: **{' / '.join(winners)}** with {top} vote(s) from {voters} voter(s)." if winners else "No votes were cast.")
            s = self.bot.registry.get(guild.id, "polls")
            dest = guild.get_channel(int(s["results_channel"])) if s.get("results_channel") else channel
            if msg is not None and dest == channel:
                try:
                    await msg.reply(text, mention_author=False, allowed_mentions=discord.AllowedMentions.none())
                    return True
                except discord.HTTPException:
                    pass
            await ui.safe_send(dest, text + (f" [poll]({msg.jump_url})" if msg else ""))
        return True

    @tasks.loop(seconds=30)
    async def expiry_loop(self):
        for row in self.db.fetchall("SELECT * FROM polls WHERE status='open' AND ends_ts IS NOT NULL AND ends_ts<=?", (time.time(),)):
            await self.close(dict(row))

    @expiry_loop.before_loop
    async def before_expiry(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------------- commands
    @commands.group(name="polls", invoke_without_command=True)
    @commands.guild_only()
    async def polls(self, ctx):
        """Button polls. `!polls new Question | A | B [| C...] [--anon] [--multi] [--for 2h]`"""
        await ui.reply(ctx, embed=ui.embed("📊 Polls", "`!polls new Question | Option A | Option B [--anon] [--multi] [--for 2h]`\n`!polls yesno Question [--for 1h]`\n`!polls list [open|closed]` · `!polls results <id>` · `!polls end <id>`\nSlash: `/pollnew`"))

    @staticmethod
    def parse_flags(text):
        anonymous = bool(re.search(r"--anon(ymous)?\b", text))
        multi = bool(re.search(r"--multi(ple)?\b", text))
        m = re.search(r"--for\s+(\S+)", text)
        duration = parse_duration(m.group(1), minimum=60, maximum=30 * 86400) if m else None
        text = re.sub(r"--anon(ymous)?\b|--multi(ple)?\b|--for\s+\S+", "", text).strip()
        return text, anonymous, multi, duration, bool(m)

    @polls.command(name="new", aliases=["create"])
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def new(self, ctx, *, spec: str):
        text, anonymous, multi, duration, had_for = self.parse_flags(spec)
        if had_for and duration is None:
            return await ui.reply(ctx, embed=ui.err("Duration must be like `30m`, `2h`, `3d` (1 minute to 30 days)."))
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 3:
            return await ui.reply(ctx, embed=ui.err("Use `!polls new Question | Option A | Option B`."))
        await self.create(ctx, ctx.guild, ctx.channel, ctx.author, parts[0], parts[1:], anonymous=anonymous, multi=multi, duration=duration)

    @polls.command(name="yesno")
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def yesno(self, ctx, *, spec: str):
        text, anonymous, _, duration, had_for = self.parse_flags(spec)
        if had_for and duration is None:
            return await ui.reply(ctx, embed=ui.err("Duration must be like `30m`, `2h`, `3d`."))
        await self.create(ctx, ctx.guild, ctx.channel, ctx.author, text, ["Yes", "No"], anonymous=anonymous, duration=duration)

    @app_commands.command(name="pollnew", description="Create a button poll")
    @app_commands.guild_only()
    @app_commands.describe(question="The question", options="Options separated by | (leave empty for Yes/No)", duration="e.g. 30m, 2h, 3d",
                           anonymous="Hide who voted", multiple="Allow choosing several options")
    async def pollnew(self, interaction: discord.Interaction, question: str, options: str = "", duration: str = "", anonymous: bool = False, multiple: bool = False):
        dur = None
        if duration:
            dur = parse_duration(duration, minimum=60, maximum=30 * 86400)
            if dur is None:
                return await interaction.response.send_message("Duration must be like `30m`, `2h`, `3d`.", ephemeral=True)
        opts = [o for o in options.split("|")] if options.strip() else ["Yes", "No"]
        await self.create(interaction, interaction.guild, interaction.channel, interaction.user, question, opts, anonymous=anonymous, multi=multiple, duration=dur)

    @polls.command(name="end", aliases=["close"])
    async def end(self, ctx, poll_id: int):
        poll = self.get(poll_id)
        if poll is None or poll["guild_id"] != ctx.guild.id:
            return await ui.reply(ctx, embed=ui.err("No such poll here."))
        if poll["author_id"] != ctx.author.id and not checks.is_mod(ctx.author):
            return await ui.reply(ctx, embed=ui.err("Only the poll's creator or a moderator can end it."))
        if not await self.close(poll):
            return await ui.reply(ctx, embed=ui.err("That poll is already closed."))
        await ui.reply(ctx, embed=ui.ok(f"Poll #{poll_id} closed."))

    @polls.command(name="results")
    async def results(self, ctx, poll_id: int):
        poll = self.get(poll_id)
        if poll is None or poll["guild_id"] != ctx.guild.id:
            return await ui.reply(ctx, embed=ui.err("No such poll here."))
        await ui.reply(ctx, embed=self.embed(poll))

    @polls.command(name="list", aliases=["history"])
    async def list_(self, ctx, status: str = "open"):
        status = "closed" if status.startswith("c") else "open"
        rows = self.db.fetchall("SELECT * FROM polls WHERE guild_id=? AND status=? ORDER BY id DESC LIMIT 100", (ctx.guild.id, status))
        lines = []
        for r in rows:
            _, voters = self.counts(r)
            link = f" [↗]({ui.jump(ctx.guild.id, r['channel_id'], r['message_id'])})" if r["message_id"] else ""
            lines.append(f"`#{r['id']}` {ui.clip(r['question'], 70)} · {voters} voters{link}")
        await ui.paginate(ctx, ui.pages_from_lines(f"📊 Polls ({status})", lines))


async def setup(bot):
    await bot.add_cog(Polls(bot))
