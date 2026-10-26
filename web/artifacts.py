"""Published report paths and file-access boundaries shared by web tasks."""

from pathlib import Path

BACKTESTS = Path("data/reports/backtest")
REPORT_FILES = {
    "report.html",
    "summary.json",
    "parameters.json",
    "account.csv",
    "fills.csv",
    "positions.csv",
}


def artifact_path(project, relative):
    """Only published reports/logs may be served; never expose the project tree."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise FileNotFoundError
    path = project / relative
    resolved = path.resolve()
    # Only the data root may be a symlink. Descendants must stay literal so a
    # published filename cannot redirect to another file on the external disk.
    if relative.parts and relative.parts[0] == "data":
        data_root = (project / "data").resolve()
        expected = data_root.joinpath(*relative.parts[1:])
        if resolved != expected or not resolved.is_relative_to(data_root):
            raise FileNotFoundError
    elif resolved != path or not resolved.is_relative_to(project):
        raise FileNotFoundError
    allowed = (
        relative.is_relative_to(BACKTESTS)
        and relative.name in REPORT_FILES
        and (
            len(relative.parts) == 6
            or (len(relative.parts) == 7 and relative.parts[3] == "portfolio")
            or (
                len(relative.parts) == 8
                and relative.parts[3] == "portfolio"
                and relative.parts[-2] == "reconciled"
            )
        )
    ) or (
        relative.parent.parent == Path("data/project_checks")
        and relative.name in {"results.json", "lint.log", "tests.log", "sources.log"}
    )
    if not allowed or not resolved.is_file():
        raise FileNotFoundError
    return resolved
