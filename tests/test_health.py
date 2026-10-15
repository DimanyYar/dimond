import pytest


@pytest.mark.django_db
def test_health_reports_ok_when_database_answers(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_rejects_writes(client):
    response = client.post("/healthz")

    assert response.status_code == 405
