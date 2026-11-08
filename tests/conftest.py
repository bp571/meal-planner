import pytest

from meal_planner import db


@pytest.fixture
def session():
    """A fresh in-memory database per test."""
    db.init_db("sqlite://")
    with db.session_scope() as session:
        yield session
