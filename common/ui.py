"""Consistent Discord-native UI pieces: embeds, confirmation prompts, pagination."""
import asyncio

import discord

PINK = discord.Colour.from_rgb(255, 126, 182)
GOOD = discord.Colour.from_rgb(87, 199, 133)
WARN = discord.Colour.from_rgb(255, 190, 90)
BAD = discord.Colour.from_rgb(237, 66, 69)
INFO = discord.Colour.from_rgb(92, 200, 255)


def embed(title=None, description=None, colour=INFO, **fields):
    e = discord.Embed(title=title, description=description, colour=colour)
    for name, value in fields.items():
        e.add_field(name=name.replace("_", " "), value=value, inline=False)
    return e


def ok(text):
    return discord.Embed(description=f"✅ {text}", colour=GOOD)


def err(text):
    return discord.Embed(description=f"⚠️ {text}", colour=BAD)


def clip(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def chunk_lines(lines, limit=1000):
    """Split lines into strings no longer than limit (for embed fields)."""
    out, cur = [], ""
    for line in lines:
        line = clip(line, limit)
        if len(cur) + len(line) + 1 > limit:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        out.append(cur)
    return out


async def reply(ctx_or_inter, content=None, *, embed=None, ephemeral=True, view=None, file=None):
    """Reply to a Context or Interaction uniformly. Returns the message when available."""
    kwargs = {"content": content, "embed": embed}
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    if isinstance(ctx_or_inter, discord.Interaction):
        inter = ctx_or_inter
        if inter.response.is_done():
            return await inter.followup.send(ephemeral=ephemeral, wait=True, **kwargs)
        await inter.response.send_message(ephemeral=ephemeral, **kwargs)
        return await inter.original_response()
    ctx = ctx_or_inter
    if getattr(ctx, "interaction", None) is not None:
        return await ctx.send(ephemeral=ephemeral, **kwargs)
    return await ctx.reply(mention_author=False, allowed_mentions=discord.AllowedMentions.none(), **kwargs)


class ConfirmView(discord.ui.View):
    """Only `author_id` can press. value: True (confirmed), False (cancelled), None (timed out)."""

    def __init__(self, author_id, *, timeout=60, confirm_label="Confirm", danger=True):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value = None
        self.message = None
        self.confirm.label = confirm_label
        self.confirm.style = discord.ButtonStyle.danger if danger else discord.ButtonStyle.success

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This confirmation isn't yours.", ephemeral=True)
            return False
        return True

    def _disable(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        self.value = True
        self._disable()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.value = False
        self._disable()
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=self)
        self.stop()

    async def on_timeout(self):
        self._disable()
        if self.message:
            try:
                await self.message.edit(content="⏱️ Timed out. Nothing was changed.", view=self)
            except discord.HTTPException:
                pass


async def confirm(ctx_or_inter, text, *, author_id=None, timeout=60, confirm_label="Confirm", danger=True):
    """Show a confirmation prompt and wait. Returns True only when the author confirms."""
    author = author_id or (ctx_or_inter.user.id if isinstance(ctx_or_inter, discord.Interaction) else ctx_or_inter.author.id)
    view = ConfirmView(author, timeout=timeout, confirm_label=confirm_label, danger=danger)
    view.message = await reply(ctx_or_inter, embed=discord.Embed(description=text, colour=WARN), view=view)
    await view.wait()
    return bool(view.value)


class Paginator(discord.ui.View):
    def __init__(self, author_id, pages, *, timeout=180):
        super().__init__(timeout=timeout)
        self.author_id, self.pages, self.index, self.message = author_id, pages or [embed("Nothing to show")], 0, None
        self._sync()

    def _sync(self):
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1
        self.counter.label = f"{self.index + 1}/{len(self.pages)}"

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Run the command yourself to page through it.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction, button):
        self.index -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction, button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.index += 1
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def paginate(ctx_or_inter, pages, *, ephemeral=False):
    author = ctx_or_inter.user.id if isinstance(ctx_or_inter, discord.Interaction) else ctx_or_inter.author.id
    if len(pages) <= 1:
        return await reply(ctx_or_inter, embed=pages[0] if pages else embed("Nothing to show"), ephemeral=ephemeral)
    view = Paginator(author, pages)
    view.message = await reply(ctx_or_inter, embed=pages[0], view=view, ephemeral=ephemeral)
    return view.message


def pages_from_lines(title, lines, *, per_page=15, colour=INFO, footer=None):
    pages = []
    for start in range(0, max(1, len(lines)), per_page):
        e = discord.Embed(title=title, description="\n".join(lines[start:start + per_page]) or "*Nothing here yet.*", colour=colour)
        if footer:
            e.set_footer(text=footer)
        pages.append(e)
    return pages


async def safe_send(channel, *args, **kwargs):
    if channel is None:
        return None
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
    try:
        return await channel.send(*args, **kwargs)
    except (discord.Forbidden, discord.HTTPException):
        return None


async def safe_dm(user, *args, **kwargs):
    try:
        return await user.send(*args, **kwargs)
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        return None


def jump(guild_id, channel_id, message_id):
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


async def sleep_rate():
    await asyncio.sleep(0.35)
