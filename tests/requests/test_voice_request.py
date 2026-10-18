import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Company, Role, User
from apps.requests.models import ClientStatus, Request, RequestType, WhenNeeded
from tests.files.conftest import PNG, WEBM_AUDIO

pytestmark = pytest.mark.django_db

URL = "/requests/new/voice/"


@pytest.fixture(autouse=True)
def private_folder(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def anna(client, pm) -> User:
    atlas = Company.objects.create(name="Atlas", owner=pm)
    user = User.objects.create_user("anna@atlas-crm.com", name="Anna", company=atlas)
    client.force_login(user)
    return user


def said(**fields):
    data = {
        "title": "Duplicate leads from the web form",
        "recording": SimpleUploadedFile("voice-request.webm", WEBM_AUDIO),
    }
    data.update(fields)
    return data


def test_client_sends_a_request_by_voice(client, anna):
    response = client.post(URL, said())

    assert response.status_code == 201
    assert response["Location"] == "/requests/"
    request = Request.objects.get()
    assert request.from_voice
    assert (request.title, request.author, request.status) == (
        "Duplicate leads from the web form",
        anna,
        ClientStatus.RECEIVED,
    )
    # Why and when are in the recording; the PM sorts the type and urgency.
    assert (request.type, request.when_needed, request.why) == (
        RequestType.NOT_SURE,
        WhenNeeded.NOT_SURE,
        "",
    )
    recording = request.attachments.get()
    assert (recording.name, recording.content_type) == ("voice-request.webm", "audio/webm")


def test_voice_request_keeps_the_type_the_client_picked(client, anna):
    client.post(URL, said(type="bug"))

    assert Request.objects.get().type == RequestType.BUG


def test_text_draft_the_client_checked_becomes_the_description(client, anna):
    client.post(URL, said(text="Since yesterday every web form submission creates two leads."))

    request = Request.objects.get()
    assert request.description == "Since yesterday every web form submission creates two leads."
    card = client.get(request.get_absolute_url()).content.decode()
    assert "What we heard" in card
    assert "auto-transcribed · may contain mistakes" in card


def test_voice_request_without_a_text_draft_has_no_what_we_heard(client, anna):
    client.post(URL, said())

    request = Request.objects.get()
    assert request.description == ""
    assert "What we heard" not in client.get(request.get_absolute_url()).content.decode()


def test_voice_request_needs_a_title(client, anna):
    response = client.post(URL, said(title=""))

    assert response.status_code == 422
    assert response.json() == {"errors": ["Give it a title in one line."]}
    assert not Request.objects.exists()


def test_voice_request_needs_sound_not_a_picture(client, anna):
    response = client.post(URL, said(recording=SimpleUploadedFile("voice.webm", PNG)))

    assert response.status_code == 422
    assert response.json() == {"errors": ["The recording should be sound only. Record it again."]}


def test_voice_request_cannot_pick_not_sure_as_a_type_itself(client, anna):
    response = client.post(URL, said(type="not_sure"))

    assert response.status_code == 422


def test_pm_does_not_send_voice_requests(client):
    pm = User.objects.create_user("dmitry@shakuro.com", name="Dmitry", role=Role.PM)
    client.force_login(pm)

    assert client.post(URL, said()).status_code == 403


def test_card_of_a_voice_request_says_so_and_has_no_empty_why(client, anna):
    client.post(URL, said())

    page = client.get(Request.objects.get().get_absolute_url()).content.decode()

    assert "Voice request" in page
    assert "Why it matters:" not in page


def test_request_form_does_not_offer_not_sure_as_a_type(client, anna):
    page = client.get("/requests/new/").content.decode()

    assert 'value="not_sure"' not in page.split('name="when_needed"')[0]


def test_my_requests_leads_to_the_form_with_just_say_it_open(client, anna):
    page = client.get("/requests/").content.decode()

    assert 'href="/requests/new/?say"' in page
    # The window itself lives over the form, so a recording can move into it.
    assert 'id="voice-request"' not in page


def test_new_request_form_has_just_say_it_with_a_way_back_to_the_form(client, anna):
    page = client.get("/requests/new/").content.decode()

    assert 'id="voice-request"' in page
    assert "Switch to the full form" in page
