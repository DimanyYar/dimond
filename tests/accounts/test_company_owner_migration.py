"""The migration that gave companies an owner: clients that already exist go to the PM."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("accounts", "0004_invites")]
AFTER = [("accounts", "0005_company_owner")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def old_apps():
    latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
    apps = migrate(BEFORE)
    yield apps
    # Back to the latest schema for the next tests; an ownerless company would stop it.
    apps.get_model("accounts", "Company").objects.all().delete()
    migrate(latest)


def test_existing_companies_go_to_the_superuser_pm(old_apps):
    User = old_apps.get_model("accounts", "User")
    Company = old_apps.get_model("accounts", "Company")
    User.objects.create(email="ivan@shakuro.com", role="pm")
    dmitry = User.objects.create(email="dmitry@shakuro.com", role="pm", is_superuser=True)
    Company.objects.create(name="Atlas")

    new_apps = migrate(AFTER)

    atlas = new_apps.get_model("accounts", "Company").objects.get(name="Atlas")
    assert atlas.owner_id == dmitry.pk


def test_companies_go_to_an_active_pm_not_a_revoked_superuser(old_apps):
    User = old_apps.get_model("accounts", "User")
    User.objects.create(email="old@shakuro.com", role="pm", is_superuser=True, is_active=False)
    dmitry = User.objects.create(email="dmitry@shakuro.com", role="pm")
    old_apps.get_model("accounts", "Company").objects.create(name="Atlas")

    new_apps = migrate(AFTER)

    atlas = new_apps.get_model("accounts", "Company").objects.get(name="Atlas")
    assert atlas.owner_id == dmitry.pk


def test_companies_without_an_active_pm_stop_the_migration_with_a_hint(old_apps):
    old_apps.get_model("accounts", "User").objects.create(
        email="old@shakuro.com", role="pm", is_active=False
    )
    old_apps.get_model("accounts", "Company").objects.create(name="Atlas")

    with pytest.raises(RuntimeError, match="createsuperuser"):
        migrate(AFTER)
