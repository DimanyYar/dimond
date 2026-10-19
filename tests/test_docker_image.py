"""What must never end up inside the Docker image (PRD: access and security)."""

from pathlib import Path

DOCKERIGNORE = Path(__file__).resolve().parent.parent / ".dockerignore"


def test_secrets_and_client_files_stay_out_of_the_docker_image():
    ignored = {line.strip() for line in DOCKERIGNORE.read_text().splitlines() if line.strip()}

    assert ".env" in ignored  # Django secret key, database and Jira passwords
    assert ".env.*" in ignored
    assert "uploads/" in ignored  # clients' attachments
