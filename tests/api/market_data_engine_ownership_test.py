"""Legacy cross-module lock integration: Engine and Platform use separate locks.

This is intentionally retained in the combined test suite until the independent
Engine can be tested with the same temporary locks over a real process boundary.
"""

from types import SimpleNamespace

from api.market_data.subscriptions import Subscriptions


def test_platform_policy_lock_never_blocks_engine_feed_owner_lock(tmp_path):
    from engine.data_manager.service import DataService as EngineDataService

    manager = SimpleNamespace(project=tmp_path)
    platform = Subscriptions(manager)
    engine = EngineDataService("42", project=tmp_path, state_root=tmp_path / "state")
    try:
        platform._claim()
        engine.subscriptions._claim()
        assert platform.lease is not None
        assert engine.subscriptions.lease is not None
        assert (tmp_path / ".local/run/api/market_policy_writer.lock").is_file()
        assert (tmp_path / ".local/run/api/subscriptions.lock").is_file()
    finally:
        platform.close()
        engine.close()
