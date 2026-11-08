"""Transport contracts are importable without initializing the Engine runtime."""

import subprocess
import sys

from core.engine_contracts import AccountSnapshot, EngineConflict, QueryError
from engine.connectors.binance.queries import AccountSnapshot as EngineAccountSnapshot
from engine.connectors.binance.queries import QueryError as EngineQueryError
from engine.runtime import EngineConflict as RuntimeEngineConflict


def test_error_and_result_types_are_shared_across_api_and_engine():
    assert QueryError is EngineQueryError
    assert EngineConflict is RuntimeEngineConflict
    assert AccountSnapshot is EngineAccountSnapshot
    result = AccountSnapshot({"USDT": "1"}, {}, [], 123)
    assert result.observed_at == 123
    assert EngineConflict("rejected").message == "rejected"


def test_engine_client_does_not_eager_import_engine_runtime():
    script = (
        "import sys; import api.engine_client; "
        "assert 'engine.runtime' not in sys.modules; "
        "assert 'engine.connectors.binance.queries' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], check=False,
        capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
