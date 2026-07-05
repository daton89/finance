import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_path}"

import pytest  # noqa: E402
from finance_core.base import engine  # noqa: E402
from finance_core.models import AppSetting  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        conn.execute(AppSetting.__table__.delete())
