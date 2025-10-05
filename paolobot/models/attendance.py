from mongoengine import Document, LongField, StringField, DateField, IntField, ReferenceField
from datetime import date


class AttendanceUser(Document):
    discord_id = LongField(required=True, unique=True)
    name = StringField(required=True, max_length=100)
    class_name = StringField(required=True, max_length=10)
    meta = {
        "indexes": [
            {"fields": ["discord_id"], "unique": True}
        ]
    }


class AttendanceRecord(Document):
    user = ReferenceField(AttendanceUser, required=True)
    date = DateField(required=True, default=date.today)
    # stored total seconds for the day (aggregation of voice time)
    seconds = IntField(required=True, default=0)
    meta = {
        "indexes": [
            {"fields": ["user", "date"], "unique": True}
        ]
    }
