import os
import subprocess
import sys

from django.conf import settings


def start_django(**env: str) -> subprocess.CompletedProcess[str]:
    """Load the settings in a fresh Python, as the site does at start-up."""
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings", **env},
        capture_output=True,
        text=True,
        cwd=settings.BASE_DIR,
        check=False,
    )


def test_resend_with_the_local_sender_stops_the_site_from_starting():
    started = start_django(RESEND_API_KEY="re_test", DEFAULT_FROM_EMAIL="Dimon <dimon@localhost>")

    assert started.returncode != 0
    assert "DEFAULT_FROM_EMAIL" in started.stderr


def test_resend_with_a_real_sender_starts():
    started = start_django(
        RESEND_API_KEY="re_test", DEFAULT_FROM_EMAIL="dimon.d <no-reply@example.com>"
    )

    assert started.returncode == 0, started.stderr
