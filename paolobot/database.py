from mongoengine import connect

from paolobot.config import config


client = connect(db=config.mongodb_db, host=config.mongodb_uri)
db = client[config.mongodb_db]
