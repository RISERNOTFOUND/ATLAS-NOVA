"""Lightweight Discord games with buttons, turn tracking, timers, scores, leaderboards and match history.
No betting, wagers, coins or paid entry of any kind.
"""
import asyncio
import json
import random
import secrets
import time
from datetime import datetime

import discord
from discord.ext import commands

from common import checks, ui
from common.config import Module, Option

from .game_data import EMOJI_PUZZLES, MEMORY_EMOJIS, RIDDLES, TRIVIA, WORDS

LETTERS = "ABCD"
POINTS = {"easy": 1, "medium": 2, "hard": 3}


class GameStore:
    def __init__(self, db):
        self.db = db
        db.migrate("games_001", """
            CREATE TABLE IF NOT EXISTS game_scores (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, game TEXT NOT NULL, wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, points INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id, game));
            CREATE TABLE IF NOT EXISTS match_history (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, game TEXT NOT NULL,
                players TEXT NOT NULL, winner_id INTEGER, result TEXT, ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_match_guild ON match_history(guild_id, ts);
            CREATE TABLE IF NOT EXISTS trivia_custom (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, category TEXT NOT NULL,
                difficulty TEXT NOT NULL, question TEXT NOT NULL, correct TEXT NOT NULL, wrong TEXT NOT NULL, explanation TEXT, author_id INTEGER);
            CREATE TABLE IF NOT EXISTS daily_quiz (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, day TEXT NOT NULL, score INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, day))""")

    def record(self, guild_id, game, players, winner_id=None, result="win", points=None):
        """players: list of user ids. points: optional {user_id: points}."""
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO match_history(guild_id, game, players, winner_id, result, ts) VALUES (?,?,?,?,?,?)",
                         (guild_id, game, json.dumps(players), winner_id, result, time.time()))
            for uid in players:
                col = "draws" if result == "draw" else ("wins" if uid == winner_id else "losses")
                if result == "solo":
                    col = "wins" if winner_id == uid else "losses"
                pts = (points or {}).get(uid, 3 if (col == "wins") else (1 if col == "draws" else 0))
                conn.execute(f"INSERT INTO game_scores(guild_id, user_id, game, {col}, points) VALUES (?,?,?,1,?) "
                             f"ON CONFLICT(guild_id, user_id, game) DO UPDATE SET {col}={col}+1, points=points+excluded.points", (guild_id, uid, game, pts))

    def add_points(self, guild_id, user_id, game, pts, win=True):
        self.db.execute("INSERT INTO game_scores(guild_id, user_id, game, wins, points) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(guild_id, user_id, game) DO UPDATE SET wins=wins+excluded.wins, points=points+excluded.points",
                        (guild_id, user_id, game, int(win), pts))


# ===================================================================== tic-tac-toe
class TTTButton(discord.ui.Button):
    def __init__(self, idx):
        super().__init__(label="​", style=discord.ButtonStyle.secondary, row=idx // 3)
        self.idx = idx

    async def callback(self, interaction):
        await self.view.play(interaction, self)


class TicTacToe(discord.ui.View):
    LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]

    def __init__(self, cog, guild_id, p1, p2):
        super().__init__(timeout=120)
        self.cog, self.guild_id, self.players, self.turn = cog, guild_id, [p1, p2], 0
        self.board = [None] * 9
        self.message = None
        for i in range(9):
            self.add_item(TTTButton(i))

    def status(self, extra=""):
        p = self.players[self.turn]
        return f"❌ {self.players[0].mention} vs ⭕ {self.players[1].mention}\n" + (extra or f"Turn: {'❌' if self.turn == 0 else '⭕'} {p.mention} (2 minutes per move)")

    def winner(self):
        for a, b, c in self.LINES:
            if self.board[a] is not None and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        return "draw" if all(v is not None for v in self.board) else None

    async def play(self, interaction, button):
        if interaction.user.id not in (p.id for p in self.players):
            return await interaction.response.send_message("You're not in this game.", ephemeral=True)
        if interaction.user.id != self.players[self.turn].id:
            return await interaction.response.send_message("Not your turn!", ephemeral=True)
        if self.board[button.idx] is not None:
            return await interaction.response.send_message("That square is taken.", ephemeral=True)
        self.board[button.idx] = self.turn
        button.label, button.disabled = ("❌", True) if self.turn == 0 else ("⭕", True)
        button.style = discord.ButtonStyle.danger if self.turn == 0 else discord.ButtonStyle.primary
        result = self.winner()
        if result is None:
            self.turn ^= 1
            return await interaction.response.edit_message(content=self.status(), view=self)
        for child in self.children:
            child.disabled = True
        self.stop()
        self.cog.release(self.players)
        if result == "draw":
            self.cog.store.record(self.guild_id, "tictactoe", [p.id for p in self.players], None, "draw")
            text = "🤝 It's a draw!"
        else:
            win = self.players[result]
            self.cog.store.record(self.guild_id, "tictactoe", [p.id for p in self.players], win.id)
            text = f"🏆 {win.mention} wins!"
        await interaction.response.edit_message(content=self.status(text), view=self)

    async def on_timeout(self):
        self.cog.release(self.players)
        for child in self.children:
            child.disabled = True
        if self.message:
            loser = self.players[self.turn]
            winner = self.players[self.turn ^ 1]
            self.cog.store.record(self.guild_id, "tictactoe", [p.id for p in self.players], winner.id, "timeout")
            try:
                await self.message.edit(content=self.status(f"⏱️ {loser.mention} ran out of time. {winner.mention} wins!"), view=self)
            except discord.HTTPException:
                pass


# ===================================================================== connect four
class C4Button(discord.ui.Button):
    def __init__(self, col):
        super().__init__(label=str(col + 1), style=discord.ButtonStyle.secondary, row=0 if col < 4 else 1)
        self.col = col

    async def callback(self, interaction):
        await self.view.drop(interaction, self.col)


class ConnectFour(discord.ui.View):
    ROWS, COLS = 6, 7
    DISCS = ("🔴", "🟡")

    def __init__(self, cog, guild_id, p1, p2):
        super().__init__(timeout=180)
        self.cog, self.guild_id, self.players, self.turn = cog, guild_id, [p1, p2], 0
        self.grid = [[None] * self.COLS for _ in range(self.ROWS)]
        self.message = None
        for c in range(self.COLS):
            self.add_item(C4Button(c))

    def render(self, extra=""):
        rows = ["".join(self.DISCS[v] if v is not None else "⚫" for v in row) for row in self.grid]
        head = f"{self.DISCS[0]} {self.players[0].mention} vs {self.DISCS[1]} {self.players[1].mention}"
        return head + "\n" + "\n".join(rows) + "\n1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣\n" + (extra or f"Turn: {self.DISCS[self.turn]} {self.players[self.turn].mention}")

    def wins(self, r, c, who):
        for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            count = 1
            for sign in (1, -1):
                rr, cc = r + dr * sign, c + dc * sign
                while 0 <= rr < self.ROWS and 0 <= cc < self.COLS and self.grid[rr][cc] == who:
                    count += 1
                    rr += dr * sign
                    cc += dc * sign
            if count >= 4:
                return True
        return False

    async def drop(self, interaction, col):
        if interaction.user.id not in (p.id for p in self.players):
            return await interaction.response.send_message("You're not in this game.", ephemeral=True)
        if interaction.user.id != self.players[self.turn].id:
            return await interaction.response.send_message("Not your turn!", ephemeral=True)
        row = next((r for r in range(self.ROWS - 1, -1, -1) if self.grid[r][col] is None), None)
        if row is None:
            return await interaction.response.send_message("That column is full.", ephemeral=True)
        self.grid[row][col] = self.turn
        if self.grid[0][col] is not None:
            for child in self.children:
                if isinstance(child, C4Button) and child.col == col:
                    child.disabled = True
        if self.wins(row, col, self.turn):
            win = self.players[self.turn]
            self.cog.store.record(self.guild_id, "connect4", [p.id for p in self.players], win.id)
            return await self.finish(interaction, f"🏆 {win.mention} connects four!")
        if all(self.grid[0][c] is not None for c in range(self.COLS)):
            self.cog.store.record(self.guild_id, "connect4", [p.id for p in self.players], None, "draw")
            return await self.finish(interaction, "🤝 Board full: it's a draw!")
        self.turn ^= 1
        await interaction.response.edit_message(content=self.render(), view=self)

    async def finish(self, interaction, text):
        for child in self.children:
            child.disabled = True
        self.stop()
        self.cog.release(self.players)
        await interaction.response.edit_message(content=self.render(text), view=self)

    async def on_timeout(self):
        self.cog.release(self.players)
        for child in self.children:
            child.disabled = True
        if self.message:
            winner = self.players[self.turn ^ 1]
            self.cog.store.record(self.guild_id, "connect4", [p.id for p in self.players], winner.id, "timeout")
            try:
                await self.message.edit(content=self.render(f"⏱️ {self.players[self.turn].mention} ran out of time. {winner.mention} wins!"), view=self)
            except discord.HTTPException:
                pass


# ===================================================================== challenge accept
class Accept(discord.ui.View):
    def __init__(self, opponent_id):
        super().__init__(timeout=60)
        self.opponent_id, self.value = opponent_id, None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message("This challenge isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def yes(self, interaction, button):
        self.value = True
        await interaction.response.edit_message(view=None)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def no(self, interaction, button):
        self.value = False
        await interaction.response.edit_message(content="Challenge declined.", view=None)
        self.stop()


# ===================================================================== rps duel
class RPSDuel(discord.ui.View):
    CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}

    def __init__(self, cog, guild_id, p1, p2):
        super().__init__(timeout=60)
        self.cog, self.guild_id, self.players, self.picks, self.message = cog, guild_id, [p1, p2], {}, None
        for name, emoji in self.CHOICES.items():
            b = discord.ui.Button(label=name.title(), emoji=emoji, style=discord.ButtonStyle.primary)
            b.callback = self.make(name)
            self.add_item(b)

    def make(self, name):
        async def cb(interaction):
            if interaction.user.id not in (p.id for p in self.players):
                return await interaction.response.send_message("You're not in this duel.", ephemeral=True)
            if interaction.user.id in self.picks:
                return await interaction.response.send_message("You already picked!", ephemeral=True)
            self.picks[interaction.user.id] = name
            await interaction.response.send_message(f"You picked {self.CHOICES[name]} (secret until both choose).", ephemeral=True)
            if len(self.picks) == 2:
                await self.resolve()
        return cb

    async def resolve(self):
        a, b = self.players
        pa, pb = self.picks[a.id], self.picks[b.id]
        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        if pa == pb:
            text, winner, result = "🤝 Draw!", None, "draw"
        elif beats[pa] == pb:
            text, winner, result = f"🏆 {a.mention} wins!", a.id, "win"
        else:
            text, winner, result = f"🏆 {b.mention} wins!", b.id, "win"
        self.cog.store.record(self.guild_id, "rps", [a.id, b.id], winner, result)
        self.cog.release(self.players)
        for child in self.children:
            child.disabled = True
        self.stop()
        if self.message:
            await self.message.edit(content=f"{a.mention} {self.CHOICES[pa]} vs {self.CHOICES[pb]} {b.mention}\n{text}", view=self)

    async def on_timeout(self):
        self.cog.release(self.players)
        if self.message and len(self.picks) < 2:
            try:
                await self.message.edit(content="⏱️ The duel timed out.", view=None)
            except discord.HTTPException:
                pass


# ===================================================================== trivia
def trivia_pool(db, guild_id, category=None, difficulty=None):
    pool = list(TRIVIA)
    for r in db.fetchall("SELECT * FROM trivia_custom WHERE guild_id=?", (guild_id,)):
        pool.append((r["category"], r["difficulty"], r["question"], r["correct"], json.loads(r["wrong"]), r["explanation"] or ""))
    if category:
        pool = [q for q in pool if q[0] == category.casefold()]
    if difficulty:
        pool = [q for q in pool if q[1] == difficulty.casefold()]
    return pool


class TriviaQuestion(discord.ui.View):
    """One multiple-choice question. solo=user id for personal play; None means everyone in the channel may answer once."""

    def __init__(self, q, solo=None, seconds=20, teams=None):
        super().__init__(timeout=seconds)
        self.q, self.solo, self.teams = q, solo, teams
        options = [q[3]] + list(q[4])
        random.shuffle(options)
        self.options = options
        self.correct_index = options.index(q[3])
        self.answers = {}        # user_id -> index
        self.message = None
        for i, opt in enumerate(options):
            b = discord.ui.Button(label=f"{LETTERS[i]}. {ui.clip(opt, 70)}", style=discord.ButtonStyle.primary, row=i // 2)
            b.callback = self.make(i)
            self.add_item(b)

    def embed(self, reveal=False):
        cat, diff, question = self.q[0], self.q[1], self.q[2]
        e = discord.Embed(title=f"🧠 Trivia · {cat} · {diff}", description=f"**{question}**", colour=ui.INFO if not reveal else ui.GOOD)
        e.add_field(name="Options", value="\n".join(f"**{LETTERS[i]}.** {o}" for i, o in enumerate(self.options)))
        if reveal:
            e.add_field(name="Answer", value=f"**{LETTERS[self.correct_index]}. {self.q[3]}**\n{self.q[5]}", inline=False)
        else:
            e.set_footer(text=f"{POINTS.get(diff, 1)} point(s) · {int(self.timeout)} seconds")
        return e

    def make(self, i):
        async def cb(interaction):
            uid = interaction.user.id
            if self.solo is not None and uid != self.solo:
                return await interaction.response.send_message("This question is for someone else. Start your own with `!trivia`.", ephemeral=True)
            if self.teams is not None and not any(uid in members for members in self.teams.values()):
                return await interaction.response.send_message("Join a team first next round!", ephemeral=True)
            if uid in self.answers:
                return await interaction.response.send_message("You already answered.", ephemeral=True)
            self.answers[uid] = i
            if self.solo is not None:
                self.stop()
                return await interaction.response.defer()
            await interaction.response.send_message(f"Locked in **{LETTERS[i]}**.", ephemeral=True)
        return cb

    def correct_users(self):
        return [u for u, i in self.answers.items() if i == self.correct_index]

    async def reveal(self):
        for i, child in enumerate(self.children):
            child.disabled = True
            if i == self.correct_index:
                child.style = discord.ButtonStyle.success
            elif i in self.answers.values():
                child.style = discord.ButtonStyle.danger
            else:
                child.style = discord.ButtonStyle.secondary
        if self.message:
            try:
                await self.message.edit(embed=self.embed(reveal=True), view=self)
            except discord.HTTPException:
                pass


class TeamLobby(discord.ui.View):
    def __init__(self, names, seconds=30):
        super().__init__(timeout=seconds)
        self.teams = {n: set() for n in names}
        for n in names:
            b = discord.ui.Button(label=f"Join {n}", style=discord.ButtonStyle.primary)
            b.callback = self.make(n)
            self.add_item(b)

    def make(self, name):
        async def cb(interaction):
            for members in self.teams.values():
                members.discard(interaction.user.id)
            self.teams[name].add(interaction.user.id)
            await interaction.response.send_message(f"You joined **{name}**.", ephemeral=True)
        return cb


# ===================================================================== memory & reaction
class MemoryView(discord.ui.View):
    def __init__(self, player_id, sequence):
        super().__init__(timeout=45)
        self.player_id, self.sequence, self.entered, self.done = player_id, sequence, [], asyncio.Event()
        self.result = None
        for e in MEMORY_EMOJIS:
            b = discord.ui.Button(emoji=e, style=discord.ButtonStyle.secondary)
            b.callback = self.make(e)
            self.add_item(b)

    def make(self, emoji):
        async def cb(interaction):
            if interaction.user.id != self.player_id:
                return await interaction.response.send_message("Start your own game with `!memory`.", ephemeral=True)
            self.entered.append(emoji)
            n = len(self.entered)
            if self.entered[-1] != self.sequence[n - 1]:
                self.result = False
            elif n == len(self.sequence):
                self.result = True
            if self.result is not None:
                for child in self.children:
                    child.disabled = True
                self.stop()
                self.done.set()
            await interaction.response.edit_message(content="Your input: " + "".join(self.entered), view=self)
        return cb


class ReactionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=15)
        self.live = False
        self.started = 0.0
        self.winner = None
        self.false_starts = set()
        self.button = discord.ui.Button(label="Wait for it…", style=discord.ButtonStyle.secondary)
        self.button.callback = self.press
        self.add_item(self.button)

    async def press(self, interaction):
        if not self.live:
            self.false_starts.add(interaction.user.id)
            return await interaction.response.send_message("Too early! You're out this round. 😅", ephemeral=True)
        if interaction.user.id in self.false_starts:
            return await interaction.response.send_message("You jumped the gun this round.", ephemeral=True)
        if self.winner is None:
            self.winner = (interaction.user, time.monotonic() - self.started)
            self.button.disabled = True
            self.button.label = "Done!"
            self.stop()
            await interaction.response.edit_message(content=f"⚡ {interaction.user.mention} wins in **{self.winner[1] * 1000:.0f} ms**!", view=self)
        else:
            await interaction.response.send_message("Someone was faster!", ephemeral=True)


# ===================================================================== cog
class Games(commands.Cog, name="Games"):
    def __init__(self, bot):
        self.bot = bot
        self.store = GameStore(bot.db)
        self.busy_users = set()
        self.busy_channels = set()
        self.word_chains = {}  # channel_id -> {"last": word, "used": set, "last_user": id, "count": n}
        bot.registry.register(Module("games", "Games", "🎮", "Tic-tac-toe, Connect Four, trivia, word games and more. No betting.", [
            Option("trivia_seconds", "Trivia answer time (s)", "int", 20, "", 10, 60),
            Option("game_channels", "Allowed game channels", "channels", [], "Empty = games work everywhere"),
        ], default_enabled=True))

    # ---------------------------------------------------------------- guards
    def release(self, players):
        for p in players:
            self.busy_users.discard(getattr(p, "id", p))

    async def cog_check(self, ctx):
        if ctx.guild is None:
            return True
        s = self.bot.registry.get(ctx.guild.id, "games")
        if not s.get("enabled"):
            raise commands.CheckFailure("Games are switched off in this server.")
        allowed = {int(c) for c in s.get("game_channels") or []}
        if allowed and ctx.channel.id not in allowed and getattr(ctx.channel, "parent_id", None) not in allowed:
            raise commands.CheckFailure("Games aren't enabled in this channel. Try " + ", ".join(f"<#{c}>" for c in list(allowed)[:5]))
        return True

    async def start_duel(self, ctx, opponent, name):
        if opponent.bot or opponent.id == ctx.author.id:
            await ui.reply(ctx, embed=ui.err("Challenge another (human) member!"))
            return False
        if ctx.author.id in self.busy_users or opponent.id in self.busy_users:
            await ui.reply(ctx, embed=ui.err("One of you is already in a game. Finish it first."))
            return False
        self.busy_users.update({ctx.author.id, opponent.id})
        view = Accept(opponent.id)
        msg = await ctx.send(f"{opponent.mention}, {ctx.author.mention} challenges you to **{name}**! (60s to accept)", view=view,
                             allowed_mentions=discord.AllowedMentions(users=True))
        await view.wait()
        if not view.value:
            self.release([ctx.author, opponent])
            if view.value is None:
                await msg.edit(content="⏱️ Challenge expired.", view=None)
            return False
        return msg

    # ---------------------------------------------------------------- duels
    @commands.command(name="ttt", aliases=["tictactoe"])
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ttt(self, ctx, opponent: discord.Member):
        """Tic-tac-toe against another member."""
        msg = await self.start_duel(ctx, opponent, "Tic-tac-toe")
        if not msg:
            return
        players = [ctx.author, opponent]
        random.shuffle(players)
        view = TicTacToe(self, ctx.guild.id, *players)
        await msg.edit(content=view.status(), view=view, allowed_mentions=discord.AllowedMentions.none())
        view.message = msg

    @commands.command(name="c4", aliases=["connect4", "connectfour"])
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def c4(self, ctx, opponent: discord.Member):
        """Connect Four against another member."""
        msg = await self.start_duel(ctx, opponent, "Connect Four")
        if not msg:
            return
        players = [ctx.author, opponent]
        random.shuffle(players)
        view = ConnectFour(self, ctx.guild.id, *players)
        await msg.edit(content=view.render(), view=view, allowed_mentions=discord.AllowedMentions.none())
        view.message = msg

    @commands.command(name="rpsduel", aliases=["rpsvs"])
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def rpsduel(self, ctx, opponent: discord.Member):
        """Rock-paper-scissors against another member (picks stay secret)."""
        msg = await self.start_duel(ctx, opponent, "Rock-Paper-Scissors")
        if not msg:
            return
        view = RPSDuel(self, ctx.guild.id, ctx.author, opponent)
        await msg.edit(content=f"{ctx.author.mention} vs {opponent.mention}: pick your move!", view=view, allowed_mentions=discord.AllowedMentions.none())
        view.message = msg

    # ---------------------------------------------------------------- trivia
    @commands.command(name="trivia")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def trivia(self, ctx, category: str = None, difficulty: str = None):
        """Multiple-choice trivia. `!trivia [science|geography|history|music|gaming|general] [easy|medium|hard]`"""
        gid = ctx.guild.id if ctx.guild else 0
        if category and category.casefold() in POINTS:
            category, difficulty = None, category
        pool = trivia_pool(self.bot.db, gid, category, difficulty)
        if not pool:
            cats = sorted({q[0] for q in trivia_pool(self.bot.db, gid)})
            return await ui.reply(ctx, embed=ui.err("No questions match. Categories: " + ", ".join(cats)))
        seconds = (self.bot.registry.get(gid, "games").get("trivia_seconds") or 20) if ctx.guild else 20
        q = random.choice(pool)
        view = TriviaQuestion(q, solo=ctx.author.id, seconds=seconds)
        view.message = await ctx.reply(embed=view.embed(), view=view, mention_author=False)
        await view.wait()
        await view.reveal()
        picked = view.answers.get(ctx.author.id)
        pts = POINTS.get(q[1], 1)
        if picked is None:
            await ctx.send(f"⏱️ Time's up, {ctx.author.mention}!", allowed_mentions=discord.AllowedMentions.none())
        elif picked == view.correct_index:
            if ctx.guild:
                self.store.add_points(gid, ctx.author.id, "trivia", pts, True)
            await ctx.send(f"✅ Correct, {ctx.author.mention}! +{pts} point(s)", allowed_mentions=discord.AllowedMentions.none())
        else:
            if ctx.guild:
                self.store.add_points(gid, ctx.author.id, "trivia", 0, False)
            await ctx.send(f"❌ Not quite, {ctx.author.mention}.", allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="triviateam", aliases=["triviaparty", "quiz"])
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.channel)
    async def triviateam(self, ctx, rounds: int = 5, *, teams: str = ""):
        """Channel trivia for everyone. `!triviateam 5` (free-for-all) or `!triviateam 5 Red, Blue` (team mode with a lobby)."""
        if ctx.channel.id in self.busy_channels:
            return await ui.reply(ctx, embed=ui.err("A channel game is already running here."))
        rounds = max(1, min(15, rounds))
        team_names = [t.strip()[:20] for t in teams.split(",") if t.strip()][:4]
        self.busy_channels.add(ctx.channel.id)
        try:
            team_map = None
            if len(team_names) >= 2:
                lobby = TeamLobby(team_names)
                msg = await ctx.send(embed=ui.embed("👥 Team trivia lobby", f"Pick a team in the next 30 seconds! {rounds} rounds."), view=lobby)
                await lobby.wait()
                await msg.edit(view=None)
                team_map = {n: m for n, m in lobby.teams.items()}
                if sum(len(m) for m in team_map.values()) < 2:
                    return await ctx.send("Not enough players joined. Game cancelled.")
            seconds = self.bot.registry.get(ctx.guild.id, "games").get("trivia_seconds") or 20
            pool = trivia_pool(self.bot.db, ctx.guild.id)
            random.shuffle(pool)
            scores = {}
            for n, q in enumerate(pool[:rounds], 1):
                view = TriviaQuestion(q, solo=None, seconds=seconds, teams=team_map)
                e = view.embed()
                e.title = f"Round {n}/{rounds} · " + e.title
                view.message = await ctx.send(embed=e, view=view)
                await asyncio.sleep(seconds)
                view.stop()
                await view.reveal()
                pts = POINTS.get(q[1], 1)
                winners = view.correct_users()
                for uid in winners:
                    scores[uid] = scores.get(uid, 0) + pts
                    self.store.add_points(ctx.guild.id, uid, "trivia", pts, True)
                await ctx.send(f"✅ {len(winners)} correct: " + (", ".join(f"<@{u}>" for u in winners[:20]) or "nobody"), allowed_mentions=discord.AllowedMentions.none())
                await asyncio.sleep(2)
            e = discord.Embed(title="🏁 Trivia results", colour=ui.GOOD)
            if team_map:
                team_scores = {name: sum(scores.get(u, 0) for u in members) for name, members in team_map.items()}
                e.description = "\n".join(f"**{n}**: {s} pts" for n, s in sorted(team_scores.items(), key=lambda kv: -kv[1]))
                best = max(team_scores.values(), default=0)
                e.add_field(name="Winning team", value=", ".join(n for n, s in team_scores.items() if s == best and best > 0) or "nobody scored")
            ranking = sorted(scores.items(), key=lambda kv: -kv[1])
            e.add_field(name="Players", value="\n".join(f"`{i}.` <@{u}> · {s}" for i, (u, s) in enumerate(ranking[:10], 1)) or "No correct answers.", inline=False)
            if ranking:
                self.bot.db.execute("INSERT INTO match_history(guild_id, game, players, winner_id, result, ts) VALUES (?,?,?,?,?,?)",
                                    (ctx.guild.id, "triviateam", json.dumps([u for u, _ in ranking]), ranking[0][0], "win", time.time()))
            await ctx.send(embed=e)
        finally:
            self.busy_channels.discard(ctx.channel.id)

    @commands.command(name="dailyquiz")
    @commands.guild_only()
    async def dailyquiz(self, ctx):
        """Today's 5-question quiz — same questions for everyone, one attempt per day."""
        day = datetime.now().strftime("%Y-%m-%d")
        if self.bot.db.fetchone("SELECT 1 FROM daily_quiz WHERE guild_id=? AND user_id=? AND day=?", (ctx.guild.id, ctx.author.id, day)):
            best = self.bot.db.fetchall("SELECT user_id, score FROM daily_quiz WHERE guild_id=? AND day=? ORDER BY score DESC LIMIT 5", (ctx.guild.id, day))
            return await ui.reply(ctx, embed=ui.embed("📆 Daily quiz", "You've already played today! Top scores:\n" + "\n".join(f"<@{r['user_id']}> · {r['score']}/5" for r in best)))
        if ctx.author.id in self.busy_users:
            return await ui.reply(ctx, embed=ui.err("Finish your current game first."))
        rng = random.Random(f"{ctx.guild.id}-{day}")
        pool = trivia_pool(self.bot.db, ctx.guild.id)
        questions = rng.sample(pool, min(5, len(pool)))
        self.busy_users.add(ctx.author.id)
        self.bot.db.execute("INSERT INTO daily_quiz(guild_id, user_id, day, score) VALUES (?,?,?,0)", (ctx.guild.id, ctx.author.id, day))
        score = 0
        try:
            for n, q in enumerate(questions, 1):
                view = TriviaQuestion(q, solo=ctx.author.id, seconds=20)
                e = view.embed()
                e.title = f"📆 Daily quiz {n}/{len(questions)}"
                view.message = await ctx.send(embed=e, view=view)
                await view.wait()
                await view.reveal()
                if view.answers.get(ctx.author.id) == view.correct_index:
                    score += 1
        finally:
            self.busy_users.discard(ctx.author.id)
            self.bot.db.execute("UPDATE daily_quiz SET score=? WHERE guild_id=? AND user_id=? AND day=?", (score, ctx.guild.id, ctx.author.id, day))
        self.store.add_points(ctx.guild.id, ctx.author.id, "dailyquiz", score, score >= 3)
        await ctx.send(f"📆 {ctx.author.mention} scored **{score}/{len(questions)}** on today's quiz!", allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="triviaadd")
    @commands.guild_only()
    @checks.admin_only()
    async def triviaadd(self, ctx, *, spec: str):
        """Add a custom question: `category | easy|medium|hard | question | correct | wrong1 | wrong2 | wrong3 | explanation`"""
        parts = [p.strip() for p in spec.split("|")]
        if len(parts) < 7 or parts[1].casefold() not in POINTS:
            return await ui.reply(ctx, embed=ui.err("Format: `category | easy|medium|hard | question | correct | wrong1 | wrong2 | wrong3 | explanation`"))
        wrong = parts[4:7]
        if parts[3] in wrong or len(set(wrong)) < 3:
            return await ui.reply(ctx, embed=ui.err("The correct answer and the three wrong answers must all be different."))
        self.bot.db.insert("INSERT INTO trivia_custom(guild_id, category, difficulty, question, correct, wrong, explanation, author_id) VALUES (?,?,?,?,?,?,?,?)",
                           (ctx.guild.id, parts[0].casefold()[:30], parts[1].casefold(), parts[2][:250], parts[3][:80], json.dumps([w[:80] for w in wrong]), (parts[7] if len(parts) > 7 else "")[:300], ctx.author.id))
        await ui.reply(ctx, embed=ui.ok("Question added."))

    @commands.command(name="triviaremove")
    @commands.guild_only()
    @checks.admin_only()
    async def triviaremove(self, ctx, question_id: int = None):
        """List custom questions, or remove one by ID."""
        if question_id is None:
            rows = self.bot.db.fetchall("SELECT id, category, question FROM trivia_custom WHERE guild_id=?", (ctx.guild.id,))
            return await ui.paginate(ctx, ui.pages_from_lines("Custom trivia", [f"`{r['id']}` [{r['category']}] {ui.clip(r['question'], 80)}" for r in rows]))
        n = self.bot.db.execute("DELETE FROM trivia_custom WHERE guild_id=? AND id=?", (ctx.guild.id, question_id)).rowcount
        await ui.reply(ctx, embed=ui.ok("Removed.") if n else ui.err("No such question."))

    # ---------------------------------------------------------------- message-based games
    async def ask_loop(self, ctx, check, timeout):
        try:
            return await self.bot.wait_for("message", timeout=timeout, check=check)
        except asyncio.TimeoutError:
            return None

    @commands.command(name="guessnumber", aliases=["numberguess", "gn"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def guessnumber(self, ctx, maximum: int = 100):
        """Guess my number (7 tries, hints after each guess)."""
        if ctx.author.id in self.busy_users:
            return await ui.reply(ctx, embed=ui.err("Finish your current game first."))
        maximum = max(10, min(1000, maximum))
        secret = secrets.randbelow(maximum) + 1
        tries = 7 if maximum <= 100 else 10
        self.busy_users.add(ctx.author.id)
        await ctx.reply(f"🔢 I'm thinking of a number from 1 to {maximum}. You have {tries} tries — type your guesses!", mention_author=False)
        try:
            for attempt in range(1, tries + 1):
                msg = await self.ask_loop(ctx, lambda m: m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content.strip().isdigit(), 45)
                if msg is None:
                    return await ctx.send(f"⏱️ Timed out. It was **{secret}**.")
                guess = int(msg.content)
                if guess == secret:
                    if ctx.guild:
                        self.store.record(ctx.guild.id, "guessnumber", [ctx.author.id], ctx.author.id, "solo", {ctx.author.id: tries - attempt + 1})
                    return await msg.reply(f"🎉 Yes! **{secret}** in {attempt} tr{'y' if attempt == 1 else 'ies'}.", mention_author=False)
                await msg.add_reaction("⬆️" if guess < secret else "⬇️")
            if ctx.guild:
                self.store.record(ctx.guild.id, "guessnumber", [ctx.author.id], None, "solo")
            await ctx.send(f"Out of tries! It was **{secret}**.")
        finally:
            self.busy_users.discard(ctx.author.id)

    @commands.command(name="hangman", aliases=["wordguess"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def hangman(self, ctx):
        """Guess the word letter by letter (6 lives). Type single letters or the whole word."""
        if ctx.author.id in self.busy_users:
            return await ui.reply(ctx, embed=ui.err("Finish your current game first."))
        word = random.choice(WORDS)
        guessed, lives = set(), 6
        self.busy_users.add(ctx.author.id)

        def board():
            return " ".join(c.upper() if c in guessed else "＿" for c in word)

        status = await ctx.reply(f"🔤 **Word guess** — {board()}\nLives: {'❤️' * lives}", mention_author=False)
        try:
            while lives > 0:
                msg = await self.ask_loop(ctx, lambda m: m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content.strip().isalpha(), 60)
                if msg is None:
                    return await ctx.send(f"⏱️ Timed out. The word was **{word}**.")
                text = msg.content.strip().casefold()
                if len(text) > 1:
                    if text == word:
                        guessed.update(word)
                    else:
                        lives -= 1
                elif text in guessed:
                    await msg.add_reaction("🔁")
                    continue
                else:
                    guessed.add(text)
                    if text not in word:
                        lives -= 1
                if all(c in guessed for c in word):
                    if ctx.guild:
                        self.store.record(ctx.guild.id, "hangman", [ctx.author.id], ctx.author.id, "solo", {ctx.author.id: lives})
                    return await status.edit(content=f"🎉 **{word.upper()}** — solved with {lives} li{'fe' if lives == 1 else 'ves'} left!")
                await status.edit(content=f"🔤 **Word guess** — {board()}\nLives: {'❤️' * lives}{'🖤' * (6 - lives)}\nTried: {' '.join(sorted(l.upper() for l in guessed if l not in word))}")
            if ctx.guild:
                self.store.record(ctx.guild.id, "hangman", [ctx.author.id], None, "solo")
            await status.edit(content=f"💀 Out of lives! The word was **{word.upper()}**.")
        finally:
            self.busy_users.discard(ctx.author.id)

    @commands.command(name="emojiguess", aliases=["emojiquiz"])
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def emojiguess(self, ctx):
        """Guess the movie/phrase from emojis — first correct answer in the channel wins."""
        if ctx.channel.id in self.busy_channels:
            return await ui.reply(ctx, embed=ui.err("A channel game is already running here."))
        clue, answer = random.choice(EMOJI_PUZZLES)
        self.busy_channels.add(ctx.channel.id)
        try:
            await ctx.send(f"🤔 **Emoji guess:** {clue}\nFirst to type the answer in 30 seconds wins!")
            normal = lambda t: "".join(ch for ch in t.casefold() if ch.isalnum())
            msg = await self.ask_loop(ctx, lambda m: m.channel.id == ctx.channel.id and not m.author.bot and normal(m.content) == normal(answer), 30)
            if msg is None:
                return await ctx.send(f"⏱️ Nobody got it! It was **{answer.title()}**.")
            if ctx.guild:
                self.store.add_points(ctx.guild.id, msg.author.id, "emojiguess", 2)
            await msg.reply(f"🎉 Correct! **{answer.title()}**", mention_author=False)
        finally:
            self.busy_channels.discard(ctx.channel.id)

    @commands.command(name="riddle")
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def riddle(self, ctx):
        """A riddle with a reveal button."""
        question, answer = random.choice(RIDDLES)
        view = discord.ui.View(timeout=300)
        button = discord.ui.Button(label="Reveal answer", emoji="💡", style=discord.ButtonStyle.secondary)

        async def reveal(interaction):
            await interaction.response.send_message(f"💡 {answer}", ephemeral=True)
        button.callback = reveal
        view.add_item(button)
        await ctx.reply(embed=discord.Embed(title="🧩 Riddle", description=question, colour=ui.PINK), view=view, mention_author=False)

    @commands.command(name="memory")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def memory(self, ctx, length: int = 5):
        """Memorise the emoji sequence, then repeat it with the buttons."""
        if ctx.author.id in self.busy_users:
            return await ui.reply(ctx, embed=ui.err("Finish your current game first."))
        length = max(3, min(9, length))
        sequence = [random.choice(MEMORY_EMOJIS) for _ in range(length)]
        self.busy_users.add(ctx.author.id)
        try:
            msg = await ctx.reply(f"🧠 Memorise this ({length} items, 5 seconds): " + "".join(sequence), mention_author=False)
            await asyncio.sleep(5)
            view = MemoryView(ctx.author.id, sequence)
            await msg.edit(content="Now repeat the sequence with the buttons!", view=view)
            try:
                await asyncio.wait_for(view.done.wait(), timeout=45)
            except asyncio.TimeoutError:
                view.result = False
            ok = bool(view.result)
            if ctx.guild:
                self.store.record(ctx.guild.id, "memory", [ctx.author.id], ctx.author.id if ok else None, "solo", {ctx.author.id: length if ok else 0})
            await ctx.send(("🎉 Perfect memory! " if ok else "❌ Not quite. ") + "The sequence was " + "".join(sequence))
        finally:
            self.busy_users.discard(ctx.author.id)

    @commands.command(name="reaction", aliases=["reactiontest", "fastest"])
    @commands.guild_only()
    @commands.cooldown(1, 15, commands.BucketType.channel)
    async def reaction(self, ctx):
        """First to press the button when it turns green wins."""
        view = ReactionView()
        msg = await ctx.send("⚡ Get ready… press when the button turns **green**!", view=view)
        await asyncio.sleep(random.uniform(2.5, 7))
        if view.is_finished():
            return
        view.live = True
        view.started = time.monotonic()
        view.button.style = discord.ButtonStyle.success
        view.button.label = "PRESS!"
        await msg.edit(view=view)
        await view.wait()
        if view.winner:
            self.store.add_points(ctx.guild.id, view.winner[0].id, "reaction", 1)
        else:
            await msg.edit(content="⏱️ Nobody pressed in time.", view=None)

    @commands.group(name="wordchain", invoke_without_command=True)
    @commands.guild_only()
    async def wordchain(self, ctx):
        """Word chain: each word must start with the last letter of the previous one. `!wordchain start|stop`"""
        state = self.word_chains.get(ctx.channel.id)
        await ui.reply(ctx, embed=ui.embed("🔗 Word chain", f"Running here: last word **{state['last']}**, chain length {state['count']}." if state else "Not running here. Start with `!wordchain start`."))

    @wordchain.command(name="start")
    async def wc_start(self, ctx):
        if ctx.channel.id in self.word_chains:
            return await ui.reply(ctx, embed=ui.err("Already running here."))
        first = random.choice(WORDS)
        self.word_chains[ctx.channel.id] = {"last": first, "used": {first}, "last_user": None, "count": 0, "best": 0}
        await ctx.send(f"🔗 **Word chain started!** First word: **{first}**. Next word must start with **{first[-1].upper()}**. No repeats, no going twice in a row. `!wordchain stop` to end.")

    @wordchain.command(name="stop")
    async def wc_stop(self, ctx):
        state = self.word_chains.pop(ctx.channel.id, None)
        await ui.reply(ctx, embed=ui.ok(f"Word chain ended at {state['count']} words.") if state else ui.err("Not running here."), ephemeral=False)

    @commands.Cog.listener()
    async def on_message(self, message):
        state = self.word_chains.get(message.channel.id)
        if state is None or message.author.bot or not message.content:
            return
        word = message.content.strip().casefold()
        if not word.isalpha() or " " in word or len(word) < 2:
            return
        if word[0] != state["last"][-1] or word in state["used"] or message.author.id == state["last_user"]:
            reason = "already used" if word in state["used"] else ("wait for someone else" if message.author.id == state["last_user"] else f"must start with **{state['last'][-1].upper()}**")
            try:
                await message.add_reaction("❌")
            except discord.HTTPException:
                pass
            if state["count"] >= 5:
                await ui.safe_send(message.channel, f"💥 Chain broken at **{state['count']}** ({reason})! Starting over from **{state['last']}**.")
                state["count"] = 0
            return
        state.update(last=word, last_user=message.author.id, count=state["count"] + 1)
        state["used"].add(word)
        if message.guild:
            self.store.add_points(message.guild.id, message.author.id, "wordchain", 1)
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass

    # ---------------------------------------------------------------- boards
    @commands.command(name="gamesboard", aliases=["gameslb", "gameleaderboard"])
    @commands.guild_only()
    async def gamesboard(self, ctx, game: str = None):
        """Game leaderboard (all games, or one: tictactoe, connect4, rps, trivia, hangman, guessnumber, memory, reaction, wordchain, emojiguess, dailyquiz)."""
        if game:
            rows = self.bot.db.fetchall("SELECT user_id, wins, losses, draws, points FROM game_scores WHERE guild_id=? AND game=? ORDER BY points DESC, wins DESC LIMIT 15", (ctx.guild.id, game.casefold()))
            title = f"🏆 {game} leaderboard"
        else:
            rows = self.bot.db.fetchall("SELECT user_id, sum(wins) wins, sum(losses) losses, sum(draws) draws, sum(points) points FROM game_scores WHERE guild_id=? GROUP BY user_id ORDER BY points DESC LIMIT 15", (ctx.guild.id,))
            title = "🏆 Games leaderboard (all games)"
        lines = [f"`{i}.` <@{r['user_id']}> · **{r['points']}** pts · {r['wins']}W {r['losses']}L {r['draws']}D" for i, r in enumerate(rows, 1)]
        await ui.reply(ctx, embed=ui.embed(title, "\n".join(lines) or "No games played yet."), ephemeral=False)

    @commands.command(name="gamehistory", aliases=["matches"])
    @commands.guild_only()
    async def gamehistory(self, ctx, member: discord.Member = None):
        """Recent matches (optionally for one member)."""
        rows = self.bot.db.fetchall("SELECT * FROM match_history WHERE guild_id=? ORDER BY id DESC LIMIT 200", (ctx.guild.id,))
        if member:
            rows = [r for r in rows if member.id in json.loads(r["players"])]
        lines = []
        for r in rows[:60]:
            players = json.loads(r["players"])
            who = " vs ".join(f"<@{p}>" for p in players[:4])
            res = "draw" if r["result"] == "draw" else (f"winner <@{r['winner_id']}>" if r["winner_id"] else "no winner")
            lines.append(f"**{r['game']}** · {who} · {res} · <t:{int(r['ts'])}:R>")
        await ui.paginate(ctx, ui.pages_from_lines("🕹️ Match history", lines, per_page=10))


async def setup(bot):
    await bot.add_cog(Games(bot))
