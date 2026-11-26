"""Scheduled/recurring announcements, formatted one-off announcements, and creator feeds
(RSS/Atom, YouTube uploads via YouTube's public RSS feed, optional Twitch live alerts).

Feeds are optional and free: RSS/YouTube need no keys. Twitch needs TWITCH_CLIENT_ID and
TWITCH_CLIENT_SECRET environment variables (a free Twitch developer app); keys are never shown.
New feeds are "seeded" on first check so old posts are not flooded into the channel.
"""
import asyncio
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

from common import checks, ui
from common.config import Module, Option
from common.timeparse import get_tz, parse_duration, parse_hhmm, parse_when, tz_name

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
YT_RE = re.compile(r"(UC[\w-]{22})")


def fill(template, guild):
    now = datetime.now(timezone.utc)
    return (template.replace("{server}", guild.name).replace("{members}", str(guild.member_count or ""))
            .replace("{date}", discord.utils.format_dt(now, "D")))


def next_run(row, after=None):
    """Compute the next UTC timestamp for an announcement row (dict)."""
    after = after or time.time()
    tz = get_tz(row["tz"] or "Asia/Kolkata")
    kind = row["kind"]
    if kind == "once":
        return None
    if kind == "interval":
        nxt = row["next_ts"] or after
        while nxt <= after:
            nxt += row["interval_minutes"] * 60
        return nxt
    hh, mm = parse_hhmm(row["at_time"])
    local = datetime.fromtimestamp(after, tz).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if kind == "daily":
        while local.timestamp() <= after:
            local += timedelta(days=1)
    elif kind == "weekly":
        target = WEEKDAYS.index(row["weekday"])
        while local.weekday() != target or local.timestamp() <= after:
            local += timedelta(days=1)
    elif kind == "monthly":
        day = int(row["weekday"] or 1)
        candidate = local
        for _ in range(400):
            if candidate.day == min(day, 28) and candidate.timestamp() > after:
                break
            candidate += timedelta(days=1)
        local = candidate
    return local.timestamp()


def parse_feed(text):
    """Return list of (id, title, link) newest-first from RSS or Atom XML."""
    root = ET.fromstring(text)
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{atom}entry"):
        eid = (entry.findtext(f"{atom}id") or "").strip()
        title = (entry.findtext(f"{atom}title") or "").strip()
        link_el = entry.find(f"{atom}link")
        link = link_el.get("href") if link_el is not None else ""
        out.append((eid or link, title, link))
    if not out:
        for item in root.iter("item"):
            guid = (item.findtext("guid") or item.findtext("link") or "").strip()
            out.append((guid, (item.findtext("title") or "").strip(), (item.findtext("link") or "").strip()))
    return out


class Announce(commands.Cog, name="Announcements"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.session = None
        self.twitch_token = (None, 0)
        bot.db.migrate("announce_001", """
            CREATE TABLE IF NOT EXISTS announcements (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
                content TEXT NOT NULL, as_embed INTEGER DEFAULT 1, kind TEXT NOT NULL, at_time TEXT, weekday TEXT, interval_minutes INTEGER, tz TEXT,
                next_ts REAL, enabled INTEGER DEFAULT 1, last_sent_ts REAL, created_by INTEGER);
            CREATE INDEX IF NOT EXISTS idx_ann_due ON announcements(enabled, next_ts);
            CREATE TABLE IF NOT EXISTS feeds (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, kind TEXT NOT NULL,
                source TEXT NOT NULL, label TEXT, template TEXT, seen TEXT NOT NULL DEFAULT '[]', seeded INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
                last_ok_ts REAL, failures INTEGER DEFAULT 0, last_error TEXT, ping_role INTEGER)""")
        bot.registry.register(Module("announcements", "Announcements", "📣", "Scheduled and recurring announcements.", [
            Option("timezone", "Default timezone", "tz", "Asia/Kolkata"),
        ], default_enabled=True))
        bot.registry.register(Module("feeds", "Creator feeds", "📡", "YouTube uploads, RSS feeds and optional Twitch live alerts.", [
            Option("default_channel", "Default feed channel", "channel", None),
            Option("check_minutes", "Check every (minutes)", "int", 10, "", 5, 120),
        ]))

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers={"User-Agent": "ATLAS-Bot/1.0 (+Discord community bot)"})
        self.announce_loop.start()
        self.feed_loop.start()

    async def cog_unload(self):
        self.announce_loop.cancel()
        self.feed_loop.cancel()
        if self.session:
            await self.session.close()

    # ================================================================= announcements
    @tasks.loop(seconds=30)
    async def announce_loop(self):
        now = time.time()
        for row in self.db.fetchall("SELECT * FROM announcements WHERE enabled=1 AND next_ts IS NOT NULL AND next_ts<=?", (now,)):
            row = dict(row)
            if not self.bot.registry.enabled(row["guild_id"], "announcements"):
                continue
            nxt = next_run(row, after=now) if row["kind"] != "once" else None
            cur = self.db.execute("UPDATE announcements SET next_ts=?, last_sent_ts=?, enabled=? WHERE id=? AND next_ts=?",
                                  (nxt, now, 0 if nxt is None else 1, row["id"], row["next_ts"]))
            if cur.rowcount != 1:
                continue  # someone else claimed it -> no duplicate
            if now - row["next_ts"] > 6 * 3600:
                continue  # missed by more than 6 hours (bot offline): skip rather than post something stale
            await self.post(row)

    @announce_loop.before_loop
    async def before_announce(self):
        await self.bot.wait_until_ready()

    async def post(self, row):
        guild = self.bot.get_guild(row["guild_id"])
        channel = guild.get_channel(row["channel_id"]) if guild else None
        if channel is None:
            return
        text = fill(row["content"], guild)
        if row["as_embed"]:
            await ui.safe_send(channel, embed=discord.Embed(description=text[:4000], colour=ui.PINK))
        else:
            await ui.safe_send(channel, text[:2000])

    def describe(self, row):
        if row["kind"] == "once":
            when = f"once at <t:{int(row['next_ts'])}:f>" if row["next_ts"] else "once (sent)"
        elif row["kind"] == "interval":
            when = f"every {row['interval_minutes']} min"
        elif row["kind"] == "weekly":
            when = f"weekly {row['weekday']} {row['at_time']} {row['tz']}"
        elif row["kind"] == "monthly":
            when = f"monthly day {row['weekday']} {row['at_time']} {row['tz']}"
        else:
            when = f"daily {row['at_time']} {row['tz']}"
        nxt = f" · next <t:{int(row['next_ts'])}:R>" if row["next_ts"] and row["enabled"] else ""
        return f"`#{row['id']}` {'🟢' if row['enabled'] else '⚫'} <#{row['channel_id']}> · {when}{nxt}\n> {ui.clip(row['content'], 90)}"

    @commands.group(name="announce", aliases=["announcement"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_only()
    async def announce(self, ctx):
        """Scheduled announcements. Subcommands: add, list, remove, toggle, test, post."""
        await ui.reply(ctx, embed=ui.embed("📣 Announcements",
            "`!announce add #ch daily 10:00 | text`\n`!announce add #ch weekly mon 18:00 | text`\n`!announce add #ch monthly 1 10:00 | text`\n"
            "`!announce add #ch every 6h | text`\n`!announce add #ch once 2026-12-31 20:00 | text`\n"
            "`!announce list` · `!announce remove <id>` · `!announce toggle <id>` · `!announce test <id>`\n"
            "`!announce post #ch Title | text` (formatted, posts now after a preview)\nPlaceholders: {server} {members} {date}. Add `--plain` for a normal message instead of an embed."))

    @announce.command(name="add")
    async def add(self, ctx, channel: discord.TextChannel, *, spec: str):
        schedule, sep, content = spec.partition("|")
        if not sep or not content.strip():
            return await ui.reply(ctx, embed=ui.err("Separate the schedule and the text with `|`."))
        plain = "--plain" in content
        content = content.replace("--plain", "").strip()
        words = schedule.split()
        tz = tz_name(get_tz(self.bot.registry.get(ctx.guild.id, "announcements").get("timezone") or "Asia/Kolkata"))
        row = {"kind": None, "at_time": None, "weekday": None, "interval_minutes": None, "tz": tz, "next_ts": None}
        try:
            kind = words[0].casefold() if words else ""
            if kind == "daily" and len(words) == 2:
                h, m = parse_hhmm(words[1])
                row.update(kind="daily", at_time=f"{h:02d}:{m:02d}")
            elif kind == "weekly" and len(words) == 3 and words[1][:3].casefold() in WEEKDAYS:
                h, m = parse_hhmm(words[2])
                row.update(kind="weekly", weekday=words[1][:3].casefold(), at_time=f"{h:02d}:{m:02d}")
            elif kind == "monthly" and len(words) == 3 and words[1].isdigit() and 1 <= int(words[1]) <= 28:
                h, m = parse_hhmm(words[2])
                row.update(kind="monthly", weekday=words[1], at_time=f"{h:02d}:{m:02d}")
            elif kind == "every" and len(words) == 2:
                delta = parse_duration(words[1], minimum=1800, maximum=30 * 86400)
                if delta is None:
                    raise ValueError("Interval must be 30m to 30d.")
                row.update(kind="interval", interval_minutes=int(delta.total_seconds() // 60), next_ts=time.time() + delta.total_seconds())
            elif kind == "once" and len(words) >= 2:
                row.update(kind="once", next_ts=parse_when(" ".join(words[1:]), get_tz(tz)).timestamp())
            else:
                raise ValueError("Unknown schedule. See `!announce` for examples (monthly days are 1–28).")
        except ValueError as exc:
            return await ui.reply(ctx, embed=ui.err(str(exc)))
        if row["kind"] in ("daily", "weekly", "monthly"):
            row["next_ts"] = next_run(row)
        if not channel.permissions_for(ctx.guild.me).send_messages:
            return await ui.reply(ctx, embed=ui.err(f"I can't post in {channel.mention}."))
        aid = self.db.insert("INSERT INTO announcements(guild_id, channel_id, content, as_embed, kind, at_time, weekday, interval_minutes, tz, next_ts, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (ctx.guild.id, channel.id, content[:3500], int(not plain), row["kind"], row["at_time"], row["weekday"], row["interval_minutes"], row["tz"], row["next_ts"], ctx.author.id))
        await ui.reply(ctx, embed=ui.ok(f"Announcement #{aid} scheduled. First post <t:{int(row['next_ts'])}:R>."))

    @announce.command(name="list")
    async def list_(self, ctx):
        rows = self.db.fetchall("SELECT * FROM announcements WHERE guild_id=? ORDER BY id", (ctx.guild.id,))
        await ui.paginate(ctx, ui.pages_from_lines("📣 Scheduled announcements", [self.describe(dict(r)) for r in rows], per_page=8))

    @announce.command(name="remove", aliases=["delete"])
    async def remove(self, ctx, aid: int):
        n = self.db.execute("DELETE FROM announcements WHERE guild_id=? AND id=?", (ctx.guild.id, aid)).rowcount
        await ui.reply(ctx, embed=ui.ok("Removed.") if n else ui.err("No such announcement."))

    @announce.command(name="toggle")
    async def toggle(self, ctx, aid: int):
        row = self.db.fetchone("SELECT * FROM announcements WHERE guild_id=? AND id=?", (ctx.guild.id, aid))
        if not row:
            return await ui.reply(ctx, embed=ui.err("No such announcement."))
        row = dict(row)
        enabled = 0 if row["enabled"] else 1
        nxt = row["next_ts"]
        if enabled and row["kind"] != "once":
            nxt = next_run(row)
        self.db.execute("UPDATE announcements SET enabled=?, next_ts=? WHERE id=?", (enabled, nxt, aid))
        await ui.reply(ctx, embed=ui.ok(f"Announcement #{aid} {'enabled' if enabled else 'disabled'}."))

    @announce.command(name="test")
    async def test(self, ctx, aid: int):
        row = self.db.fetchone("SELECT * FROM announcements WHERE guild_id=? AND id=?", (ctx.guild.id, aid))
        if not row:
            return await ui.reply(ctx, embed=ui.err("No such announcement."))
        text = fill(row["content"], ctx.guild)
        await ui.reply(ctx, content="Preview (only you see the command result; nothing was posted):", embed=discord.Embed(description=text[:4000], colour=ui.PINK))

    @announce.command(name="post")
    async def post_now(self, ctx, channel: discord.TextChannel, *, text: str):
        """Post a formatted announcement now: `!announce post #ch Title | body [--ping @role]`"""
        ping = None
        m = re.search(r"--ping\s+<@&(\d+)>", text)
        if m:
            ping = ctx.guild.get_role(int(m.group(1)))
            text = text.replace(m.group(0), "").strip()
        title, sep, body = text.partition("|")
        e = discord.Embed(title=title.strip()[:256] if sep else None, description=(body if sep else title).strip()[:4000], colour=ui.PINK, timestamp=discord.utils.utcnow())
        e.set_footer(text=f"Announced by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        preview = await ctx.send(content=f"**Preview** — will be posted in {channel.mention}" + (f" pinging {ping.mention}" if ping else ""), embed=e,
                                 allowed_mentions=discord.AllowedMentions.none())
        if not await ui.confirm(ctx, "Post this announcement?", confirm_label="Post", danger=False):
            return
        await channel.send(content=ping.mention if ping else None, embed=e, allowed_mentions=discord.AllowedMentions(roles=[ping] if ping else False))
        try:
            await preview.delete()
        except discord.HTTPException:
            pass
        await ui.reply(ctx, embed=ui.ok(f"Posted in {channel.mention}."))

    # ================================================================= feeds
    async def fetch_text(self, url):
        async with self.session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            data = await resp.content.read(2_000_001)
            if len(data) > 2_000_000:
                raise RuntimeError("Feed too large")
            return data.decode("utf-8", errors="replace")

    async def twitch_live(self, login):
        cid, secret = os.getenv("TWITCH_CLIENT_ID"), os.getenv("TWITCH_CLIENT_SECRET")
        if not cid or not secret:
            raise RuntimeError("Twitch is not configured (set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET)")
        token, expires = self.twitch_token
        if not token or time.time() > expires - 60:
            async with self.session.post("https://id.twitch.tv/oauth2/token", params={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Twitch auth failed (HTTP {resp.status})")
                payload = await resp.json()
            token, expires = payload["access_token"], time.time() + payload.get("expires_in", 3600)
            self.twitch_token = (token, expires)
        async with self.session.get("https://api.twitch.tv/helix/streams", params={"user_login": login},
                                    headers={"Client-Id": cid, "Authorization": f"Bearer {token}"}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Twitch API HTTP {resp.status}")
            data = (await resp.json()).get("data", [])
        return data[0] if data else None

    async def check_feed(self, feed):
        seen = json.loads(feed["seen"] or "[]")
        new_items = []
        if feed["kind"] == "twitch":
            stream = await self.twitch_live(feed["source"])
            if stream and stream["id"] not in seen:
                new_items.append((stream["id"], stream.get("title") or "Live now", f"https://twitch.tv/{feed['source']}"))
        else:
            url = feed["source"] if feed["kind"] == "rss" else f"https://www.youtube.com/feeds/videos.xml?channel_id={feed['source']}"
            items = parse_feed(await self.fetch_text(url))
            new_items = [i for i in items[:15] if i[0] and i[0] not in seen][::-1]
        guild = self.bot.get_guild(feed["guild_id"])
        channel = guild.get_channel(feed["channel_id"]) if guild else None
        if feed["seeded"] and channel:
            for item_id, title, link in new_items[:5]:
                default = {"youtube": "📺 **{label}** uploaded: **{title}**\n{link}", "twitch": "🔴 **{label}** is live: **{title}**\n{link}"}.get(feed["kind"], "📰 **{label}**: **{title}**\n{link}")
                text = (feed["template"] or default).replace("{title}", title).replace("{link}", link).replace("{label}", feed["label"] or feed["source"])
                role = guild.get_role(feed["ping_role"]) if feed["ping_role"] else None
                await ui.safe_send(channel, (f"{role.mention} " if role else "") + text[:1900], allowed_mentions=discord.AllowedMentions(roles=[role] if role else False))
        seen = ([i[0] for i in new_items] + seen)[:60]
        self.db.execute("UPDATE feeds SET seen=?, seeded=1, last_ok_ts=?, failures=0, last_error=NULL WHERE id=?", (json.dumps(seen), time.time(), feed["id"]))

    @tasks.loop(minutes=5)
    async def feed_loop(self):
        now = time.time()
        for row in self.db.fetchall("SELECT * FROM feeds WHERE enabled=1"):
            feed = dict(row)
            s = self.bot.registry.get(feed["guild_id"], "feeds")
            if not s.get("enabled"):
                continue
            interval = max(5, int(s.get("check_minutes") or 10)) * 60 * (1 + min(feed["failures"] or 0, 6))
            if feed["last_ok_ts"] and now - feed["last_ok_ts"] < interval and not feed["failures"]:
                continue
            try:
                await self.check_feed(feed)
            except Exception as exc:
                self.db.execute("UPDATE feeds SET failures=failures+1, last_error=? WHERE id=?", (f"{type(exc).__name__}: {str(exc)[:150]}", feed["id"]))
            await asyncio.sleep(1)

    @feed_loop.before_loop
    async def before_feeds(self):
        await self.bot.wait_until_ready()

    @commands.group(name="feeds", aliases=["feed"], invoke_without_command=True)
    @commands.guild_only()
    @checks.admin_only()
    async def feeds(self, ctx):
        """Creator feeds: add youtube/rss/twitch, list, remove, health."""
        rows = self.db.fetchall("SELECT * FROM feeds WHERE guild_id=?", (ctx.guild.id,))
        lines = [f"`#{r['id']}` {'🟢' if r['enabled'] else '⚫'} **{r['kind']}** {r['label'] or r['source']} → <#{r['channel_id']}>" + (f" ⚠️ {r['failures']} failures" if r["failures"] else "") for r in rows]
        e = ui.embed("📡 Feeds", "\n".join(lines) or "No feeds yet.")
        e.add_field(name="Add", value="`!feeds add youtube #ch <channel ID UC… or URL> [label]`\n`!feeds add rss #ch <url> [label]`\n`!feeds add twitch #ch <login>` (needs TWITCH_CLIENT_ID/SECRET)\n`!feeds remove <id>` · `!feeds health` · `!feeds ping <id> @role` · `!feeds template <id> <text with {title} {link} {label}>`")
        if not self.bot.registry.get(ctx.guild.id, "feeds").get("enabled"):
            e.set_footer(text="The feeds module is off: enable it with !config feeds")
        await ui.reply(ctx, embed=e)

    @feeds.command(name="add")
    async def feeds_add(self, ctx, kind: str, channel: discord.TextChannel, source: str, *, label: str = ""):
        kind = kind.casefold()
        if kind == "youtube":
            m = YT_RE.search(source)
            if not m:
                return await ui.reply(ctx, embed=ui.err("Give the YouTube channel ID (starts with `UC`, 24 characters) or a URL containing it. Handles like @name don't contain the ID; open the channel's About → Share → Copy channel ID."))
            source = m.group(1)
        elif kind == "rss":
            if not re.match(r"^https?://", source):
                return await ui.reply(ctx, embed=ui.err("RSS feeds need an http(s) URL."))
        elif kind == "twitch":
            if not re.fullmatch(r"[A-Za-z0-9_]{3,25}", source):
                return await ui.reply(ctx, embed=ui.err("That isn't a valid Twitch login."))
            if not (os.getenv("TWITCH_CLIENT_ID") and os.getenv("TWITCH_CLIENT_SECRET")):
                return await ui.reply(ctx, embed=ui.err("Twitch alerts need a free Twitch developer app: set the TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET environment variables on the bot computer, then restart."))
            source = source.casefold()
        else:
            return await ui.reply(ctx, embed=ui.err("Kind must be youtube, rss or twitch."))
        fid = self.db.insert("INSERT INTO feeds(guild_id, channel_id, kind, source, label) VALUES (?,?,?,?,?)", (ctx.guild.id, channel.id, kind, source[:500], label[:80] or None))
        try:
            await self.check_feed(dict(self.db.fetchone("SELECT * FROM feeds WHERE id=?", (fid,))))
            note = "Checked it once (existing posts were skipped). New ones will be announced."
        except Exception as exc:
            self.db.execute("UPDATE feeds SET failures=1, last_error=? WHERE id=?", (str(exc)[:150], fid))
            note = f"⚠️ First check failed: {str(exc)[:150]}. I'll keep retrying."
        if not self.bot.registry.get(ctx.guild.id, "feeds").get("enabled"):
            self.bot.registry.set(ctx.guild, ctx.author, "feeds", "enabled", True)
        await ui.reply(ctx, embed=ui.ok(f"Feed #{fid} added. {note}"))

    @feeds.command(name="remove")
    async def feeds_remove(self, ctx, fid: int):
        n = self.db.execute("DELETE FROM feeds WHERE guild_id=? AND id=?", (ctx.guild.id, fid)).rowcount
        await ui.reply(ctx, embed=ui.ok("Removed.") if n else ui.err("No such feed."))

    @feeds.command(name="ping")
    async def feeds_ping(self, ctx, fid: int, role: discord.Role = None):
        n = self.db.execute("UPDATE feeds SET ping_role=? WHERE guild_id=? AND id=?", (role.id if role else None, ctx.guild.id, fid)).rowcount
        await ui.reply(ctx, embed=ui.ok("Updated.") if n else ui.err("No such feed."))

    @feeds.command(name="template")
    async def feeds_template(self, ctx, fid: int, *, template: str = ""):
        n = self.db.execute("UPDATE feeds SET template=? WHERE guild_id=? AND id=?", (template[:500] or None, ctx.guild.id, fid)).rowcount
        await ui.reply(ctx, embed=ui.ok("Template updated." if template else "Template reset.") if n else ui.err("No such feed."))

    @feeds.command(name="health")
    async def feeds_health(self, ctx):
        rows = self.db.fetchall("SELECT * FROM feeds WHERE guild_id=?", (ctx.guild.id,))
        lines = []
        for r in rows:
            state = "✅ healthy" if not r["failures"] else f"⚠️ {r['failures']} failure(s): {r['last_error']}"
            last = f"last OK <t:{int(r['last_ok_ts'])}:R>" if r["last_ok_ts"] else "never checked OK"
            lines.append(f"`#{r['id']}` {r['kind']} {r['label'] or r['source']} · {state} · {last}")
        await ui.reply(ctx, embed=ui.embed("📡 Feed health", "\n".join(lines) or "No feeds."))


async def setup(bot):
    await bot.add_cog(Announce(bot))
