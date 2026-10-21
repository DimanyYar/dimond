import pytest

from apps.accounts.models import Role, User


@pytest.fixture
def pm(db) -> User:
    """The PM who owns the test clients (PRD: every PM has their own clients)."""
    return User.objects.create_user("dmitry@shakuro.com", name="Dmitry", role=Role.PM)


@pytest.fixture(autouse=True)
def private_folder(settings, tmp_path):
    """Every test writes files to its own temporary folder, never to uploads/: the sync and
    the answers to the team store files too, not only the files tests."""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path
