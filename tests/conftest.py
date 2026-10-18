import pytest

from apps.accounts.models import Role, User


@pytest.fixture
def pm(db) -> User:
    """The PM who owns the test clients (PRD: every PM has their own clients)."""
    return User.objects.create_user("dmitry@shakuro.com", name="Dmitry", role=Role.PM)
