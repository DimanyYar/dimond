import pytest

from apps.files.models import Attachment
from apps.requests import services
from apps.requests.models import ClientStatus

from .conftest import PDF, PNG, upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def request_(screenshot):
    return screenshot.request


def card(request):
    return request.get_absolute_url()


def edit(request):
    return f"/requests/{request.pk}/edit/"


def as_edited(request, **changes):
    data = {
        "type": request.type,
        "title": request.title,
        "why": request.why,
        "when_needed": request.when_needed,
        "description": request.description,
        "link": request.link,
    }
    data.update(changes)
    return data


def test_reply_box_offers_screen_recording_voice_note_and_camera(client, request_, anna):
    client.force_login(anna)

    page = client.get(card(request_)).content.decode()

    reply = page[page.index('<form id="reply"') :]
    reply = reply[: reply.index("</form>")]
    assert 'name="attachments"' in reply
    assert 'aria-label="Record screen"' in reply
    assert 'aria-label="Voice note"' in reply
    assert 'aria-label="Camera"' in reply


def test_client_replies_with_only_a_screenshot(client, request_, anna):
    client.force_login(anna)

    client.post(card(request_), {"text": "", "attachments": [upload("lead.png", PNG)]})

    message = request_.messages.get()
    assert message.text == ""
    assert [file.name for file in message.attachments.all()] == ["lead.png"]


def test_reply_takes_up_to_five_files_at_once(client, request_, anna):
    client.force_login(anna)

    response = client.post(
        card(request_),
        {"text": "All steps", "attachments": [upload(f"{n}.png", PNG) for n in range(6)]},
    )

    assert response.status_code == 422
    assert "Attach up to 5 files at a time." in response.content.decode()
    assert not request_.messages.exists()


def test_reply_with_a_file_answers_the_pm_question(client, request_, anna, pm):
    services.post_message(pm, request_, "Which lead?", ask_for_input=True)
    client.force_login(anna)

    client.post(card(request_), {"text": "", "attachments": [upload("lead.png", PNG)]})

    request_.refresh_from_db()
    assert request_.status == ClientStatus.RECEIVED


@pytest.mark.parametrize(
    "status",
    [
        ClientStatus.PLANNED,
        ClientStatus.IN_PROGRESS,
        ClientStatus.READY_TO_CHECK,
        ClientStatus.LATER,
    ],
)
def test_files_are_added_through_the_thread_once_planned(client, request_, anna, pm, status):
    services.change_status(request_, status, pm)
    client.force_login(anna)

    client.post(card(request_), {"text": "One more", "attachments": [upload("more.png", PNG)]})

    assert request_.messages.get().attachments.count() == 1


@pytest.mark.parametrize(
    "status", [ClientStatus.DONE, ClientStatus.DECLINED, ClientStatus.WITHDRAWN]
)
def test_no_files_are_added_to_a_closed_request(client, request_, anna, pm, status):
    services.change_status(request_, status, pm)
    client.force_login(anna)

    response = client.post(card(request_), {"text": "", "attachments": [upload("more.png", PNG)]})

    assert response.status_code == 403
    assert Attachment.objects.count() == 1


def test_pm_attaches_a_file_to_a_reply(client, request_, pm):
    client.force_login(pm)

    client.post(card(request_), {"text": "Mockup attached", "attachments": [upload("m.pdf", PDF)]})

    assert request_.messages.get().attachments.get().content_type == "application/pdf"


def test_client_adds_files_in_the_edit_form(client, request_, anna):
    client.force_login(anna)

    client.post(edit(request_), as_edited(request_, attachments=[upload("spec.pdf", PDF)]))

    added = Attachment.objects.get(name="spec.pdf")
    assert added.message is None
    assert added.added_in == request_.revisions.get()


def test_client_removes_a_request_file_in_the_edit_form(client, screenshot, anna, private_folder):
    client.force_login(anna)

    client.post(
        edit(screenshot.request), as_edited(screenshot.request, remove=[str(screenshot.id)])
    )

    page = client.get(card(screenshot.request)).content.decode()
    assert f'href="/files/{screenshot.id}/"' not in page
    assert "Removed files" in page
    assert "duplicates.png" in page  # named in the edit history
    # Kept on disk, so the PM can still find out what was there.
    assert (private_folder / str(screenshot.id)).exists()


def test_changing_only_the_files_counts_as_an_edit(client, screenshot, anna):
    client.force_login(anna)

    client.post(
        edit(screenshot.request), as_edited(screenshot.request, remove=[str(screenshot.id)])
    )

    page = client.get(card(screenshot.request)).content.decode()
    assert "Anna edited the request" in page


def test_editing_files_keeps_the_status(client, request_, anna, pm):
    services.post_message(pm, request_, "Which lead?", ask_for_input=True)
    client.force_login(anna)

    client.post(edit(request_), as_edited(request_, attachments=[upload("lead.png", PNG)]))

    request_.refresh_from_db()
    assert request_.status == ClientStatus.NEEDS_INPUT


def test_files_from_the_thread_cannot_be_removed(client, request_, anna):
    message = services.post_message(anna, request_, "Here", files=[])
    in_thread = Attachment.objects.create(
        request=request_,
        message=message,
        uploaded_by=anna,
        file=upload("thread.png", PNG),
        name="thread.png",
        content_type="image/png",
        size=len(PNG),
    )
    client.force_login(anna)

    client.post(edit(request_), as_edited(request_, remove=[str(in_thread.id)]))

    in_thread.refresh_from_db()
    assert in_thread.removed_in is None


def test_files_cannot_be_changed_in_the_edit_form_once_planned(client, screenshot, anna, pm):
    services.change_status(screenshot.request, ClientStatus.PLANNED, pm)
    client.force_login(anna)

    response = client.post(
        edit(screenshot.request),
        as_edited(
            screenshot.request, remove=[str(screenshot.id)], attachments=[upload("x.png", PNG)]
        ),
    )

    assert response.status_code == 409
    screenshot.refresh_from_db()
    assert screenshot.removed_in is None
    assert Attachment.objects.count() == 1


def test_all_attachments_lists_request_and_thread_files_with_author(client, request_, anna, pm):
    client.force_login(pm)
    client.post(card(request_), {"text": "Mockup", "attachments": [upload("mockup.pdf", PDF)]})
    client.force_login(anna)

    page = client.get(card(request_)).content.decode()

    panel = page[page.index("All attachments") :]
    assert "duplicates.png" in panel
    assert "mockup.pdf" in panel
    assert "Dmitry" in panel
