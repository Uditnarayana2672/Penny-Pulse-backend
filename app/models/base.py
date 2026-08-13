from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for models that mirror ../penny-pulse-migrations/000*.sql.

    There is no metadata.create_all() anywhere, including in tests. The SQL files are
    the source of truth; when a model and the SQL disagree, the model is the bug.
    """


live_metadata = MetaData()
"""Holds the `*_live` view copies of the tables above.

Writes go to the table, reads go to the view — the view is the table with
`deleted_at IS NULL`. It is kept in its own MetaData so a view can never be mistaken
for something creatable.
"""
