from typing import Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.notifications.discord import WEBHOOK, webhook_tail
from apps.tracker.base import PROJECT_KEY

from .time_zones import validate_time_zone


class Role(models.TextChoices):
    CLIENT = "client", "Client"
    TEAM = "team", "Team"
    PM = "pm", "PM"


class Company(models.Model):
    """A client organisation. Everyone at a company sees all of its requests.

    Each company belongs to the PM who works with it; other PMs never see it, its people or
    its requests (PRD: every PM has their own clients).
    """

    name = models.CharField(max_length=120)
    owner = models.ForeignKey(
        "User",
        on_delete=models.PROTECT,
        related_name="clients",
        limit_choices_to={"role": Role.PM, "is_active": True},
        help_text="The PM who works with this client. Other PMs never see it.",
    )
    email_domain = models.CharField(
        max_length=120,
        blank=True,
        help_text="Invites to other domains ask the PM to confirm, e.g. atlas-crm.com",
    )
    color = models.CharField(max_length=7, default="#ff5600", help_text="Hex colour in the sidebar")
    jira_project = models.CharField(
        max_length=20,
        blank=True,
        validators=[RegexValidator(rf"^{PROJECT_KEY.pattern}$", "A Jira project key, like FI.")],
        help_text="Key of the client's Jira project, e.g. FI. Their tasks are created only there.",
    )
    discord_webhook = models.CharField(
        max_length=300,
        blank=True,
        validators=[
            RegexValidator(
                WEBHOOK,
                "Paste the webhook address Discord gives: https://discord.com/api/webhooks/…",
            )
        ],
        help_text="Webhook of the forum channel for dimond on the client's Discord server. Each "
        "Jira task gets a topic there (PRD: Discord for the team). Empty: nothing goes to Discord.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"
        constraints = [
            # Unique per PM, not across PMs: another PM's clients are invisible, so a clash
            # with them could not be fixed and would reveal that they exist (ADR 0014).
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="one_client_name_per_pm",
                violation_error_message="You already have a client with this name.",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def discord_webhook_tail(self) -> str:
        return webhook_tail(self.discord_webhook)

    @property
    def text_on_color(self) -> str:
        """Ink or white for the initial on the company colour, whichever reads better."""
        red, green, blue = (int(self.color[i : i + 2], 16) for i in (1, 3, 5))
        return "#ffffff" if 0.299 * red + 0.587 * green + 0.114 * blue < 140 else "#111111"

    def clean(self) -> None:
        # owner_id is still empty when the admin form left the field blank.
        if self.owner_id is not None and not (self.owner.role == Role.PM and self.owner.is_active):
            raise ValidationError({"owner": "Only a PM with access can own a client."})


class Notify(models.TextChoices):
    """Which requests a person gets emails about (PRD: notifications)."""

    OWN = "own", "My requests"
    COMPANY = "company", "My requests and my team’s"
    NONE = "none", "Nothing"


class UserManager(BaseUserManager["User"]):
    def create_user(self, email: str, name: str = "", **extra: Any) -> "User":
        user = self.model(email=email, name=name, **extra)
        user.set_unusable_password()
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, name: str = "") -> "User":
        user = self.model(
            email=email,
            name=name,
            role=Role.PM,
            is_staff=True,
            is_superuser=True,
        )
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """Anyone who signs in: client people, the Shakuro team, the PM.

    Clients belong to exactly one company; team members and the PM belong to none.
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120, blank=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.PROTECT, related_name="people"
    )
    notify = models.CharField(max_length=10, choices=Notify.choices, default=Notify.OWN)
    time_zone = models.CharField(
        max_length=64,
        blank=True,
        validators=[validate_time_zone],
        help_text="IANA name, e.g. Europe/Moscow. Empty: times show in UTC.",
    )
    emails_off_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a spam mark switched their emails to Nothing; cleared once they or "
        "the PM pick again.",
    )
    is_active = models.BooleanField(
        default=True, help_text="Off means access is revoked; requests and replies stay."
    )
    is_staff = models.BooleanField(default=False, help_text="Can open the Django admin.")
    date_joined = models.DateTimeField(default=timezone.now)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["name"]

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")), name="email_is_lowercase"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(role=Role.CLIENT, company__isnull=False)
                    | (~models.Q(role=Role.CLIENT) & models.Q(company__isnull=True))
                ),
                name="only_clients_belong_to_a_company",
            ),
        ]

    def __str__(self) -> str:
        return self.name or self.email

    @property
    def initials(self) -> str:
        """Up to two letters for the round avatar; "@" for someone with no name yet."""
        words = self.name.split()
        return "".join(word[0] for word in words[:2]).upper() or "@"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Nobody signs in with a password except the admin superuser (ADR 0003). An empty
        # password string counts as usable in Django, so every other path gets an unusable one.
        if not self.password:
            self.set_unusable_password()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        # Runs before the uniqueness check: Anna@Atlas.com and anna@atlas.com are one person.
        self.email = self.email.strip().lower()
        # A client nobody owns would vanish from every PM's view (ADR 0014).
        if (
            self.pk is not None
            and (self.role != Role.PM or not self.is_active)
            and self.clients.exists()
        ):
            raise ValidationError("This PM still owns clients. Give them to another PM first.")


class LinkKind(models.TextChoices):
    SIGN_IN = "sign_in", "Sign-in link"
    INVITE = "invite", "Invite"


class SignInLink(models.Model):
    """A one-time link (ADR 0003), queued by the sign-in page or the PM, emailed by the worker.

    An invite is the same link with a longer life that opens the welcome screen first.
    The token is made at sending time and only its hash is kept, so the database never holds
    a working link. Until the worker sends it, the hash and expiry are empty.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sign_in_links")
    kind = models.CharField(max_length=10, choices=LinkKind.choices, default=LinkKind.SIGN_IN)
    invited_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    token_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for {self.user}"


# Checked in order: Edge and Opera also say "Chrome", Chrome also says "Safari", and an
# iPhone also says "Mac OS X".
BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
SYSTEMS = (
    ("iPhone", "iPhone", "iOS"),
    ("iPad", "iPad", "iOS"),
    ("Android", "Android", "AND"),
    ("Mac OS X", "macOS", "MAC"),
    ("Windows", "Windows", "WIN"),
    ("Linux", "Linux", "LNX"),
)


class Device(models.Model):
    """A browser someone signed in from, so the PM can see where they are signed in and sign
    them out everywhere (PRD: person page, ADR 0016). One per session."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    session_key = models.CharField(max_length=40, unique=True)
    user_agent = models.CharField(max_length=300, blank=True)
    signed_in_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.name} for {self.user}"

    @property
    def name(self) -> str:
        """ "Chrome on macOS": enough to tell a laptop from a phone, not a fingerprint."""
        browser = next((name for mark, name in BROWSERS if mark in self.user_agent), "Browser")
        system = next((name for mark, name, _ in SYSTEMS if mark in self.user_agent), None)
        return f"{browser} on {system}" if system else browser

    @property
    def system_mark(self) -> str:
        return next((mark for key, _, mark in SYSTEMS if key in self.user_agent), "WEB")
