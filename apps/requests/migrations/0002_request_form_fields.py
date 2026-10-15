from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("requests", "0001_requests_and_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="request",
            name="steps",
            field=models.TextField(blank=True, help_text="Steps to reproduce; bugs only."),
        ),
        migrations.AddField(
            model_name="request",
            name="link",
            field=models.URLField(blank=True, help_text="Required for bugs.", max_length=500),
        ),
        # Requests made before the form existed never said how urgent they are.
        migrations.AddField(
            model_name="request",
            name="when_needed",
            field=models.CharField(
                choices=[
                    ("hotfix", "Hotfix"),
                    ("this_release", "This release"),
                    ("next_release", "Next release"),
                    ("nice_to_have", "Nice to have"),
                    ("not_sure", "Not sure"),
                ],
                default="not_sure",
                max_length=20,
            ),
            preserve_default=False,
        ),
    ]
