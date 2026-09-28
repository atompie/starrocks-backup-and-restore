from collections.abc import Iterator

from sqlalchemy.orm import Session

from ..store.session import get_session_factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped SQLAlchemy session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
