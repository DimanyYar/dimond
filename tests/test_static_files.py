import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings

from apps.core.views import static_file


@override_settings(DEBUG=True)
def test_changed_stylesheet_shows_up_without_a_hard_reload():
    response = static_file(RequestFactory().get("/static/css/dimond.css"), "css/dimond.css")

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-cache"
    response.file_to_stream.close()


@override_settings(DEBUG=True)
def test_unchanged_stylesheet_is_not_downloaded_again():
    first = static_file(RequestFactory().get("/static/css/dimond.css"), "css/dimond.css")
    first.file_to_stream.close()
    request = RequestFactory().get(
        "/static/css/dimond.css", headers={"if-modified-since": first["Last-Modified"]}
    )

    assert static_file(request, "css/dimond.css").status_code == 304


def test_django_does_not_serve_static_files_in_production():
    with pytest.raises(Http404):
        static_file(RequestFactory().get("/static/css/dimond.css"), "css/dimond.css")
