import logging
import os

from pathlib import Path


def parse_variable(variable, vartype, default=None, required=False):
    value = os.getenv(variable, None)
    if not value:
        if required:
            logging.fatal(f"Missing required environment variable: {variable}")
            exit(1)
        return default

    if vartype == str:
        return value
    elif vartype == bool:
        return True if value.lower() in ["true", "1", "t", "y", "yes"] else False
    elif vartype == int:
        return int(value) if value.isdigit() else default
    return default


BACKUPS_DIR_DEFAULT = (Path(__file__).parent.parent / "backups").resolve()

class Config:
    def __init__(self):
        # Required
        self.bot_token = parse_variable("BOT_TOKEN", str, required=True)

        # Options
        self.guild_id = parse_variable("GUILD_ID", int)
        self.mongodb_uri = parse_variable("MONGODB_URI", str, default="mongodb://localhost:27017")
        self.mongodb_db = parse_variable("MONGODB_DB", str, default="brunnerbot")
        self.backups_dir = parse_variable("BACKUPS_DIR", str, default=BACKUPS_DIR_DEFAULT)


config = Config()
