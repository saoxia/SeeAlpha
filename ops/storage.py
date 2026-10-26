"""Validate the configured data directory without creating a local fallback."""

import argparse
import os
from pathlib import Path


def check_data_directory(project, volumes_root=Path("/Volumes")):
    data = Path(project) / "data"
    if not data.is_dir():
        raise RuntimeError(f"数据目录不可用，请先连接外置盘：{data}")
    resolved = data.resolve()
    if resolved.is_relative_to(volumes_root):
        volume = volumes_root / resolved.relative_to(volumes_root).parts[0]
        if not os.path.ismount(volume):
            raise RuntimeError(f"外置盘尚未挂载：{volume}")
    return resolved


def main():
    parser = argparse.ArgumentParser(description="检查数据目录和外置盘挂载，不创建目录")
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(check_data_directory(args.project))
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
