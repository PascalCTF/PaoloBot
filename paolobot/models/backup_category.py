from mongoengine import Document, IntField, LongField


class BackupCategory(Document):
    index = IntField(required=True)
    original_id = LongField(required=True)
    category_id = LongField(required=True)
    meta = {
        "indexes": [
            {
                "fields": ["category_id"],
                "unique": True
            },
            "original_id"
        ]
    }
