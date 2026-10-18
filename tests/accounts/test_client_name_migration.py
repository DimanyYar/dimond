"""The migration that made client names unique per PM, whatever the letter case."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("accounts", "0005_company_owner")]
AFTER = [("accounts", "0006_client_name_unique_per_pm")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.fixture
def old_apps():
    latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
    yield migrate(BEFORE)
    migrate(latest)


def test_names_that_differ_only_in_letter_case_get_a_number(old_apps):
    User = old_apps.get_model("accounts", "User")
    Company = old_apps.get_model("accounts", "Company")
    dmitry = User.objects.create(email="dmitry@shakuro.com", role="pm")
    ivan = User.objects.create(email="ivan@shakuro.com", role="pm")
    for name in ("Atlas", "atlas", "ATLAS"):
        Company.objects.create(name=name, owner=dmitry)
    Company.objects.create(name="aTlas", owner=ivan)

    new_apps = migrate(AFTER)

    names = new_apps.get_model("accounts", "Company").objects.order_by("pk")
    assert [c.name for c in names] == ["Atlas", "atlas (2)", "ATLAS (3)", "aTlas"]
