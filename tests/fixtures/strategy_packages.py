"""Portable test strategy ZIP fixtures with no Engine or Nautilus imports.

The generator mirrors the Engine's deterministic allowlisted ZIP member layout,
but does not validate strategy source. Engine behavior tests additionally assert
that the resulting ZIP bytes equal the production archive_directory output.
"""

import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
EXAMPLE = PROJECT / "packages" / "ema_crossover"
ALLOWED_SUFFIXES = {".py", ".json", ".md", ".txt"}


def zip_strategy_directory(root):
    """Generate stable test archive bytes; real parsing/loading belongs to Engine."""
    root = Path(root)
    if not (root / "seealpha-package.json").is_file():
        raise ValueError("Test package lacks seealpha-package.json")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        paths = [
            path for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
        for path in sorted(paths):
            relative = path.relative_to(root).as_posix()
            if Path(relative).suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            info = zipfile.ZipInfo(relative)
            info.date_time = (2026, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


CLOSES = [100] * 8 + [101, 102, 104, 108, 116, 130, 120, 100, 80, 60, 40]


def crossover_bars():
    bars = []
    previous = CLOSES[0]
    for close in CLOSES:
        high = max(previous, close) + Decimal("0.5")
        low = min(previous, close) - Decimal("0.5")
        bars.append({
            "open": format(Decimal(previous), "f"),
            "high": format(high, "f"),
            "low": format(low, "f"),
            "close": format(Decimal(close), "f"),
        })
        previous = close
    return bars


def ema_archive(version="1"):
    if version == "1":
        return zip_strategy_directory(EXAMPLE)
    return _rewritten_archive(EXAMPLE, version=version)


def _rewritten_archive(root, *, version):
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        dest = Path(directory) / "pkg"
        dest.mkdir()
        for path in root.iterdir():
            if path.is_file():
                (dest / path.name).write_bytes(path.read_bytes())
        document = json.loads((dest / "seealpha-package.json").read_text(encoding="utf-8"))
        document["version"] = version
        (dest / "seealpha-package.json").write_text(json.dumps(document), encoding="utf-8")
        return zip_strategy_directory(dest)

def hold_archive(root, quantity="1"):
    root.mkdir()
    (root / "seealpha-package.json").write_text(json.dumps({
        "format": 1,
        "name": "hold_flat" if quantity == "0" else "hold_long",
        "title": "固定持仓",
        "version": "1",
        "entry_point": "strategy:Hold",
        "parameters": {"symbol": "ETHUSDT", "allocation": "1", "risk_limit": "1"},
    }), encoding="utf-8")
    (root / "strategy.py").write_text(
        "from decimal import Decimal\n"
        "from nautilus_trader.trading import Strategy\n"
        "def prepare(parameters):\n"
        "    return {'symbol': 'ETHUSDT', 'allocation': '1', 'risk_limit': '1'}\n"
        "class Hold(Strategy):\n"
        "    def __init__(self, config):\n"
        "        super().__init__(config)\n"
        "        self._sent = False\n"
        "    def on_start(self):\n"
        "        self.subscribe_bars(self.bar_type)\n"
        "    def on_bar(self, bar):\n"
        "        if self._sent:\n"
        "            return\n"
        "        self._sent = True\n"
        f"        self.publish(symbol='ETHUSDT', quantity=Decimal('{quantity}'))\n",
        encoding="utf-8",
    )
    return zip_strategy_directory(root)
