from django.contrib.staticfiles import finders


def test_browser_tab_shows_the_dimond_icon(client):
    page = client.get("/sign-in/").content.decode()

    for icon in ("icons/favicon.ico", "icons/favicon.svg", "icons/apple-touch-icon.png"):
        assert f"/static/{icon}" in page
        assert finders.find(icon), f"{icon} is linked but missing from static/"


def test_tab_of_an_opened_file_also_gets_the_dimond_icon(client):
    response = client.get("/favicon.ico")

    assert response.status_code == 301
    assert response["Location"] == "/static/icons/favicon.ico"
