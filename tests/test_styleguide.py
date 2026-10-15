def test_styleguide_shows_the_ui_kit_in_development(client, settings):
    settings.DEBUG = True

    response = client.get("/styleguide/")

    assert response.status_code == 200
    assert b"Needs your input" in response.content


def test_styleguide_is_hidden_in_production(client, settings):
    settings.DEBUG = False

    assert client.get("/styleguide/").status_code == 404
