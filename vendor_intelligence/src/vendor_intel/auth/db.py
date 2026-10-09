"""SQLAlchemy engine and session factory."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vendor_intel.auth.config import get_auth_settings
from vendor_intel.auth.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_auth_settings()
        url = settings.database_url
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        # Local laptop mode: SQLite needs check_same_thread=False (pipeline threads).
        if url.startswith("sqlite"):
            from pathlib import Path

            # sqlite:///./data/foo.db → ensure parent folder exists
            raw = url.split("///", 1)[-1] if "///" in url else ""
            if raw and not raw.startswith(":memory:"):
                Path(raw).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False, future=True
        )
    return _engine


def init_db() -> None:
    """Create auth tables if they do not exist."""
    from vendor_intel.auth import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
