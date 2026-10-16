import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Company, Role, User
from apps.files.models import Attachment
from apps.files.uploads import check_upload
from apps.requests.models import RequestType, WhenNeeded
from apps.requests.services import create_request

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"


@pytest.fixture(autouse=True)
def private_folder(settings, tmp_path):
    """Every test writes files to its own temporary folder, never to uploads/."""
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


@pytest.fixture
def atlas() -> Company:
    return Company.objects.create(name="Atlas")


@pytest.fixture
def anna(atlas) -> User:
    return User.objects.create_user("anna@atlas-crm.com", name="Anna", company=atlas)


@pytest.fixture
def screenshot(anna) -> Attachment:
    request = create_request(
        anna,
        RequestType.IDEA,
        "Duplicate leads from the web form",
        "Managers call the same person twice",
        WhenNeeded.NOT_SURE,
        files=[check_upload(upload("duplicates.png", PNG))],
    )
    return request.attachments.get()


@pytest.fixture
def pm() -> User:
    return User.objects.create_user("dmitry@shakuro.com", name="Dmitry", role=Role.PM)
