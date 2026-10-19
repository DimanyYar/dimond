"""What must never end up inside the Docker image (PRD: access and security).

`COPY . .` copies the project into the image except what `.dockerignore` leaves out. The
test reads that file with Docker's rules (patterns from the project root, a folder pattern
covers everything inside, `**/` means any folder, `!` brings a path back, the last matching
line wins) and checks real paths against it.
"""

from fnmatch import fnmatchcase
from pathlib import Path

DOCKERIGNORE = Path(__file__).resolve().parent.parent / ".dockerignore"


def _patterns() -> list[str]:
    lines = (line.strip() for line in DOCKERIGNORE.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _matches(path: str, pattern: str) -> bool:
    parts = path.split("/")
    # A path is left out when it or any folder above it matches.
    for prefix in ("/".join(parts[: i + 1]) for i in range(len(parts))):
        if fnmatchcase(prefix, pattern):
            return True
        if pattern.startswith("**/") and fnmatchcase(prefix.split("/")[-1], pattern[3:]):
            return True
    return False


def goes_into_image(path: str) -> bool:
    included = True
    for line in _patterns():
        brings_back = line.startswith("!")
        pattern = line.removeprefix("!").rstrip("/")
        if _matches(path, pattern):
            included = brings_back
    return included


def test_secrets_never_go_into_the_docker_image():
    assert not goes_into_image(".env")  # Django secret key, database and Jira passwords
    assert not goes_into_image(".env.local")


def test_client_attachments_never_go_into_the_docker_image():
    assert not goes_into_image("uploads/2026/10/duplicates.png")
    assert not goes_into_image("uploads/previews/duplicates.webp")


def test_the_settings_template_and_the_code_still_go_into_the_image():
    assert goes_into_image(".env.example")
    assert goes_into_image("apps/tracker/jira.py")
    assert goes_into_image("templates/base.html")
    assert not goes_into_image("apps/tracker/__pycache__/jira.cpython-313.pyc")
