"""Validate a full Python strategy ZIP without importing its source.

This is a Platform-side preflight, not a sandbox. The pinned development Engine
independently rechecks the exact archive hash before its Backtest Worker loads it.
"""
from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import io
import json
import re
import stat
import zipfile
from decimal import Decimal, InvalidOperation

_MAX_BYTES = 256 * 1024
_MAX_B64 = 400_000
_MAX_MEMBERS = 32
_SUFFIXES = {".py", ".json", ".md", ".txt"}
_NAME = re.compile(r"[a-z][a-z0-9_]{1,40}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}\Z")
_ENTRY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,8})?\Z")
_SECRET = frozenset({
    "api_key", "secret", "passphrase", "password", "token",
    "credential", "credentials",
})


def parse_python_candidate(encoded: str) -> tuple[bytes, dict]:
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= _MAX_B64:
        raise ValueError("Python candidate archive too large")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not 1 <= len(raw) <= _MAX_BYTES:
            raise ValueError
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (not 1 <= len(names) <= _MAX_MEMBERS
                    or len(set(names)) != len(names)
                    or "seealpha-package.json" not in names):
                raise ValueError
            total = 0
            for item in entries:
                path = item.filename.split("/")
                if (not item.filename or item.filename.startswith("/")
                        or "\\" in item.filename or ".." in path
                        or any(part.startswith("strategy_python") for part in path)
                        or item.flag_bits & 1
                        or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK):
                    raise ValueError
                if not item.is_dir() and not any(
                    item.filename.lower().endswith(ext) for ext in _SUFFIXES
                ):
                    raise ValueError
                total += item.file_size
                if total > _MAX_BYTES:
                    raise ValueError
            metadata = json.loads(archive.read("seealpha-package.json").decode("utf-8"))
            if not isinstance(metadata, dict) or metadata.get("format") != 1:
                raise ValueError
            name, version, entry = (
                metadata.get("name"), metadata.get("version"), metadata.get("entry_point"),
            )
            if (not isinstance(name, str) or not _NAME.fullmatch(name)
                    or not isinstance(version, str) or not _VERSION.fullmatch(version)
                    or not isinstance(entry, str) or not _ENTRY.fullmatch(entry)):
                raise ValueError
            module, _, classname = entry.partition(":")
            filename = module.replace(".", "/") + ".py"
            if filename not in names:
                raise ValueError
            for filename_py in (n for n in names if n.endswith(".py")):
                content = archive.read(filename_py).decode("utf-8")
                if "strategy_python" in content:
                    raise ValueError
                tree = ast.parse(content, filename=filename_py)
                if filename_py == filename and not any(
                    isinstance(node, ast.ClassDef) and node.name == classname
                    for node in tree.body
                ):
                    raise ValueError
            params = metadata.get("parameters")
            if (not isinstance(params, dict) or not params or len(params) > 24
                    or set(params) & _SECRET
                    or not isinstance(params.get("symbol"), str)
                    or re.fullmatch(r"[A-Z0-9]{3,20}", params["symbol"]) is None):
                raise ValueError
            for key, value in params.items():
                if (not isinstance(key, str)
                        or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", key)
                        or type(value) not in (str, int)
                        or (isinstance(value, str) and len(value) > 128)
                        or (type(value) is int and not -1_000_000_000 <= value <= 1_000_000_000)):
                    raise ValueError
            for key in ("allocation", "risk_limit"):
                number = params.get(key)
                if not isinstance(number, str) or not _DECIMAL.fullmatch(number):
                    raise ValueError
                if not Decimal(number).is_finite() or Decimal(number) <= 0:
                    raise ValueError
            title = metadata.get("title", name)
            if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
                raise ValueError
        return raw, {
            "name": name, "title": title.strip(), "version": version,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "recipe": {
                "mode": "python", "entry_point": entry,
                "parameters": params,
            },
        }
    except (ValueError, TypeError, OverflowError, SyntaxError, UnicodeError,
            json.JSONDecodeError, binascii.Error, zipfile.BadZipFile,
            RuntimeError, MemoryError, RecursionError, InvalidOperation):
        raise ValueError("Invalid full Python candidate archive") from None
