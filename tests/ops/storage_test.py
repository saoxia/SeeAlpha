from pathlib import Path

import pytest

from ops.storage import check_data_directory


def test_missing_external_data_never_creates_a_local_fallback(tmp_path):
    data = tmp_path / "data"
    data.symlink_to(tmp_path / "unmounted/data", target_is_directory=True)
    with pytest.raises(RuntimeError, match="数据目录不可用"):
        check_data_directory(tmp_path)
    assert data.is_symlink()
    assert not data.exists()
    assert not (tmp_path / "unmounted").exists()


def test_existing_directory_under_unmounted_volume_is_rejected(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    volumes = tmp_path / "Volumes"
    target = volumes / "disk/project/data"
    target.mkdir(parents=True)
    (project / "data").symlink_to(target, target_is_directory=True)
    monkeypatch.setattr("ops.storage.os.path.ismount", lambda path: False)
    with pytest.raises(RuntimeError, match="外置盘尚未挂载"):
        check_data_directory(project, volumes)
    monkeypatch.setattr("ops.storage.os.path.ismount", lambda path: path == volumes / "disk")
    assert check_data_directory(project, volumes) == target


def test_local_data_is_supported_for_other_developers(tmp_path):
    (tmp_path / "data").mkdir()
    assert check_data_directory(tmp_path) == Path(tmp_path) / "data"
