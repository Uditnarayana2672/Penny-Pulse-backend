from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        # Supabase's pooler drops idle connections; without this the first query
        # after a quiet period fails instead of reconnecting.
        pool_pre_ping=True,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """One request-scoped session that commits exactly once, on success.

    Nothing downstream commits, rolls back or begins. A transfer pair, a split, a
    refund and a month_start_day change each write mutually-referencing rows against
    DEFERRABLE INITIALLY DEFERRED constraints, and are only valid inside one
    transaction — a stray commit elsewhere breaks all four, and only under real data.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
