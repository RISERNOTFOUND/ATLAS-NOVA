"""Onboarding additions around ATLAS's existing welcome system.

ATLAS's existing one-time welcome (and its duplicate protection in void_data.json) is untouched.
This module only adds optional extras an admin can switch on: a custom welcome text, a generated
welcome card image, account-age info, a guidance DM, goodbye messages and a newcomer nudge.
"""
import asyncio
import io
import time

import discord
from discord.ext import commands, tasks

from common import ui
from common.config import Module, Option

PLACEHOLDERS = "{user} {name} {server} {count} {rules} {roles}"


def render_template(text, member):
    guild = member.guild
    rules = discord.utils.get(guild.text_channels, name="rules")
    return (text.replace("{user}", member.mention).replace("{name}", member.display_name).replace("{server}", guild.name)
            .replace("{count}", str(guild.member_count or "")).replace("{rules}", rules.mention if rules else "#rules")
            .replace("{roles}", "`!roles`"))


def make_card(avatar_bytes, title, subtitle):
    """Welcome card with Pillow (installed alongside matplotlib). Returns PNG bytes."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    width, height = 900, 300
    card = Image.new("RGB", (width, height), (27, 28, 40))
    draw = ImageDraw.Draw(card)
    for x in range(width):  # soft gradient band
        shade = int(40 + 40 * x / width)
        draw.line([(x, height - 14), (x, height)], fill=(255, 126, 182 - shade // 3))
    if avatar_bytes:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((200, 200))
        mask = Image.new("L", (200, 200), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 200, 200), fill=255)
        avatar = ImageOps.fit(avatar, (200, 200))
        card.paste(avatar, (50, 45), mask)
        draw.ellipse((46, 41, 254, 249), outline=(255, 126, 182), width=5)
    try:
        big = ImageFont.truetype("arial.ttf", 46)
        small = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        try:
            big = ImageFont.truetype("DejaVuSans-Bold.ttf", 46)
            small = ImageFont.truetype("DejaVuSans.ttf", 28)
        except OSError:
            big = small = ImageFont.load_default()
    clean = lambda s: "".join(ch for ch in s if ord(ch) < 0x2000)[:28]
    draw.text((290, 95), clean(title), font=big, fill=(236, 235, 245))
    draw.text((290, 165), clean(subtitle), font=small, fill=(139, 141, 166))
    buf = io.BytesIO()
    card.save(buf, format="PNG")
    return buf.getvalue()


class Onboarding(commands.Cog, name="Onboarding"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        bot.db.migrate("onboarding_001", """
            CREATE TABLE IF NOT EXISTS newcomer_nudges (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                due_ts REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
            CREATE UNIQUE INDEX IF NOT EXISTS idx_nudge_member ON newcomer_nudges(guild_id, user_id);
            CREATE TABLE IF NOT EXISTS goodbyes_sent (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, joined_ts REAL NOT NULL,
                PRIMARY KEY (guild_id, user_id, joined_ts))""")
        bot.registry.register(Module("onboarding", "Welcome & onboarding extras", "👋",
                                     f"Optional extras on top of ATLAS's welcome. Placeholders: {PLACEHOLDERS}", [
            Option("welcome_message", "Custom welcome text", "text", "", "Replaces the welcome embed text (leave empty for the default)", max_length=1500),
            Option("welcome_card", "Welcome card image", "bool", False, "Adds a generated card with the member's avatar"),
            Option("show_account_age", "Show account age", "bool", True),
            Option("guide_dm", "Guidance DM text", "text", "", "Sent privately to new members (empty = off)", max_length=1500),
            Option("goodbye_channel", "Goodbye channel", "channel", None), Option("goodbye_message", "Goodbye text", "text", "👋 **{name}** has left {server}. We'll miss you!", max_length=500),
            Option("nudge_hours", "Newcomer nudge after (hours)", "int", 0, "Friendly reminder if they haven't chatted yet; 0 = off", 0, 168),
            Option("nudge_text", "Nudge text", "text", "Hey {name}! 👋 Say hi in chat whenever you're ready, and grab some roles with {roles}.", max_length=500),
        ]))

    async def cog_load(self):
        self.nudge_loop.start()

    async def cog_unload(self):
        self.nudge_loop.cancel()

    def settings(self, guild_id):
        return self.bot.registry.get(guild_id, "onboarding")

    async def welcome_extras(self, member, embed):
        """Called by bot.py's welcome delivery. Mutates the embed; returns an optional discord.File."""
        s = self.settings(member.guild.id)
        if not s.get("enabled"):
            return None
        if s.get("welcome_message"):
            embed.description = render_template(s["welcome_message"], member)[:4000]
        if s.get("show_account_age", True):
            embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        if s.get("welcome_card"):
            try:
                avatar = await member.display_avatar.replace(size=256, format="png").read()
                png = await asyncio.to_thread(make_card, avatar, f"Welcome, {member.display_name}!", f"Member #{member.guild.member_count} of {member.guild.name}")
                embed.set_image(url="attachment://welcome.png")
                return discord.File(io.BytesIO(png), filename="welcome.png")
            except Exception as exc:
                print(f"[Onboarding] welcome card skipped: {type(exc).__name__}: {exc}")
        return None

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        s = self.settings(member.guild.id)
        if not s.get("enabled"):
            return
        if s.get("guide_dm"):
            await ui.safe_dm(member, embed=discord.Embed(title=f"Welcome to {member.guild.name}!", description=render_template(s["guide_dm"], member)[:4000], colour=ui.PINK))
        if int(s.get("nudge_hours") or 0) > 0:
            self.db.execute("INSERT OR IGNORE INTO newcomer_nudges(guild_id, user_id, due_ts) VALUES (?,?,?)",
                            (member.guild.id, member.id, time.time() + int(s["nudge_hours"]) * 3600))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.bot:
            return
        s = self.settings(member.guild.id)
        self.db.execute("UPDATE newcomer_nudges SET status='left' WHERE guild_id=? AND user_id=? AND status='pending'", (member.guild.id, member.id))
        if not s.get("enabled") or not s.get("goodbye_channel"):
            return
        joined = member.joined_at.timestamp() if member.joined_at else 0
        try:
            self.db.execute("INSERT INTO goodbyes_sent(guild_id, user_id, joined_ts) VALUES (?,?,?)", (member.guild.id, member.id, joined))
        except Exception:
            return  # already said goodbye for this membership
        channel = member.guild.get_channel(int(s["goodbye_channel"]))
        text = render_template(s.get("goodbye_message") or "👋 **{name}** has left {server}.", member).replace(member.mention, f"**{member.display_name}**")
        await ui.safe_send(channel, text)

    @tasks.loop(minutes=5)
    async def nudge_loop(self):
        rows = self.db.fetchall("SELECT * FROM newcomer_nudges WHERE status='pending' AND due_ts<=? LIMIT 50", (time.time(),))
        stats = self.bot.get_cog("StatsCog")
        for row in rows:
            if not self.db.claim("newcomer_nudges", row["id"]):
                continue
            guild = self.bot.get_guild(row["guild_id"])
            member = guild.get_member(row["user_id"]) if guild else None
            if member is None:
                continue
            s = self.settings(guild.id)
            if not s.get("enabled"):
                continue
            chatted = stats is not None and stats.store.user_total(guild.id, member.id, "all")["m"] > 0
            if chatted:
                continue
            from common.diag import user_prefs
            if not user_prefs(self.db, member.id).get("dm_reminders", 1):
                continue
            await ui.safe_dm(member, render_template(s.get("nudge_text") or "Say hi whenever you're ready! 👋", member))

    @nudge_loop.before_loop
    async def before_nudge(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Onboarding(bot))
