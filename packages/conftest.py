import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"

import pytest  # noqa: E402
from finance_core.base import Base, engine  # noqa: E402
from finance_core.models import (  # noqa: E402
    ExternalRating,
    IndicatorValue,
    PriceBar,
    WatchlistStock,
)

Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(ExternalRating.__table__.delete())
        conn.execute(IndicatorValue.__table__.delete())
        conn.execute(PriceBar.__table__.delete())
        conn.execute(WatchlistStock.__table__.delete())
