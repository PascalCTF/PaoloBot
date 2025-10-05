import re

import discord
from discord import app_commands

from paolobot.models.backup_category import BackupCategory
from paolobot.models.guild_settings import GuildSettings

MAX_CHANNELS = 500
CATEGORY_MAX_CHANNELS = 50


def get_category_pos(category_channel: discord.CategoryChannel, name: str) -> int:
    if name.count("-") == 1:
        ctf, category = name.split("-")[0], None
    else:
        ctf, category, _ = name.split("-")

    same_category_channel = None
    same_ctf_channel = None
    for channel in category_channel.text_channels:
        if channel.name.startswith(f"{ctf}-"):
            same_ctf_channel = channel
        if category is None:
            if channel.name.count("-") == 1:
                same_category_channel = channel
        elif channel.name.startswith(f"{ctf}-{category}-"):
            same_category_channel = channel

    if same_category_channel:
        return same_category_channel.position
    if same_ctf_channel:
        return same_ctf_channel.position + 1
    if category_channel.text_channels:
        ctf_pos = category_channel.text_channels[-1].position // 1000 + 1
        return ctf_pos * 1000
    return 0


async def get_backup_category(
        original_category: discord.CategoryChannel
    ) -> discord.CategoryChannel:
    last_backup = None
    for cat in BackupCategory.objects(original_id=original_category.id).order_by("index"):
        last_backup = cat
        category = original_category.guild.get_channel(cat["category_id"])
        if len(category.channels) < CATEGORY_MAX_CHANNELS:
            return category

    idx = 2 if not last_backup else last_backup["index"]+1
    new_category = await original_category.guild.create_category(
        f"{original_category.name} {idx}", position=original_category.position
    )
    backup_category = BackupCategory(
        original_id=original_category.id,
        category_id=new_category.id,
        index=idx
    )
    backup_category.save()
    return new_category


async def free_backup_category(category: discord.CategoryChannel):
    if len(category.channels) == 0:
        backup_category = BackupCategory.objects(category_id=category.id).first()
        if backup_category is not None:
            backup_category.delete()
            await category.delete(reason="Removing unused backup category")


async def delete_channel(channel: discord.TextChannel):
    original_category = channel.category
    await channel.delete(reason="Deleted CTF channels")
    await free_backup_category(original_category)


async def create_channel(
        name: str,
        overwrites: dict,
        category: discord.CategoryChannel,
        challenge=True
    ) -> discord.TextChannel:
    if len(category.channels) == CATEGORY_MAX_CHANNELS:
        category = await get_backup_category(category)

    if challenge:
        pos = get_category_pos(category, name)
        return await category.create_text_channel(name, overwrites=overwrites, position=pos)

    return await category.create_text_channel(name, overwrites=overwrites)


async def move_channel(
        channel: discord.TextChannel,
        goal_category: discord.CategoryChannel,
        challenge=True
    ):
    if goal_category == channel.category:
        return

    if len(goal_category.channels) == CATEGORY_MAX_CHANNELS:
        goal_category = await get_backup_category(goal_category)

    original_category = channel.category

    if challenge:
        pos = get_category_pos(goal_category, channel.name)
        await channel.edit(category=goal_category, position=pos)
    else:
        await channel.edit(category=goal_category)

    await free_backup_category(original_category)


async def is_team_admin(interaction: discord.Interaction) -> bool:
    if not get_admin_role(interaction.guild) in interaction.user.roles:
        raise app_commands.AppCommandError("Only team admins are allowed to run this command")
    return True


_channel_name_translation = {ord(i): "" for i in """!"#$%&'()*+,./:;<=>?@[\\]^`{|}~"""}
_channel_name_translation |= {ord(" "): "_", ord("-"): "_"}
def sanitize_channel_name(name: str) -> str:
    name = re.sub(r"<a?:.+?:\d+?>", "", name)  # Remove emojis
    name = name.translate(_channel_name_translation).lower()
    return re.sub(r"_+", "_", name).strip("_")


def _discord_get(
        guild: discord.Guild,
        value: int,
        id_type: str
    ) -> discord.Role | discord.abc.GuildChannel | None:
    if id_type == "role":
        return guild.get_role(value)

    if id_type in ("channel", "category"):
        return guild.get_channel(value)

    return None


def _discord_find(
        guild: discord.Guild,
        name: str,
        id_type: str
    ) -> discord.Role | discord.abc.GuildChannel | None:
    if id_type == "role":
        return discord.utils.get(guild.roles, name=name)
    if id_type == "channel":
        return discord.utils.get(guild.channels, name=name)
    if id_type == "category":
        return discord.utils.get(guild.categories, name=name)
    return None


def _discord_create(
        guild: discord.Guild,
        name: str,
        id_type: str
    ) -> discord.Role | discord.abc.GuildChannel | None:
    if id_type == "role":
        return guild.create_role(name=name)
    if id_type == "channel":
        return guild.create_text_channel(name=name)
    if id_type == "category":
        return guild.create_category_channel(name=name)
    return None


async def setup_settings(guild: discord.Guild):
    settings = GuildSettings.objects(guild_id=guild.id).first()
    if settings is None:
        settings = GuildSettings(guild_id=guild.id)

    discord_values = {
        "admin_role": "Team Admin",
        "team_role": "Team Member",
        "ctfs_category": "CTFS",
        "incomplete_category": "INCOMPLETE CHALLENGES",
        "complete_category": "COMPLETE CHALLENGES",
        "archive_category": "ARCHIVE",
        "ctf_archive_category": "ARCHIVED CTFS",
        "export_channel": "export",
        "invite_channel": "ctf-invites"
    }
    for key, name in discord_values.items():
        key_type = key.rsplit("_", 1)[-1]
        if getattr(settings, key) and _discord_get(guild, getattr(settings, key), key_type):
            continue

        existing = _discord_find(guild, name, key_type)
        if existing:
            setattr(settings, key, existing.id)
            continue

        new_id = (await _discord_create(guild, name, key_type)).id
        setattr(settings, key, new_id)
    settings.save()

    # Add guild admins to admin and team roles
    for member in guild.members:
        if member.guild_permissions.administrator and member != guild.me:
            await member.add_roles(
                guild.get_role(settings.admin_role),
                guild.get_role(settings.team_role)
            )


def get_settings(guild: discord.Guild | None) -> GuildSettings:
    if guild is None:
        raise app_commands.AppCommandError("You must run this command in a guild")

    settings = GuildSettings.objects(guild_id=guild.id).first()
    if settings is None:
        raise app_commands.AppCommandError(
            "Settings have not been set up correctly for this guild. "
            "Please remove and re-invite the bot to fix this."
        )
    return settings


def get_admin_role(guild: discord.Guild) -> discord.Role:
    settings = get_settings(guild)
    admin_role = guild.get_role(settings.admin_role)
    if admin_role is None:
        raise app_commands.AppCommandError(
            "Admin role missing. Please re-invite the bot to fix this."
        )
    return admin_role


def get_team_role(guild: discord.Guild) -> discord.Role:
    settings = get_settings(guild)
    team_role = guild.get_role(settings.team_role)
    if team_role is None:
        raise app_commands.AppCommandError(
            "Team role missing. Fix this with /bot set team_role <role_id>"
        )
    return team_role


def get_export_channel(guild: discord.Guild) -> discord.TextChannel:
    settings = get_settings(guild)
    export_channel = guild.get_channel(settings.export_channel)
    if export_channel is None:
        raise app_commands.AppCommandError(
            "Export channel missing. Fix this with /bot set export_channel <channel_id>"
        )
    return export_channel


def get_invite_channel(guild: discord.Guild) -> discord.TextChannel:
    settings = get_settings(guild)
    invite_channel = guild.get_channel(settings.invite_channel)
    if invite_channel is None:
        raise app_commands.AppCommandError(
            "Invite channel missing. Fix this with /bot set invite_channel <channel_id>"
        )
    return invite_channel


def _get_category(guild: discord.Guild, category_name: str) -> discord.CategoryChannel:
    settings = get_settings(guild)
    category = guild.get_channel(getattr(settings, category_name))
    if category is None:
        raise app_commands.AppCommandError(
            f"\"{category_name}\" category missing. "
            f"Fix this with /bot set {category_name} <category_id>"
        )
    return category


def get_ctfs_category(guild: discord.Guild):
    return _get_category(guild, "ctfs_category")


def get_incomplete_category(guild: discord.Guild):
    return _get_category(guild, "incomplete_category")


def get_complete_category(guild: discord.Guild):
    return _get_category(guild, "complete_category")


def get_archive_category(guild: discord.Guild):
    return _get_category(guild, "archive_category")


def get_ctf_archive_category(guild: discord.Guild):
    return _get_category(guild, "ctf_archive_category")
