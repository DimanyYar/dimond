from django.db import migrations, models

HELP = "The worker has taken it into a notification email (PRD: email notifications)."


class Migration(migrations.Migration):
    """Messages from before message emails existed count as emailed: nobody should get a pile
    of old news on the day these emails start."""

    dependencies = [
        ("requests", "0020_status_events_emailed"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="emailed",
            field=models.BooleanField(default=True, help_text=HELP),
        ),
        migrations.AlterField(
            model_name="message",
            name="emailed",
            field=models.BooleanField(default=False, help_text=HELP),
        ),
    ]
