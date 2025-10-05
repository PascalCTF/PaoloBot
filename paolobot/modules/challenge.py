import re
import tempfile

from pathlib import Path

from discord import app_commands, ui
import discord
from mongoengine import NotUniqueError
import matplotlib.pyplot as plt
from matplotlib.table import Table, Cell

from paolobot.models.ctf_category import CtfCategory
from paolobot.utils import (
    move_channel,
    is_team_admin,
    get_incomplete_category,
    create_channel,
    get_complete_category,
    get_admin_role,
    sanitize_channel_name,
    get_settings,
    MAX_CHANNELS
)
from paolobot.modules.ctf import get_ctf_db

from paolobot.models.challenge import Challenge
from paolobot.models.ctf import Ctf


async def check_challenge(
    interaction: discord.Interaction
) -> tuple[Challenge | None, Ctf | None]:
    chall_db: Challenge = Challenge.objects(channel_id=interaction.channel_id).first()
    if chall_db is None:
        raise app_commands.AppCommandError("Not a challenge!")
    ctf_db: Ctf = chall_db.ctf
    if ctf_db.archived:
        raise app_commands.AppCommandError("This CTF is archived!")
    return chall_db, ctf_db


async def category_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    current = sanitize_channel_name(current)
    query = CtfCategory.objects(
        name=re.compile("^" + re.escape(current)),
        guild_id=interaction.guild_id
    ).order_by("-count")[:25]
    return [app_commands.Choice(name=c["name"], value=c["name"]) for c in query]


async def category_autocomplete_nullable(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    out = await category_autocomplete(interaction, current)
    if len(out) < 25 and "none".startswith(current):
        out.append(app_commands.Choice(name="None", value=""))
    return out


def category_is_valid(category: str, guild_id: int) -> bool:
    return category is None or CtfCategory.objects(name=category, guild_id=guild_id)


def get_work_embeds(chall_db: Challenge):
    embeds = []
    for work_value in WORK_VALUES[1:]:
        work_list = chall_db.working.filter(value=work_value.value)
        if work_list:
            embeds.append(discord.Embed(color=work_value.color).add_field(
                name=work_value.name,
                value=", ".join(f"<@!{work.user}>" for work in work_list)
            ))
    return embeds


async def update_work_message(chall_db: Challenge, channel: discord.PartialMessageable | None):
    if channel:
        message = channel.get_partial_message(chall_db.work_message)
        try:
            await message.edit(embeds=get_work_embeds(chall_db))
        except discord.HTTPException:
            pass


async def set_work(guild: discord.Guild, chall_db: Challenge, user: discord.User, value: int):
    if value == 0:
        chall_db.working.filter(user=user.id).delete()
    else:
        work = chall_db.working.filter(user=user.id).first()
        if work is None:
            chall_db.working.create(user=user.id, value=value)
        elif work.value != value:
            work.value = value
        else:
            return
    chall_db.save()
    channel = guild.get_channel(chall_db.channel_id)
    await update_work_message(chall_db, channel)


async def move_work(guild: discord.Guild, ctf_db: Ctf, chall_db: Challenge, user: discord.User):
    for chall in Challenge.objects(ctf=ctf_db):
        if chall.id == chall_db.id:
            continue
        work = chall.working.filter(user=user.id).first()
        if work is not None and work.value == 1:
            await set_work(guild, chall, user, 2)
    await set_work(guild, chall_db, user, 1)


class WorkView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(
        label="Set Working",
        emoji="🛠️",
        style=discord.ButtonStyle.success,
        custom_id="work_view:set_working"
    )
    async def set_working(self, interaction: discord.Interaction, _button: ui.Button):
        chall_db, ctf_db = await check_challenge(interaction)
        await move_work(interaction.guild, ctf_db, chall_db, interaction.user)
        await interaction.response.defer()


@app_commands.command(description="Add a challenge")
@app_commands.autocomplete(category=category_autocomplete_nullable)
@app_commands.guild_only
async def add(interaction: discord.Interaction, category: str, name: str):
    ctf_db = await get_ctf_db(interaction)

    if len(interaction.guild.channels) >= MAX_CHANNELS - 3:
        admin_role = get_admin_role(interaction.guild)
        await interaction.response.send_message(
            "There are too many channels on this discord server. "
            f"Please wait for an admin to delete some channels. {admin_role.mention}",
            allowed_mentions=discord.AllowedMentions.all()
        )
        return

    incomplete_category = get_incomplete_category(interaction.guild)

    # Check category is valid
    category = sanitize_channel_name(category) if category else None
    settings = get_settings(interaction.guild)
    if settings.enforce_categories and not category_is_valid(category, interaction.guild_id):
        raise app_commands.AppCommandError("Invalid CTF category")

    ctf = sanitize_channel_name(ctf_db.name)

    class ChallengeInfoModal(ui.Modal, title="Add Challenge"):
        name_field = ui.TextInput(
            label="Name",
            default=name,
            placeholder="Challenge name",
        )
        category_field = ui.TextInput(
            label="Category",
            default=category,
            placeholder="Challenge category",
            required=False,
        )
        description_field = ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            placeholder="Challenge description",
            max_length=1000,
        )

        async def on_submit(self, submit_interaction: discord.Interaction):
            name = sanitize_channel_name(self.name_field.value)
            if self.category_field.value:
                category = sanitize_channel_name(self.category_field.value)
                valid_category = category_is_valid(category, interaction.guild_id)
                if settings.enforce_categories and not valid_category:
                    await submit_interaction.response.send_message(
                        "Invalid CTF category",
                        ephemeral=True
                    )
                    return
                channel_name = f"{ctf}-{category}-{name}"[:100]
            else:
                category = None
                channel_name = f"{ctf}-{name}"[:100]

            if old_chall := Challenge.objects(name=name, category=category, ctf=ctf_db).first():
                if interaction.guild.get_channel(old_chall.channel_id):
                    await submit_interaction.response.send_message(
                        "A challenge with that name already exists",
                        ephemeral=True
                    )
                    return
                old_chall.delete()

            new_channel = await create_channel(
                channel_name,
                interaction.channel.overwrites,
                incomplete_category
            )

            chall_title = discord.utils.escape_mentions(self.name_field.value)
            chall_description = discord.utils.escape_mentions(self.description_field.value)

            await new_channel.send(f"# {chall_title}\n\n{chall_description}")

            work_message_id = None
            if settings.send_work_message:
                work_message = await new_channel.send(view=WorkView())
                await work_message.pin()
                work_message_id = work_message.id

            chall_db = Challenge(
                name=name,
                category=category,
                channel_id=new_channel.id,
                ctf=ctf_db,
                work_message=work_message_id
            )
            chall_db.save()

            if category:
                ctf_category = CtfCategory.objects(
                    name=category,
                    guild_id=interaction.guild_id
                ).first()
                if ctf_category is None:
                    ctf_category = CtfCategory(
                        name=category,
                        guild_id=interaction.guild_id,
                        count=0
                    )
                ctf_category.count += 1
                ctf_category.save()

            await submit_interaction.response.send_message(
                f"Added challenge {new_channel.mention}"
            )

    info = ChallengeInfoModal()
    await interaction.response.send_modal(info)


@app_commands.command(description="Marks a challenge as done")
@app_commands.guild_only
async def done(interaction: discord.Interaction, contributors: str | None):
    chall_db, ctf_db = await check_challenge(interaction)
    assert isinstance(interaction.channel, discord.TextChannel)

    users = chall_db.solvers
    if interaction.user.id not in users:
        users.append(interaction.user.id)

    if contributors is not None:
        for user in [int(i) for i in re.findall(r"<@!?(\d+)>", contributors)]:
            if user not in users:
                users.append(user)

    chall_db.solvers = users
    chall_db.solved = True
    chall_db.save()

    await move_channel(interaction.channel, get_complete_category(interaction.guild))

    # Special emojis for certain users, otherwise default
    msg_emojis = ("🎉", "🎉")
    reaction_emoji = "paolo"
    
    left_emoji, right_emoji = msg_emojis

    solvers = " ".join(f"<@!{user}>" for user in users)
    msg = f"{left_emoji}  {interaction.channel.mention} was solved by {solvers}!  {right_emoji}"
    sent_msg = await interaction.guild.get_channel(ctf_db.channel_id).send(msg)

    # Pre-react to solver message
    emoji = discord.utils.get(interaction.guild.emojis, name=reaction_emoji)
    await sent_msg.add_reaction(emoji)

    await interaction.response.send_message("Challenge moved to done!")


@app_commands.command(description="Marks a challenge as undone")
@app_commands.guild_only
async def undone(interaction: discord.Interaction):
    chall_db, _ = await check_challenge(interaction)
    assert isinstance(interaction.channel, discord.TextChannel)

    if not chall_db.solved:
        raise app_commands.AppCommandError("This challenge is not done yet!")

    chall_db.solvers = []
    chall_db.solved = False
    chall_db.save()

    await move_channel(interaction.channel, get_incomplete_category(interaction.guild))
    await interaction.response.send_message("Reopened challenge as not done")


class CategoryCommands(app_commands.Group):

    @app_commands.command(description="Create CTF category suggestion")
    @app_commands.guild_only
    async def create(self, interaction: discord.Interaction, category: str):
        category = sanitize_channel_name(category)
        try:
            ctf_category = CtfCategory(name=category, guild_id=interaction.guild_id, count=5)
            ctf_category.save()
        except NotUniqueError:
            await interaction.response.send_message("CTF category already exists", ephemeral=True)
        else:
            await interaction.response.send_message("Created CTF category", ephemeral=True)

    @app_commands.command(description="Delete CTF category suggestion")
    @app_commands.autocomplete(category=category_autocomplete)
    @app_commands.guild_only
    @app_commands.check(is_team_admin)
    async def delete(self, interaction: discord.Interaction, category: str):
        ctf_category: CtfCategory = CtfCategory.objects(
            name=category,
            guild_id=interaction.guild_id
        ).first()
        if ctf_category is None:
            await interaction.response.send_message("Unknown CTF category", ephemeral=True)
        else:
            ctf_category.delete()
            await interaction.response.send_message("Deleted CTF category", ephemeral=True)


class WorkValue:
    def __init__(self, value: int, color: int, name: str):
        self.value = value
        self.color = color
        self.name = name

    def hex_color(self):
        return f"#{self.color:06x}"

    def __str__(self):
        return self.name


WORK_VALUES = [WorkValue(0, 0xffffff, "None"),
               WorkValue(1, 0x00b618, "Working"),
               WorkValue(2, 0xffab00, "Has Worked")]
CELL_HEIGHT = 35 / 77
CELL_WIDTH = 100 / 77
MAX_TABLE_USERS = 20

def export_table(solves: dict[discord.Member, list[int]], challs: list[str], filename: str):
    has_names = len(solves) <= MAX_TABLE_USERS
    height = len(challs)
    width = len(solves)

    fig, ax = plt.subplots(
        figsize=(
            width * (CELL_WIDTH if has_names else CELL_HEIGHT),
            height * CELL_HEIGHT
        )
    )
    ax.axis("off")
    tbl = Table(ax, loc="center")

    def add_cell(r, c, text=None, color="w", loc="center", edges="closed"):
        tbl[r, c] = Cell(
            (r, c),
            text=text,
            facecolor=color,
            edgecolor=color,
            width=1 / width,
            height=1 / height,
            loc=loc,
            visible_edges=edges
        )

    for row, name in enumerate(challs):
        add_cell(row + 1, 0, text=name, loc="left")

    for col, user in enumerate(solves.keys()):
        nm = user.nick if hasattr(user, "nick") and user.nick else user.name
        add_cell(0, col + 1, text=nm if has_names else None, edges="B", color="black")
        if has_names:
            tbl[0, col + 1].auto_set_font_size(fig.canvas.get_renderer())
        for row, val in enumerate(solves[user]):
            color = WORK_VALUES[val].hex_color() if 0 <= val < len(WORK_VALUES) else "w"
            add_cell(row + 1, col + 1, color=color)
    tbl.auto_set_column_width(0)
    tbl.auto_set_font_size(False)
    ax.add_table(tbl)
    plt.savefig(filename, bbox_inches="tight", pad_inches=0)


@app_commands.command(description="Shortcut to set working status on the challenge")
@app_commands.guild_only
async def w(interaction: discord.Interaction):
    chall_db, ctf_db = await check_challenge(interaction)
    assert isinstance(interaction.channel, discord.TextChannel)
    await move_work(interaction.guild, ctf_db, chall_db, interaction.user)
    await interaction.response.send_message("Updated working status to Working", ephemeral=True)


class WorkingCommands(app_commands.Group):
    @app_commands.command(description="Set working status on the challenge")
    @app_commands.choices(
        value=[app_commands.Choice(name=w.name, value=w.value) for w in WORK_VALUES]
    )
    @app_commands.guild_only
    async def set(self, interaction: discord.Interaction, value: int, user: discord.Member | None):
        chall_db, _ = await check_challenge(interaction)
        await set_work(interaction.guild, chall_db, user or interaction.user, value)
        await interaction.response.send_message(
            f"Updated working status to {WORK_VALUES[value]}",
            ephemeral=True
        )

    @app_commands.command(description="Get list of people working on the challenge")
    @app_commands.guild_only
    async def get(self, interaction: discord.Interaction):
        chall_db, _ = await check_challenge(interaction)
        embeds = get_work_embeds(chall_db)
        await interaction.response.send_message(
            "" if embeds else "Nobody is working on this",
            embeds=embeds,
            view=WorkView(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(description="Get table of all work on challenges")
    @app_commands.guild_only
    async def table(self, interaction: discord.Interaction, include_solved: bool = False):
        ctf_db = await get_ctf_db(interaction, archived=None)
        assert isinstance(interaction.channel, discord.TextChannel)

        await interaction.response.defer(ephemeral=True)
        if include_solved:
            challs = Challenge.objects(ctf=ctf_db)
        else:
            challs = Challenge.objects(ctf=ctf_db, solved=False)
        sorted_challs = sorted(challs, key=lambda x: (x.category or "", x.name))

        # Filter out deleted challs
        challs = []
        for chall in sorted_challs:
            if interaction.guild.get_channel(chall.channel_id):
                challs.append(chall)
            else:
                chall.delete()

        # Create table of users who have done work
        tbl = {}
        for i, chall in enumerate(challs):
            for work in chall.working:
                user = interaction.guild.get_member(work.user)
                if user not in tbl:
                    tbl[user] = [0] * len(challs)
                tbl[user][i] = work.value

        if not tbl:
            await interaction.edit_original_response(
                content="No work has been done on any challenges yet"
            )
            return

        with tempfile.TemporaryDirectory() as tmp:
            filename = Path(tmp) / "overview.png"
            export_table(
                tbl,
                [
                    (chall.category + "-" if chall.category else "") + chall.name
                    for chall in challs
                ],
                filename
            )
            await interaction.edit_original_response(attachments=[discord.File(filename)])


def add_commands(tree: app_commands.CommandTree, guild: discord.Object | None):
    tree.add_command(add, guild=guild)
    tree.add_command(done, guild=guild)
    tree.add_command(undone, guild=guild)
    tree.add_command(w, guild=guild)
    tree.add_command(CategoryCommands(name="category"), guild=guild)
    tree.add_command(WorkingCommands(name="working"), guild=guild)
