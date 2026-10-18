"""The PM invites people from the Clients page, sends an invite again or calls it off
(PRD: Clients screen)."""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import Company, LinkKind, Notify, Role, SignInLink, User

pytestmark = pytest.mark.django_db


@pytest.fixture
def atlas(pm) -> Company:
    return Company.objects.create(name="Atlas", owner=pm, email_domain="atlas-crm.com")


@pytest.fixture
def signed_in_pm(client, pm):
    client.force_login(pm)
    return client


def invite(client, company: Company, emails: str, **extra):
    return client.post(
        reverse("invite_people", args=[company.pk]), {"emails": emails, "notify": "own", **extra}
    )


def test_pm_invites_several_people_at_once(signed_in_pm, atlas):
    response = invite(
        signed_in_pm, atlas, "Sam.R@atlas-crm.com, kate@atlas-crm.com\nlee@atlas-crm.com"
    )

    assert response.status_code == 302
    people = User.objects.filter(company=atlas).order_by("email")
    assert [p.email for p in people] == [
        "kate@atlas-crm.com",
        "lee@atlas-crm.com",
        "sam.r@atlas-crm.com",
    ]
    assert all(p.role == Role.CLIENT and p.notify == Notify.OWN for p in people)
    assert SignInLink.objects.filter(user__in=people, kind=LinkKind.INVITE).count() == 3


def test_invites_go_out_by_email_with_who_invited(signed_in_pm, atlas):
    invite(signed_in_pm, atlas, "sam@atlas-crm.com")

    services.send_queued_links()

    [email] = mail.outbox
    assert email.to == ["sam@atlas-crm.com"]
    assert "Dmitry" in email.body


def test_pm_chooses_what_invited_people_hear_about(signed_in_pm, atlas):
    invite(signed_in_pm, atlas, "sam@atlas-crm.com", notify="company")

    assert User.objects.get(email="sam@atlas-crm.com").notify == Notify.COMPANY


def test_address_outside_the_company_domain_asks_to_confirm_first(signed_in_pm, atlas):
    response = invite(signed_in_pm, atlas, "sam@atlas-crm.com, dev@nimbus.io")

    assert response.status_code == 200
    assert "Send anyway" in response.content.decode()
    assert not User.objects.filter(company=atlas).exists()

    invite(signed_in_pm, atlas, "sam@atlas-crm.com, dev@nimbus.io", send_anyway="on")

    assert User.objects.filter(company=atlas).count() == 2


def test_an_address_with_an_account_is_refused_without_naming_its_company(signed_in_pm, atlas):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)
    User.objects.create_user("paul@harbor.test", company=harbor)

    response = invite(signed_in_pm, atlas, "sam@atlas-crm.com, Paul@harbor.test", send_anyway="on")

    html = response.content.decode()
    assert "Already has a dimon.d account: paul@harbor.test" in html
    assert "Harbor" not in html
    assert not User.objects.filter(company=atlas).exists()


@pytest.mark.parametrize("emails", ["", "sam@atlas-crm.com, not-an-email"])
def test_empty_or_wrong_addresses_are_refused(signed_in_pm, atlas, emails):
    response = invite(signed_in_pm, atlas, emails)

    assert response.status_code == 200
    assert not User.objects.filter(company=atlas).exists()


def test_more_than_twenty_people_at_once_is_refused(signed_in_pm, atlas):
    emails = ", ".join(f"p{n}@atlas-crm.com" for n in range(21))

    response = invite(signed_in_pm, atlas, emails)

    assert "Up to 20 people at once" in response.content.decode()


def test_pm_cannot_invite_to_another_pms_client(signed_in_pm):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)

    response = invite(signed_in_pm, harbor, "sam@harbor.test")

    assert response.status_code == 404
    assert not User.objects.filter(email="sam@harbor.test").exists()


def test_client_cannot_invite_colleagues(client, atlas):
    anna = User.objects.create_user("anna@atlas-crm.com", company=atlas)
    client.force_login(anna)

    assert invite(client, atlas, "sam@atlas-crm.com").status_code == 404


def test_resend_gives_a_person_who_has_not_signed_in_a_fresh_link(signed_in_pm, atlas, pm):
    sam = User.objects.create_user("sam@atlas-crm.com", company=atlas)
    now = timezone.now()
    SignInLink.objects.create(
        user=sam,
        kind=LinkKind.INVITE,
        sent_at=now - timedelta(days=9),
        expires_at=now - timedelta(days=2),
    )

    signed_in_pm.post(reverse("resend_invite", args=[atlas.pk, sam.pk]))

    assert sam.sign_in_links.filter(sent_at__isnull=True, invited_by=pm).exists()


def test_no_resend_for_someone_who_already_signed_in(signed_in_pm, atlas):
    anna = User.objects.create_user("anna@atlas-crm.com", company=atlas, last_login=timezone.now())

    signed_in_pm.post(reverse("resend_invite", args=[atlas.pk, anna.pk]))

    assert not anna.sign_in_links.exists()


def test_cancelling_an_invite_removes_the_person_and_their_link(signed_in_pm, atlas, pm):
    sam = User.objects.create_user("sam@atlas-crm.com", company=atlas)
    services.invite([sam], invited_by=pm)

    response = signed_in_pm.post(reverse("cancel_invite", args=[atlas.pk, sam.pk]))

    assert response.status_code == 302
    assert not User.objects.filter(email="sam@atlas-crm.com").exists()
    assert not SignInLink.objects.exists()


def test_a_person_who_signed_in_cannot_be_cancelled(signed_in_pm, atlas):
    anna = User.objects.create_user("anna@atlas-crm.com", company=atlas, last_login=timezone.now())

    signed_in_pm.post(reverse("cancel_invite", args=[atlas.pk, anna.pk]))

    assert User.objects.filter(pk=anna.pk).exists()


def test_cancel_asks_first_in_the_persons_row(signed_in_pm, atlas):
    sam = User.objects.create_user("sam@atlas-crm.com", company=atlas)

    html = signed_in_pm.get(
        reverse("client", args=[atlas.pk]) + f"?cancel={sam.pk}"
    ).content.decode()

    assert "Cancel the invite to sam@atlas-crm.com?" in html


def test_pm_cannot_touch_people_of_another_pms_client(signed_in_pm):
    ivan = User.objects.create_user("ivan@shakuro.com", role=Role.PM)
    harbor = Company.objects.create(name="Harbor Foods", owner=ivan)
    sam = User.objects.create_user("sam@harbor.test", company=harbor)

    cancel = signed_in_pm.post(reverse("cancel_invite", args=[harbor.pk, sam.pk]))
    resend = signed_in_pm.post(reverse("resend_invite", args=[harbor.pk, sam.pk]))

    assert (cancel.status_code, resend.status_code) == (404, 404)
    assert User.objects.filter(pk=sam.pk).exists()
    assert not sam.sign_in_links.exists()


def test_only_separators_are_not_a_list_of_people(signed_in_pm, atlas):
    response = invite(signed_in_pm, atlas, " , ;\n,")

    assert "Add an email." in response.content.decode()
    assert not User.objects.filter(company=atlas).exists()


def test_an_address_added_meanwhile_is_a_form_error_not_a_crash(signed_in_pm, atlas, monkeypatch):
    # The form's check passes, then someone else saves the same address before this save.
    monkeypatch.setattr("apps.accounts.forms.selectors.email_has_account", lambda email: False)
    User.objects.create_user("sam@atlas-crm.com", company=atlas)

    response = invite(signed_in_pm, atlas, "kate@atlas-crm.com, sam@atlas-crm.com")

    assert response.status_code == 200
    assert "was just added" in response.content.decode()
    assert not User.objects.filter(email="kate@atlas-crm.com").exists()


def test_an_invite_already_used_to_sign_in_cannot_be_cancelled(atlas, pm):
    sam = User.objects.create_user("sam@atlas-crm.com", company=atlas)
    SignInLink.objects.create(user=sam, kind=LinkKind.INVITE, used_at=timezone.now())

    with pytest.raises(services.NotAllowedError):
        services.cancel_invite(sam)

    assert User.objects.filter(pk=sam.pk).exists()
