from discord.ext import tasks
from datetime import datetime, timedelta
import discord
from discord import app_commands
import io
import csv
import tempfile

from paolobot.models.attendance import AttendanceUser, AttendanceRecord


def register_user(discord_id: int, name: str, class_name: str) -> None:
    user = AttendanceUser.objects(discord_id=discord_id).first()
    if user is None:
        user = AttendanceUser(discord_id=discord_id, name=name, class_name=class_name)
    else:
        user.name = name
        user.class_name = class_name
    user.save()


def user_already_registered(user_id: int) -> bool:
    return AttendanceUser.objects(discord_id=user_id).first() is not None


def get_registered_users():
    return [u.discord_id for u in AttendanceUser.objects.only('discord_id')]


def save_to_db(seconds_map: dict[int, int]) -> None:
    today = datetime.now().date()
    for uid, secs in seconds_map.items():
        user = AttendanceUser.objects(discord_id=uid).first()
        if user is None:
            continue  # skip unknown
        rec = AttendanceRecord.objects(user=user, date=today).first()
        if rec is None:
            rec = AttendanceRecord(user=user, date=today, seconds=secs)
        else:
            rec.seconds += secs
        rec.save()

def get_status_attendance_csv(target_date: datetime.date) -> tempfile.NamedTemporaryFile:
    # Build CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Class", "Time"])
    for uid, secs in members_total_time.items():
        user = AttendanceUser.objects(discord_id=uid).first()
        if user is None:
            continue
        total = timedelta(seconds=secs)
        writer.writerow([user.name, user.class_name, str(total)])
    tmp = tempfile.NamedTemporaryFile(prefix=f"attendance_status_{target_date}_", suffix=".csv", delete=False)
    try:
        with open(tmp.name, 'w', encoding='utf-8') as f:
            f.write(output.getvalue())
    finally:
        tmp.close()
    return tmp

    # Create unique temp file to
def get_attendance_results_csv(target_date: datetime.date) -> tempfile.NamedTemporaryFile:
    # Build CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Class", "Time"])
    for rec in AttendanceRecord.objects(date=target_date).select_related():
        total = timedelta(seconds=rec.seconds)
        writer.writerow([rec.user.name, rec.user.class_name, str(total)])
    # Create unique temp file to avoid race conditions
    tmp = tempfile.NamedTemporaryFile(prefix=f"attendance_{target_date}_", suffix=".csv", delete=False)
    try:
        with open(tmp.name, 'w', encoding='utf-8') as f:
            f.write(output.getvalue())
    finally:
        tmp.close()
    return tmp


# In-memory tracking
members_time = {}
members_total_time = {}
user_notified = set()


async def send_dms(bot, ids: list[int], server: discord.Guild):
    notified = []
    for id in ids:
        try:
            user = await bot.fetch_user(id)
            await user.send(f"Registrati nel server {server.name} con il comando /signup")
            notified.append(id)
        except Exception:
            pass
    return notified


@tasks.loop(seconds=10)
async def timer_members(bot, server: discord.Guild, registered_users: list[int]):
    not_registered = set()
    for vc in server.voice_channels + server.stage_channels:
        if vc == server.afk_channel:
            continue

        for member in vc.members:
            if member.id not in registered_users:
                not_registered.add(member.id)
                continue
            if member.id not in members_total_time:
                members_total_time[member.id] = 0
            members_total_time[member.id] += 10
    
    user_sent = await send_dms(bot,list(not_registered - user_notified),server)
    for u in user_sent:
        user_notified.add(u)


class AttendanceCommands(app_commands.Group):
    @app_commands.command(description="Keep track of attendance")
    @app_commands.guild_only
    @app_commands.checks.has_permissions(administrator=True)
    async def start(self, interaction: discord.Interaction):
        registered_users = get_registered_users()
        server = interaction.guild
        if not server or not (server.voice_channels + server.stage_channels):
            await interaction.response.send_message("This command cannot be used in DMs or in servers without voice channels.", ephemeral=True)
            return

        if timer_members.is_running():
            await interaction.response.send_message("The bot is already tracking attendance.", ephemeral=True)
        else:
            timer_members.start(self._get_bot(), server, registered_users)
            await interaction.response.send_message("The bot has started checking student attendance.")

    @app_commands.command(description="Stop tracking attendance")
    @app_commands.guild_only
    @app_commands.checks.has_permissions(administrator=True)
    async def stop(self, interaction: discord.Interaction):
        registered_users = get_registered_users()
        server = interaction.guild
        if timer_members.is_running():
            now = datetime.now()
        for vc in server.voice_channels + server.stage_channels:
            if vc == server.afk_channel:
                continue

            for member in vc.members:
                if member.id not in registered_users:
                    continue
                
                if member.id not in members_total_time:
                    members_total_time[member.id] = 0
                members_total_time[member.id] += 10

            timer_members.stop()

            # Convert tracked seconds into accumulation map
            seconds_map = {uid: int(total) for uid, total in members_total_time.items()}
            save_to_db(seconds_map)

            members_time.clear()
            members_total_time.clear()
            user_notified.clear()
            results_file : tempfile.NamedTemporaryFile = get_attendance_results_csv(datetime.now().date())
            try:
                await interaction.response.send_message(f"Attendance results for {datetime.now().date()}:", file=discord.File(results_file.name))
            finally:
                try:
                    results_file.close()
                except Exception:
                    pass
        else:
            await interaction.response.send_message("The bot is not currently tracking attendance.", ephemeral=True)

    def _get_bot(self):
        # Inspect parent to find client object
        # app_commands.Group instances don't store bot reference, so rely on discord.app_commands.CommandTree client
        # Fallback: try to access a global 'client' if present
        try:
            # This will work when tree is bound to a Client and stored as module-level in main
            from paolobot.main import client
            return client
        except Exception:
            raise RuntimeError("Unable to find bot client")
    @app_commands.command(description="Check if the bot is currently tracking attendance")
    @app_commands.guild_only
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        if timer_members.is_running():
            results_file : tempfile.NamedTemporaryFile = get_status_attendance_csv(datetime.now().date())
            try:
                await interaction.response.send_message(f"Current attendance status for {datetime.now().date()}:", file=discord.File(results_file.name))
            finally:
                try:
                    results_file.close()
                except Exception:
                    pass
        else:
            await interaction.response.send_message("The bot is not currently tracking attendance.")


    @app_commands.command(description="Get attendance results for a specific date (format: DD-MM-YYYY)")
    @app_commands.guild_only
    async def results(self, interaction: discord.Interaction, date: str):
        try:
            date_obj = datetime.strptime(date, "%d-%m-%Y").date()
        except ValueError:
            await interaction.response.send_message("Invalid date format. Use DD-MM-YYYY.", ephemeral=True)
            return

        results_file : tempfile.NamedTemporaryFile = get_attendance_results_csv(date_obj)
        try:
            await interaction.response.send_message(f"Attendance results for {date_obj}:", file=discord.File(results_file.name))
        finally:
            try:
                results_file.close()
            except Exception:
                pass

    

    @app_commands.command(description="Register your name and class")
    @app_commands.guild_only
    async def signup(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("This command cannot be used in DMs.", ephemeral=True)

        if not user_already_registered(interaction.user.id):
            class SignupModal(discord.ui.Modal, title="Signup Form"):
                name = discord.ui.TextInput(label="Name", placeholder="Enter your full name", max_length=100)
                class_name = discord.ui.TextInput(label="Class", placeholder="Enter your class (e.g., 3A)", max_length=3)

                async def on_submit(self, submit_interaction: discord.Interaction):
                    register_user(interaction.user.id, self.name.value, self.class_name.value)
                    await submit_interaction.response.send_message(f"Thank you for signing up, {self.name.value} from class {self.class_name.value}!", ephemeral=True)

            await interaction.response.send_modal(SignupModal())
        else:
            await interaction.response.send_message("You are already registered.", ephemeral=True)


def add_commands(tree: app_commands.CommandTree, guild: discord.Object | None):
    tree.add_command(AttendanceCommands(name="attendance"), guild=guild)
