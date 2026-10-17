import uuid

from django.db import models
from django.urls import reverse

from apps.accounts.models import User
from apps.requests.models import Message, Request, RequestRevision


def _stored_name(attachment: "Attachment", filename: str) -> str:
    """The name on disk is the attachment's random id, never the client's file name: a name
    like "../../config.py" or "invoice.pdf.exe" means nothing to the server."""
    return str(attachment.id)


def _preview_name(attachment: "Attachment", filename: str) -> str:
    return f"previews/{attachment.id}.webp"


class PreviewStatus(models.TextChoices):
    """Whether a small picture of the file exists for its tile (PRD: attachments, previews)."""

    PENDING = "pending", "Being made"
    READY = "ready", "Ready"
    # Audio and video tiles are drawn without a picture; a file that could not be read gets none.
    NONE = "none", "None"


# Opened right in the browser. Anything else is downloaded, so the browser never runs it.
SHOWN_IN_BROWSER = (
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "audio/mp4",
    "audio/webm",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
)


class AttachmentQuerySet(models.QuerySet["Attachment"]):
    def shown(self) -> "AttachmentQuerySet":
        """Files still on the card: a file removed in an edit stays on disk and in the edit
        history, but nobody opens it from the card anymore."""
        return self.filter(removed_in__isnull=True)


class Attachment(models.Model):
    """A file attached to a request. It sits in a private folder (settings.MEDIA_ROOT) that no
    address serves directly; people open it only through `/files/<id>/`, after an access check.
    """

    # Random, so a file's address cannot be guessed from another one's.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(Request, on_delete=models.PROTECT, related_name="attachments")
    message = models.ForeignKey(
        Message,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="attachments",
        help_text="Empty for the request's own files; set for files sent with a reply.",
    )
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    file = models.FileField(upload_to=_stored_name, max_length=100)
    name = models.CharField(max_length=255, help_text="The file's name on the uploader's computer.")
    content_type = models.CharField(
        max_length=100, help_text="Checked from the file's first bytes, not from its name."
    )
    size = models.PositiveIntegerField(help_text="In bytes.")
    created_at = models.DateTimeField(auto_now_add=True)
    added_in = models.ForeignKey(
        RequestRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="added_files",
        help_text="The edit that added it; empty when sent with the request or a reply.",
    )
    preview = models.FileField(upload_to=_preview_name, max_length=100, blank=True)
    preview_status = models.CharField(
        max_length=10, choices=PreviewStatus.choices, default=PreviewStatus.NONE
    )
    pages = models.PositiveIntegerField(null=True, blank=True, help_text="For a PDF.")
    excerpt = models.TextField(blank=True, help_text="A text file's first lines, for its tile.")
    removed_in = models.ForeignKey(
        RequestRevision,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="removed_files",
        help_text="The edit that removed it from the card. The file itself is kept.",
    )

    objects = AttachmentQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("open_file", args=[self.id])

    @property
    def preview_url(self) -> str:
        """Empty until the preview is made, and for files that have none."""
        if self.preview_status != PreviewStatus.READY:
            return ""
        return reverse("open_preview", args=[self.id])

    @property
    def extension(self) -> str:
        """ "PNG", "LOG": the label of a tile without a picture."""
        return self.name.rpartition(".")[2].upper()[:4] if "." in self.name else "FILE"

    @property
    def shown_in_browser(self) -> bool:
        return self.content_type in SHOWN_IN_BROWSER

    @property
    def kind(self) -> str:
        """How the viewer shows it: "image", "video", "audio" or "pdf"."""
        if self.content_type == "application/pdf":
            return "pdf"
        return self.content_type.split("/")[0]
