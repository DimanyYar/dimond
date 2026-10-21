import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Company, Role, User
from apps.files.models import Attachment
from apps.files.uploads import check_upload
from apps.requests.models import RequestType, WhenNeeded
from apps.requests.services import create_request

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"
# The parts of MP4 and WebM files the type check reads: the header and a track that holds
# picture ("vide", "V_VP8") or only sound ("soun", "A_OPUS").
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"hdlr" + b"\x00" * 8 + b"vide"
M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00M4A mp42" + b"hdlr" + b"\x00" * 8 + b"soun"
WEBM = b"\x1a\x45\xdf\xa3\x9f\x42\x82\x84webm\x42\x87\x81\x04"
WEBM_VIDEO = WEBM + b"\x86\x85V_VP8"
WEBM_AUDIO = WEBM + b"\x86\x86A_OPUS"


def upload(name: str, content: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm)


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
