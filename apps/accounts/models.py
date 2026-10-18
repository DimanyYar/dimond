from typing import Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Role(models.TextChoices):
    CLIENT = "client", "Client"
    TEAM = "team", "Team"
    PM = "pm", "PM"


class Company(models.Model):
    """A client organisation. Everyone at a company sees all of its requests.

    Each company belongs to the PM who works with it; other PMs never see it, its people or
    its requests (PRD: every PM has their own clients).
    """

    name = models.CharField(max_length=120, unique=True)
    owner = models.ForeignKey(
        "User",
        on_delete=models.PROTECT,
        related_name="clients",
        limit_choices_to={"role": Role.PM},
        help_text="The PM who works with this client. Other PMs never see it.",
    )
    email_domain = models.CharField(
        max_length=120,
        blank=True,
        help_text="Invites to other domains ask the PM to confirm, e.g. atlas-crm.com",
    )
    color = models.CharField(max_length=7, default="#ff5600", help_text="Hex colour in the sidebar")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.owner_id is not None and self.owner.role != Role.PM:
            raise ValidationError({"owner": "Only a PM can own a client."})


class Notify(models.TextChoices):
    """Which requests a person gets emails about (PRD: notifications)."""

    OWN = "own", "My requests and the ones I follow"
    COMPANY = "company", "Everything at my company"
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
        if self.pk is not None and self.role != Role.PM and self.clients.exists():
            raise ValidationError(
                {"role": "This PM still owns clients. Give them to another PM first."}
            )


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
