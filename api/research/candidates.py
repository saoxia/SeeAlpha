"""No-exec validation for research candidate ZIPs; Engine rechecks independently."""
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

_MAX_BYTES = 64 * 1024
_FILES = {"strategy.py", "seealpha-package.json"}
_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_ALLOWED = {"ema_cross", "ema_revert", "sma_cross", "sma_revert"}
_FIELDS = {"kind", "symbol", "fast_period", "slow_period", "allocation", "risk_limit"}


def parse_candidate(encoded: str) -> tuple[bytes, dict]:
    if not isinstance(encoded, str) or not 1 <= len(encoded) <= 90_000:
        raise ValueError("Invalid candidate package size")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not 1 <= len(raw) <= _MAX_BYTES:
            raise ValueError
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) != 2 or {item.filename for item in members} != _FILES:
                raise ValueError
            for item in members:
                if (
                    item.is_dir() or item.flag_bits & 1
                    or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK
                    or item.file_size > (8_192 if item.filename.endswith(".json") else 32_768)
                ):
                    raise ValueError
            metadata = json.loads(archive.read("seealpha-package.json").decode("utf-8"))
            source = archive.read("strategy.py").decode("utf-8")
        tree = ast.parse(source, filename="strategy.py")
        statements = tree.body
        if len(statements) == 2 and (isinstance(statements[0], ast.Expr)
                and isinstance(statements[0].value, ast.Constant)
                and isinstance(statements[0].value.value, str)):
            statements = statements[1:]
        if len(statements) != 1 or not isinstance(statements[0], ast.Assign):
            raise ValueError
        stmt = statements[0]
        if (len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name)
                or stmt.targets[0].id != "CANDIDATE"):
            raise ValueError
        recipe = ast.literal_eval(stmt.value)
        if (not isinstance(metadata, dict)
                or set(metadata) != {"format", "name", "title", "version", "entry_point"}
                or type(metadata["format"]) is not int or metadata["format"] != 1
                or not isinstance(metadata["name"], str)
                or not _ID.fullmatch(metadata["name"])
                or not isinstance(metadata["version"], str)
                or not _VERSION.fullmatch(metadata["version"])
                or metadata["entry_point"] != "strategy:CANDIDATE"
                or not isinstance(metadata["title"], str)
                or not 1 <= len(metadata["title"]) <= 100
                or not isinstance(recipe, dict) or set(recipe) != _FIELDS
                or recipe["kind"] not in _ALLOWED
                or not isinstance(recipe["symbol"], str)
                or not re.fullmatch(r"[A-Z0-9]{3,20}", recipe["symbol"])
                or type(recipe["fast_period"]) is not int
                or type(recipe["slow_period"]) is not int
                or not 2 <= recipe["fast_period"] < recipe["slow_period"] <= 500):
            raise ValueError
        from decimal import Decimal, InvalidOperation

        for label in ("allocation", "risk_limit"):
            value = recipe[label]
            if (not isinstance(value, str)
                    or not re.fullmatch(r"(?:0|[1-9][0-9]{0,20})(?:\.[0-9]{1,8})?", value)):
                raise ValueError
            try:
                number = Decimal(value)
            except (InvalidOperation, ValueError):
                raise ValueError from None
            if (not number.is_finite() or number <= 0
                    or number.as_tuple().exponent < -8 or len(number.as_tuple().digits) > 30):
                raise ValueError
            recipe[label] = format(number, "f")
        return raw, {
            "name": metadata["name"], "title": metadata["title"],
            "version": metadata["version"], "recipe": recipe,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    except (ValueError, TypeError, SyntaxError, UnicodeError, binascii.Error,
            KeyError, json.JSONDecodeError, zipfile.BadZipFile, RuntimeError,
            MemoryError, RecursionError, OverflowError):
        raise ValueError("Unsupported or unsafe candidate package") from None
