"""Developer tooling must work independently of the data directory."""

import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("shell", ["/bin/bash", "/bin/zsh"])
@pytest.mark.parametrize("dangling_link", [False, True])
def test_activation_without_available_data_uses_local_builds(tmp_path, shell, dangling_link):
    activation = tmp_path / "activate.sh"
    activation.write_bytes((PROJECT / "activate.sh").read_bytes())
    data = tmp_path / "data"
    if dangling_link:
        data.symlink_to(tmp_path / "unavailable/data", target_is_directory=True)
    result = subprocess.run(
        [
            shell,
            "-c",
            'source "$1" && printf "%s\n%s\n%s" "$CARGO_HOME" "$UV_CACHE_DIR" "$XDG_CACHE_HOME"',
            "activate-test",
            str(activation),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # The Rust converter target now lives in the independent Engine checkout.
    # Platform activation must keep its own tools and caches project-local.
    assert result.stdout.splitlines() == [
        str(tmp_path / ".local/cargo"),
        str(tmp_path / ".local/cache/uv"),
        str(tmp_path / ".local/cache"),
    ]
    assert not data.exists()
    assert data.is_symlink() == dangling_link
    assert not (tmp_path / "unavailable").exists()
