import asyncio
import logging
import sys

import discord
import pymongo.errors

from discord import RawReactionActionEvent, app_commands

from paolobot.modules import ctf, ctftime, challenge, notes, bot, attendance
from paolobot.config import config
from paolobot.database import db
from paolobot.models.invite import Invite
from paolobot.utils import setup_settings

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.all()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

GUILD_OBJ = discord.Object(id=config.guild_id) if config.guild_id else None
challenge.add_commands(tree, GUILD_OBJ)
ctf.add_commands(tree, GUILD_OBJ)
ctftime.add_commands(tree, GUILD_OBJ)
notes.add_commands(tree, GUILD_OBJ)
bot.add_commands(tree, GUILD_OBJ)
attendance.add_commands(tree, GUILD_OBJ)


@client.event
async def setup_hook():
    client.add_view(notes.ModalNoteView())
    client.add_view(notes.HedgeDocNoteView(""))
    client.add_view(challenge.WorkView())


@client.event
async def on_ready():
    try:
        db.command("ping")
    except pymongo.errors.ServerSelectionTimeoutError:
        logging.critical("Could not connect to MongoDB")
        sys.exit(1)

    if config.guild_id:
        guild = client.get_guild(config.guild_id)
        if guild:
            await setup_settings(guild)
            await tree.sync(guild=GUILD_OBJ)
    else:
        for guild in client.guilds:
            await setup_settings(guild)
        await tree.sync(guild=GUILD_OBJ)
    logging.info("%s is online", client.user.name)


@client.event
async def on_guild_join(guild: discord.Guild):
    if config.guild_id is None or config.guild_id == guild.id:
        logging.info("%s has joined guild \"%s\"", client.user.name, guild.name)
        await setup_settings(guild)
        if config.guild_id:
            await tree.sync(guild=GUILD_OBJ)


@client.event
async def on_raw_reaction_add(reaction: RawReactionActionEvent):
    # Handle CTF joins through invite message reactions
    if client.user.id == reaction.user_id:
        return

    if config.guild_id is not None and config.guild_id != reaction.guild_id:
        return

    invite = Invite.objects(message_id=reaction.message_id).first()
    if invite is None or invite.emoji != str(reaction.emoji):
        return

    guild = client.get_guild(reaction.guild_id)
    member = guild.get_member(reaction.user_id)
    if member is None:
        return

    role = guild.get_role(invite.ctf.role_id)
    if role is None:
        return

    await member.add_roles(role, reason=f"User {member.name} joined CTF {invite.ctf.name}")


@client.event
async def on_raw_reaction_remove(reaction: RawReactionActionEvent):
    # Handle CTF leaves through invite message reactions
    if client.user.id == reaction.user_id:
        return

    if config.guild_id is not None and config.guild_id != reaction.guild_id:
        return

    invite = Invite.objects(message_id=reaction.message_id).first()
    if invite is None or invite.emoji != str(reaction.emoji):
        return

    guild = client.get_guild(reaction.guild_id)
    member = guild.get_member(reaction.user_id)
    if member is None:
        return

    role = guild.get_role(invite.ctf.role_id)
    if role is None:
        return

    await member.remove_roles(role, reason=f"User {member.name} left CTF {invite.ctf.name}")


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    try:
        raise error
    except app_commands.CommandInvokeError as e:
        try:
            raise e.original
        except AssertionError:
            await interaction.response.send_message(
                "An assertion failed when running this command",
                ephemeral=True
            )
    except app_commands.AppCommandError:
        if error.args:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=error.args[0])
            else:
                await interaction.response.send_message(error.args[0], ephemeral=True)


async def main():
    async with client:
        await client.start(config.bot_token)


if __name__ == "__main__":
    asyncio.run(main())
