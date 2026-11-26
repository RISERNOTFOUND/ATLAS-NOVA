"""Member and server utilities: rich serverinfo/userinfo/avatar (same command names as before),
banner, profile cards, role list, emoji tools, quote, fun facts and command search."""
import re
import secrets
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from common import ui

from .game_data import FUN_FACTS

LINK_RE = re.compile(r"https?://(?:\w+\.)?discord(?:app)?\.com/channels/(\d+|@me)/(\d+)/(\d+)")


def duration_text(since):
    days = (datetime.now(timezone.utc) - since).days
    years, days = divmod(days, 365)
    months, days = divmod(days, 30)
    parts = [f"{n} {w}{'s' if n != 1 else ''}" for n, w in ((years, "year"), (months, "month"), (days, "day")) if n]
    return ", ".join(parts) or "today"


class Utility(commands.Cog, name="Utility"):
    def __init__(self, bot):
        self.bot = bot

    async def resolve_link(self, ctx, link):
        """Open a message link the invoking member is allowed to read. Returns Message or None."""
        m = LINK_RE.search(link or "")
        if not m or ctx.guild is None or m.group(1) != str(ctx.guild.id):
            return None
        channel = ctx.guild.get_channel_or_thread(int(m.group(2)))
        if channel is None:
            return None
        author = ctx.author if isinstance(ctx.author, discord.Member) else ctx.guild.get_member(ctx.author.id)
        if author is None or not channel.permissions_for(author).read_message_history or not channel.permissions_for(author).view_channel:
            return None
        try:
            return await channel.fetch_message(int(m.group(3)))
        except discord.HTTPException:
            return None

    # ---------------------------------------------------------------- server
    @commands.hybrid_command(name="serverinfo", description="Display information about this server")
    @commands.guild_only()
    async def serverinfo(self, ctx):
        g = ctx.guild
        humans = sum(1 for m in g.members if not m.bot)
        bots = sum(1 for m in g.members if m.bot)
        e = discord.Embed(title=g.name, colour=ui.PINK, description=g.description or None)
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        if g.banner:
            e.set_image(url=g.banner.url)
        e.add_field(name="Owner", value=f"<@{g.owner_id}>")
        e.add_field(name="Created", value=f"{discord.utils.format_dt(g.created_at, 'D')}\n({duration_text(g.created_at)} ago)")
        e.add_field(name="Members", value=f"{g.member_count:,} total" + (f"\n{humans:,} humans · {bots} bots" if g.members else ""))
        e.add_field(name="Channels", value=f"💬 {len(g.text_channels)} · 🔊 {len(g.voice_channels)} · 📁 {len(g.categories)}" + (f" · 🗂️ {len(g.forums)}" if g.forums else "") + (f" · 🎤 {len(g.stage_channels)}" if g.stage_channels else ""))
        e.add_field(name="Roles", value=str(len(g.roles) - 1))
        e.add_field(name="Boosts", value=f"Level {g.premium_tier} · {g.premium_subscription_count or 0} boosts")
        e.add_field(name="Emojis / stickers", value=f"{len(g.emojis)}/{g.emoji_limit} · {len(g.stickers)}/{g.sticker_limit}")
        e.add_field(name="Verification level", value=str(g.verification_level).replace("_", " ").title())
        stats = self.bot.get_cog("StatsCog")
        if stats:
            totals = stats.store.all_totals(g.id, "7d")
            e.add_field(name="Activity (7 days)", value=f"{sum(t['m'] for t in totals.values()):,} messages · {len(totals)} active members")
        if g.features:
            e.add_field(name="Features", value=ui.clip(", ".join(f.replace("_", " ").title() for f in sorted(g.features)), 1000), inline=False)
        e.set_footer(text=f"Server ID {g.id}")
        await ctx.reply(embed=e, mention_author=False)

    # ---------------------------------------------------------------- members
    @commands.hybrid_command(name="userinfo", description="Display information about a server member")
    @commands.guild_only()
    @app_commands.describe(member="The member to view (defaults to yourself)")
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        e = discord.Embed(title=discord.utils.escape_markdown(member.display_name), colour=member.colour if member.colour.value else ui.PINK)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Username", value=f"`{member}`")
        e.add_field(name="ID", value=f"`{member.id}`")
        e.add_field(name="Bot", value="yes" if member.bot else "no")
        e.add_field(name="Account created", value=f"{discord.utils.format_dt(member.created_at, 'D')}\n({duration_text(member.created_at)} ago)")
        if member.joined_at:
            e.add_field(name="Joined server", value=f"{discord.utils.format_dt(member.joined_at, 'D')}\n(member for {duration_text(member.joined_at)})")
        if member.premium_since:
            e.add_field(name="Boosting since", value=discord.utils.format_dt(member.premium_since, "D"))
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        e.add_field(name=f"Roles ({len(roles)})", value=ui.clip(" ".join(roles) or "none", 1000), inline=False)
        if member.is_timed_out():
            e.add_field(name="Timed out until", value=discord.utils.format_dt(member.timed_out_until, "F"))
        await ctx.reply(embed=e, mention_author=False)

    @commands.hybrid_command(name="avatar", description="View a member's avatar")
    @app_commands.describe(member="The member to view (defaults to yourself)")
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        avatar = member.display_avatar
        e = discord.Embed(title=f"{member.display_name}'s avatar", colour=ui.PINK)
        e.set_image(url=avatar.replace(size=1024).url)
        formats = ["png", "jpg", "webp"] + (["gif"] if avatar.is_animated() else [])
        e.description = " • ".join(f"[{f.upper()}]({avatar.replace(size=1024, format=f).url})" for f in formats)
        if isinstance(member, discord.Member) and member.guild_avatar and member.avatar:
            e.add_field(name="Global avatar", value=f"[Open]({member.avatar.replace(size=1024).url})")
        await ctx.reply(embed=e, mention_author=False)

    @commands.hybrid_command(name="banner", description="View a member's profile banner")
    @app_commands.describe(member="The member to view (defaults to yourself)")
    async def banner(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)  # banners are only available on a fetched user
        if user.banner is None:
            colour = f" Their accent colour is `{user.accent_colour}`." if user.accent_colour else ""
            return await ui.reply(ctx, embed=ui.embed(description=f"{member.display_name} has no profile banner.{colour}"), ephemeral=False)
        e = discord.Embed(title=f"{member.display_name}'s banner", colour=user.accent_colour or ui.PINK)
        e.set_image(url=user.banner.replace(size=1024).url)
        await ctx.reply(embed=e, mention_author=False)

    @commands.hybrid_command(name="profile", description="A member's profile card")
    @commands.guild_only()
    @app_commands.describe(member="The member to view (defaults to yourself)")
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        e = discord.Embed(title=f"🪪 {discord.utils.escape_markdown(member.display_name)}", colour=member.colour if member.colour.value else ui.PINK)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Member for", value=duration_text(member.joined_at) if member.joined_at else "?")
        e.add_field(name="Account age", value=duration_text(member.created_at))
        position = sorted((m for m in ctx.guild.members if m.joined_at), key=lambda m: m.joined_at).index(member) + 1 if member.joined_at and ctx.guild.members else None
        if position:
            e.add_field(name="Join position", value=f"#{position:,}")
        stats = self.bot.get_cog("StatsCog")
        if stats:
            t30 = stats.store.user_total(ctx.guild.id, member.id, "30d")
            from atlas_stats_core import fmt_dur
            e.add_field(name="Last 30 days", value=f"💬 {t30['m']:,} messages · 🎙️ {fmt_dur(t30['v'])} in voice", inline=False)
        top = [r.mention for r in sorted(member.roles, key=lambda r: -r.position) if not r.is_default()][:5]
        e.add_field(name="Top roles", value=" ".join(top) or "none", inline=False)
        try:
            recs = self.bot.db.scalar("SELECT count(*) FROM recognitions WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id), 0)
            wins = self.bot.db.scalar("SELECT sum(wins) FROM game_scores WHERE guild_id=? AND user_id=?", (ctx.guild.id, member.id), 0)
            e.add_field(name="Community", value=f"🏅 recognised {recs}× · 🎮 {wins or 0} game wins")
        except Exception:
            pass
        await ctx.reply(embed=e, mention_author=False)

    @commands.command(name="roleslist", aliases=["myroles"])
    @commands.guild_only()
    async def roleslist(self, ctx, member: discord.Member = None):
        """List a member's roles (highest first)."""
        member = member or ctx.author
        roles = [f"{r.mention} `{r.id}`" for r in sorted(member.roles, key=lambda r: -r.position) if not r.is_default()]
        await ui.paginate(ctx, ui.pages_from_lines(f"🎭 Roles of {member.display_name} ({len(roles)})", roles, per_page=20))

    # ---------------------------------------------------------------- emojis
    @commands.command(name="emoji", aliases=["enlarge", "bigemoji"])
    async def emoji(self, ctx, emoji: str):
        """Show a custom emoji large with its info."""
        partial = discord.PartialEmoji.from_str(emoji)
        if partial.id is None:
            code = " ".join(f"U+{ord(c):04X}" for c in emoji)
            return await ui.reply(ctx, embed=ui.embed(description=f"{emoji} is a standard emoji ({code})."), ephemeral=False)
        e = discord.Embed(title=f":{partial.name}:", colour=ui.PINK)
        e.set_image(url=partial.url)
        e.add_field(name="ID", value=f"`{partial.id}`")
        e.add_field(name="Animated", value="yes" if partial.animated else "no")
        own = ctx.guild.get_emoji(partial.id) if ctx.guild else None
        e.add_field(name="From this server", value="yes" if own else "no")
        if own and own.created_at:
            e.add_field(name="Added", value=discord.utils.format_dt(own.created_at, "D"))
        await ctx.reply(embed=e, mention_author=False)

    @commands.command(name="emojis", aliases=["emojilist"])
    @commands.guild_only()
    async def emojis(self, ctx):
        """List this server's custom emojis."""
        static = [str(e) for e in ctx.guild.emojis if not e.animated]
        animated = [str(e) for e in ctx.guild.emojis if e.animated]
        lines = [" ".join(static[i:i + 15]) for i in range(0, len(static), 15)] + (["**Animated:**"] if animated else []) + [" ".join(animated[i:i + 15]) for i in range(0, len(animated), 15)]
        await ui.paginate(ctx, ui.pages_from_lines(f"😀 Emojis ({len(ctx.guild.emojis)}/{ctx.guild.emoji_limit})", lines, per_page=10))

    # ---------------------------------------------------------------- messages
    @commands.command(name="quote")
    @commands.guild_only()
    async def quote(self, ctx, link: str):
        """Quote a message by its link (only from channels you can read)."""
        message = await self.resolve_link(ctx, link)
        if message is None:
            return await ui.reply(ctx, embed=ui.err("I couldn't open that message. Use a message link from this server that you can read."))
        if getattr(message.channel, "nsfw", False) and not getattr(ctx.channel, "nsfw", False):
            return await ui.reply(ctx, embed=ui.err("That message is in an age-restricted channel and can't be quoted here."))
        e = discord.Embed(description=ui.clip(message.content, 3500) or "*attachment/embed*", colour=ui.PINK, timestamp=message.created_at)
        e.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        e.add_field(name="Source", value=f"[Jump]({message.jump_url}) · {message.channel.mention}")
        image = next((a for a in message.attachments if (a.content_type or "").startswith("image")), None)
        if image:
            e.set_image(url=image.url)
        await ctx.send(embed=e)

    @commands.command(name="funfact", aliases=["fact"])
    @commands.cooldown(1, 5, commands.BucketType.channel)
    async def funfact(self, ctx):
        await ui.reply(ctx, embed=discord.Embed(description=f"💡 {secrets.choice(FUN_FACTS)}", colour=ui.PINK), ephemeral=False)

    @commands.command(name="commandsearch", aliases=["findcommand", "searchcmd"])
    async def commandsearch(self, ctx, *, word: str):
        """Search ATLAS's commands by name or description."""
        word = word.casefold()
        hits = []
        for c in self.bot.walk_commands():
            text = f"{c.qualified_name} {' '.join(c.aliases)} {c.help or ''} {c.description or ''}".casefold()
            if word in text and not c.hidden:
                hits.append(f"`{ctx.prefix}{c.qualified_name}` — {ui.clip((c.help or c.description or '').splitlines()[0] if (c.help or c.description) else '', 80)}")
        for c in self.bot.tree.walk_commands():
            if word in f"{c.qualified_name} {getattr(c, 'description', '')}".casefold():
                hits.append(f"`/{c.qualified_name}` — {ui.clip(getattr(c, 'description', ''), 80)}")
        await ui.paginate(ctx, ui.pages_from_lines(f"🔎 Commands matching “{word}”", sorted(set(hits)) or ["No matches. Try `!help`."], per_page=15))


async def setup(bot):
    await bot.add_cog(Utility(bot))
