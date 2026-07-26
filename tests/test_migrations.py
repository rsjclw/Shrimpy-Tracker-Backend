"""Structural checks on the alembic revision chain."""
import re
from pathlib import Path

import pytest

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
# alembic_version.version_num is varchar(32). A longer id runs the whole
# migration successfully and then fails on the final UPDATE, which is a
# miserable way to find out - on a production database.
MAX_REVISION_LENGTH = 32

REVISION_PATTERN = re.compile(r'^revision(?::\s*str)?\s*=\s*["\'](.+?)["\']', re.M)


def revision_files() -> list[Path]:
    return sorted(path for path in VERSIONS_DIR.glob("*.py"))


def revision_id(path: Path) -> str:
    match = REVISION_PATTERN.search(path.read_text(encoding="utf-8"))
    assert match, f"{path.name} has no revision id"
    return match.group(1)


def test_there_are_migrations_to_check():
    assert revision_files()


@pytest.mark.parametrize("path", revision_files(), ids=lambda p: p.name)
def test_revision_id_fits_the_version_column(path: Path):
    identifier = revision_id(path)

    assert len(identifier) <= MAX_REVISION_LENGTH, (
        f"revision id {identifier!r} is {len(identifier)} chars; "
        f"alembic_version.version_num holds {MAX_REVISION_LENGTH}"
    )


def test_revision_ids_are_unique():
    identifiers = [revision_id(path) for path in revision_files()]

    assert len(identifiers) == len(set(identifiers))
