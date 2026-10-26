import hashlib
import json
import zipfile

import pytest

from ops import check_project


@pytest.fixture
def source_update(tmp_path, monkeypatch):
    monkeypatch.setattr(check_project, "HISTORICAL", ("study/manifest.json",))
    (tmp_path / "ops").mkdir()
    (tmp_path / "study").mkdir()
    (tmp_path / "ops/lint_baseline.json").write_text('{"files": {}}')
    original, current = b"original source", b"optimized source"
    old_hash = hashlib.sha256(original).hexdigest()
    (tmp_path / "shared.py").write_bytes(current)
    (tmp_path / "study/manifest.json").write_text(
        json.dumps({"source_sha256": {"shared.py": old_hash}})
    )
    (tmp_path / "ops/source_updates.json").write_text(
        json.dumps(
            {
                "shared.py": {
                    "previous_sha256": old_hash,
                    "sha256": hashlib.sha256(current).hexdigest(),
                }
            }
        )
    )
    with zipfile.ZipFile(tmp_path / "study/frozen_sources.zip", "w") as archive:
        archive.writestr("shared.py", original)
    (tmp_path / "study/archive.json").write_text(
        json.dumps(
            {
                "sha256": check_project.digest(tmp_path / "study/frozen_sources.zip"),
            }
        )
    )
    return tmp_path


def test_registered_shared_update_requires_exact_current_and_archived_sources(source_update):
    result = check_project.verify_sources(source_update)
    assert result["failures"] == []
    assert result["verified_source_updates"] == ["study/manifest.json: shared.py"]


@pytest.mark.parametrize("damage", ["current", "missing", "unregistered", "archive", "member"])
def test_shared_source_update_cannot_hide_other_changes(source_update, damage):
    if damage == "current":
        (source_update / "shared.py").write_text("unreviewed change")
    elif damage == "missing":
        (source_update / "shared.py").unlink()
    elif damage == "unregistered":
        (source_update / "ops/source_updates.json").unlink()
    elif damage == "archive":
        (source_update / "study/frozen_sources.zip").write_bytes(b"broken")
    else:
        with zipfile.ZipFile(source_update / "study/frozen_sources.zip", "w") as archive:
            archive.writestr("shared.py", b"wrong historical source")
        (source_update / "study/archive.json").write_text(
            json.dumps(
                {
                    "sha256": check_project.digest(source_update / "study/frozen_sources.zip"),
                }
            )
        )
    assert check_project.verify_sources(source_update)["failures"]
