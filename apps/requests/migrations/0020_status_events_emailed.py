from django.db import migrations, models

HELP = "The worker has taken it into a notification email, or decided none is due."


class Migration(migrations.Migration):
    """Status changes from before notification emails existed count as emailed: nobody should
    get a pile of old news on the day emails start."""

    dependencies = [
        ("requests", "0019_sent_to_jira"),
    ]

    operations = [
        migrations.AddField(
            model_name="statusevent",
            name="emailed",
            field=models.BooleanField(default=True, help_text=HELP),
        ),
        migrations.AlterField(
            model_name="statusevent",
            name="emailed",
            field=models.BooleanField(default=False, help_text=HELP),
        ),
    ]
