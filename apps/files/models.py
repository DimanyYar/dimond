import uuid

from django.db import models
from django.urls import reverse

from apps.accounts.models import User
from apps.requests.models import Request


def _stored_name(attachment: "Attachment", filename: str) -> str:
    """The name on disk is the attachment's random id, never the client's file name: a name
    like "../../config.py" or "invoice.pdf.exe" means nothing to the server."""
    return str(attachment.id)


# Opened right in the browser. Anything else is downloaded, so the browser never runs it.
SHOWN_IN_BROWSER = ("image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf")


class Attachment(models.Model):
    """A file attached to a request. It sits in a private folder (settings.MEDIA_ROOT) that no
    address serves directly; people open it only through `/files/<id>/`, after an access check.
    """

    # Random, so a file's address cannot be guessed from another one's.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(Request, on_delete=models.PROTECT, related_name="attachments")
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    file = models.FileField(upload_to=_stored_name, max_length=100)
    name = models.CharField(max_length=255, help_text="The file's name on the uploader's computer.")
    content_type = models.CharField(
        max_length=100, help_text="Checked from the file's first bytes, not from its name."
    )
    size = models.PositiveIntegerField(help_text="In bytes.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("open_file", args=[self.id])

    @property
    def shown_in_browser(self) -> bool:
        return self.content_type in SHOWN_IN_BROWSER
