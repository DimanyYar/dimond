import uuid

from django.db import models
from django.urls import reverse

from apps.accounts.models import User
from apps.requests.models import Message, Request, RequestRevision


def _stored_name(attachment: "Attachment", filename: str) -> str:
    """The name on disk is the attachment's random id, never the client's file name: a name
    like "../../config.py" or "invoice.pdf.exe" means nothing to the server."""
    return str(attachment.id)


# Opened right in the browser. Anything else is downloaded, so the browser never runs it.
SHOWN_IN_BROWSER = ("image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf")


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
    def shown_in_browser(self) -> bool:
        return self.content_type in SHOWN_IN_BROWSER

    @property
    def is_image(self) -> bool:
        return self.content_type.startswith("image/")
