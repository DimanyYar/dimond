from typing import Any, ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Company(models.Model):
    """A client organisation. Everyone at a company sees all of its requests."""

    name = models.CharField(max_length=120, unique=True)
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


class Role(models.TextChoices):
    CLIENT = "client", "Client"
    TEAM = "team", "Team"
    PM = "pm", "PM"


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


class SignInLink(models.Model):
    """A one-time sign-in link (ADR 0003), queued by the sign-in page and emailed by the worker.

    The token is made at sending time and only its hash is kept, so the database never holds
    a working link. Until the worker sends it, the hash and expiry are empty.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sign_in_links")
    token_hash = models.CharField(max_length=64, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Sign-in link for {self.user}"
