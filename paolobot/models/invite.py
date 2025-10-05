from mongoengine import (
    Document,
    StringField,
    ReferenceField,
    LongField,
)
from paolobot.models.ctf import Ctf


class Invite(Document):
    message_id = LongField(required=True)
    emoji = StringField(requried=True)
    ctf = ReferenceField(Ctf, required=True)
    meta = {
        "indexes": [
            {
                "fields": ["message_id"],
                "unique": True
            }
        ]
    }
