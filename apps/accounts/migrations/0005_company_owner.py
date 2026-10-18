import django.db.models.deletion
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def give_companies_to_the_first_pm(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Until now there was one PM; the clients that already exist are theirs."""
    Company = apps.get_model("accounts", "Company")
    User = apps.get_model("accounts", "User")
    if not Company.objects.exists():
        return
    # A PM whose access was revoked could not see the companies they were given.
    pm = (
        User.objects.filter(role="pm", is_active=True)
        .order_by("-is_superuser", "date_joined", "pk")
        .first()
    )
    if pm is None:
        raise RuntimeError(
            "Companies exist but there is no active PM to own them. "
            "Create one with `manage.py createsuperuser`, then migrate again."
        )
    Company.objects.update(owner=pm)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_invites"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clients",
                to="accounts.user",
            ),
        ),
        migrations.RunPython(give_companies_to_the_first_pm, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="company",
            name="owner",
            field=models.ForeignKey(
                help_text="The PM who works with this client. Other PMs never see it.",
                limit_choices_to={"is_active": True, "role": "pm"},
                on_delete=django.db.models.deletion.PROTECT,
                related_name="clients",
                to="accounts.user",
            ),
        ),
    ]
