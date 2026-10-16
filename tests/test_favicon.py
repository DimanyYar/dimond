from django.contrib.staticfiles import finders


def test_browser_tab_shows_the_dimond_icon(client):
    page = client.get("/sign-in/").content.decode()

    for icon in ("icons/favicon.ico", "icons/favicon.svg", "icons/apple-touch-icon.png"):
        assert f"/static/{icon}" in page
        assert finders.find(icon), f"{icon} is linked but missing from static/"
