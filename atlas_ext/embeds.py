"""Admin-only embed builder with modals, live preview, validation and a publish confirmation.
Posts as ATLAS itself. No webhooks are created or used.
"""
import re

import discord
from discord.ext import commands

from common import checks, ui

URL_RE = re.compile(r"^https://\S+$")
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def embed_length(e):
    total = len(e.title or "") + len(e.description or "") + len(e.footer.text or "") + len(e.author.name or "")
    return total + sum(len(f.name) + len(f.value) for f in e.fields)


class TextModal(discord.ui.Modal):
    def __init__(self, builder, title, fields):
        super().__init__(title=title)
        self.builder, self.inputs = builder, {}
        for key, label, default, style, max_len, required in fields:
            ti = discord.ui.TextInput(label=label, default=default or None, style=style, max_length=max_len, required=required)
            self.inputs[key] = ti
            self.add_item(ti)

    async def on_submit(self, interaction):
        values = {k: str(v.value or "").strip() for k, v in self.inputs.items()}
        error = self.builder.apply(values)
        if error:
            return await interaction.response.send_message(f"⚠️ {error}", ephemeral=True)
        await interaction.response.edit_message(content=self.builder.header(), embed=self.builder.embed, view=self.builder)


class Builder(discord.ui.View):
    def __init__(self, author_id, channel):
        super().__init__(timeout=900)
        self.author_id, self.target, self.mode = author_id, channel, None
        self.embed = discord.Embed(title="New announcement", description="Use the buttons to edit me.", colour=ui.PINK)
        self.edit_message = None
        select = discord.ui.ChannelSelect(channel_types=[discord.ChannelType.text, discord.ChannelType.news], placeholder="Channel to post in…", row=3)
        select.callback = self.pick_channel
        self.channel_select = select
        self.add_item(select)

    def header(self):
        where = f"editing [this message]({self.edit_message.jump_url})" if self.edit_message else f"posting in {self.target.mention}"
        return f"🛠️ **Embed builder** — {where} · {embed_length(self.embed)}/6000 characters · {len(self.embed.fields)}/25 fields"

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id or not checks.is_admin(interaction.user):
            await interaction.response.send_message("Only the admin who opened this builder can use it.", ephemeral=True)
            return False
        return True

    def apply(self, values):
        e = self.embed.copy()
        mode = self.mode
        if mode == "text":
            e.title = values["title"] or None
            e.description = values["description"] or None
        elif mode == "colour":
            m = HEX_RE.match(values["colour"])
            if not m:
                return "Use a hex colour like #FF7EB6."
            e.colour = discord.Colour(int(m.group(1), 16))
        elif mode == "images":
            for key in ("thumbnail", "image"):
                if values[key] and not URL_RE.match(values[key]):
                    return f"The {key} must be an https:// link."
            e.set_thumbnail(url=values["thumbnail"] or None)
            e.set_image(url=values["image"] or None)
        elif mode == "footer":
            e.set_footer(text=values["footer"] or None)
            if values["author"]:
                e.set_author(name=values["author"])
            else:
                e.remove_author()
        elif mode == "field":
            if len(e.fields) >= 25:
                return "Embeds can have at most 25 fields."
            e.add_field(name=values["name"], value=values["value"], inline=values["inline"].casefold() in ("y", "yes", "true", "1"))
        if embed_length(e) > 6000:
            return "That would exceed Discord's 6000-character embed limit."
        if not (e.title or e.description or e.fields or e.image.url):
            return "The embed needs at least a title, description, field or image."
        self.embed = e
        return None

    async def open(self, interaction, mode):
        self.mode = mode
        e = self.embed
        P, S = discord.TextStyle.paragraph, discord.TextStyle.short
        forms = {
            "text": ("Title & description", [("title", "Title", e.title, S, 256, False), ("description", "Description", e.description, P, 4000, False)]),
            "colour": ("Colour", [("colour", "Hex colour", f"#{e.colour.value:06X}" if e.colour else "#FF7EB6", S, 7, True)]),
            "images": ("Images", [("thumbnail", "Thumbnail URL (https)", e.thumbnail.url, S, 500, False), ("image", "Large image URL (https)", e.image.url, S, 500, False)]),
            "footer": ("Footer & author", [("footer", "Footer text", e.footer.text, S, 2048, False), ("author", "Author line", e.author.name, S, 256, False)]),
            "field": ("Add field", [("name", "Field name", "", S, 256, True), ("value", "Field value", "", P, 1024, True), ("inline", "Inline? (yes/no)", "no", S, 3, True)]),
        }
        title, fields = forms[mode]
        await interaction.response.send_modal(TextModal(self, title, fields))

    @discord.ui.button(label="Title/Text", emoji="📝", row=0)
    async def b_text(self, interaction, button):
        await self.open(interaction, "text")

    @discord.ui.button(label="Colour", emoji="🎨", row=0)
    async def b_colour(self, interaction, button):
        await self.open(interaction, "colour")

    @discord.ui.button(label="Images", emoji="🖼️", row=0)
    async def b_images(self, interaction, button):
        await self.open(interaction, "images")

    @discord.ui.button(label="Footer/Author", emoji="🔖", row=0)
    async def b_footer(self, interaction, button):
        await self.open(interaction, "footer")

    @discord.ui.button(label="Add field", emoji="➕", row=1)
    async def b_field(self, interaction, button):
        await self.open(interaction, "field")

    @discord.ui.button(label="Remove last field", emoji="➖", row=1)
    async def b_unfield(self, interaction, button):
        if self.embed.fields:
            self.embed.remove_field(len(self.embed.fields) - 1)
        await interaction.response.edit_message(content=self.header(), embed=self.embed, view=self)

    @discord.ui.button(label="Timestamp", emoji="🕒", row=1)
    async def b_time(self, interaction, button):
        self.embed.timestamp = None if self.embed.timestamp else discord.utils.utcnow()
        await interaction.response.edit_message(content=self.header(), embed=self.embed, view=self)

    @discord.ui.button(label="Publish", emoji="📤", style=discord.ButtonStyle.success, row=2)
    async def b_send(self, interaction, button):
        perms = self.target.permissions_for(interaction.guild.me) if not self.edit_message else None
        if perms is not None and not (perms.send_messages and perms.embed_links):
            return await interaction.response.send_message(f"I can't post embeds in {self.target.mention}.", ephemeral=True)
        confirm = ui.ConfirmView(self.author_id, confirm_label="Yes, publish", danger=False)
        await interaction.response.send_message("Publish this embed exactly as previewed?", view=confirm, ephemeral=True)
        await confirm.wait()
        if not confirm.value:
            return
        try:
            if self.edit_message:
                await self.edit_message.edit(embed=self.embed)
                done = f"Updated [the message]({self.edit_message.jump_url})."
            else:
                msg = await self.target.send(embed=self.embed, allowed_mentions=discord.AllowedMentions.none())
                done = f"Published in {self.target.mention} ([jump]({msg.jump_url}))."
        except discord.HTTPException as exc:
            return await interaction.followup.send(f"Discord rejected the embed: {exc}", ephemeral=True)
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.followup.send(done, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=2)
    async def b_cancel(self, interaction, button):
        self.stop()
        await interaction.response.edit_message(content="Builder closed. Nothing was posted.", embed=None, view=None)

    async def pick_channel(self, interaction):
        channel = interaction.guild.get_channel(self.channel_select.values[0].id)
        if channel is not None:
            self.target = channel
            self.edit_message = None
        await interaction.response.edit_message(content=self.header(), embed=self.embed, view=self)


class Embeds(commands.Cog, name="Embeds"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="embed", aliases=["embedbuilder"])
    @commands.guild_only()
    @checks.admin_only()
    async def embed(self, ctx, channel: discord.TextChannel = None):
        """Open the embed builder (Manage Server)."""
        builder = Builder(ctx.author.id, channel or ctx.channel)
        await ctx.reply(builder.header(), embed=builder.embed, view=builder, mention_author=False)

    @commands.command(name="embededit")
    @commands.guild_only()
    @checks.admin_only()
    async def embededit(self, ctx, link: str):
        """Edit an embed ATLAS posted earlier (give its message link)."""
        util = self.bot.get_cog("Utility")
        message = await util.resolve_link(ctx, link) if util else None
        if message is None or message.author.id != self.bot.user.id or not message.embeds:
            return await ui.reply(ctx, embed=ui.err("Give a link to an embed message that ATLAS posted."))
        builder = Builder(ctx.author.id, message.channel)
        builder.embed = message.embeds[0].copy()
        builder.edit_message = message
        await ctx.reply(builder.header(), embed=builder.embed, view=builder, mention_author=False)


async def setup(bot):
    await bot.add_cog(Embeds(bot))
