"""仅完整五分钟 K 线更新当前策略指标。"""
import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from strategy_python.mean_reversion_short_5min.features import EthFeatureModel
from tests.strategy_python.mean_reversion_short_5min.strategy_test import (
    MINUTE_BAR_TYPE,
    make_config,
)


def test_only_completed_five_minute_candles_update_boll_and_reset():
    instrument = TestInstrumentProvider.ethusdt_perp_binance()
    features = EthFeatureModel(make_config(bollinger_period=3))
    for minute in range(1, 16):
        px = instrument.make_price(90 + 10 * ((minute - 1) // 5))
        ts = minute * 60_000_000_000 - 1_000_000
        completed = features.aggregate(Bar(MINUTE_BAR_TYPE, px, px, px, px,
                                           instrument.make_qty(1), ts, ts))
        assert set(completed) == {5}
        candle = completed[5]
        if minute % 5:
            assert candle is None
        else:
            features.on_five_minute_bar(candle)
        if minute < 15:
            assert features.bollinger is None
    assert features.bollinger == pytest.approx((100 - 2 * (200 / 3) ** .5, 100, 100 + 2 * (200 / 3) ** .5))
    features.reset()
    assert features.bollinger is None and features.add_distance_mean is None
    assert features.ma_distance.current_ma is None



def test_minimum_bollinger_width_uses_240_complete_bars_and_expires_old_minimum():
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import completed_bar

    features = EthFeatureModel(make_config())
    # Controlled bands distinguish full width from half-width or normalized width.
    features._bollinger.update = lambda close: (100., 100. + close / 2, 100. + close)
    step = 300_000_000_000
    for i in range(1, 240):
        features.on_five_minute_bar(completed_bar(i*step, close=float(i), minutes=5))
    assert features.min_bollinger_width is None
    features.on_five_minute_bar(completed_bar(240*step, close=240., minutes=5))
    assert features.min_bollinger_width == 1.
    features.on_five_minute_bar(completed_bar(240*step, close=0., minutes=5))
    assert features.min_bollinger_width == 1. and len(features.bollinger_widths) == 240
    features.on_five_minute_bar(completed_bar(241*step, close=241., minutes=5))
    assert features.min_bollinger_width == 2.
    features.on_five_minute_bar(completed_bar(243*step, close=1., minutes=5))
    assert features.min_bollinger_width is None and len(features.bollinger_widths) == 1
    features.on_five_minute_bar(completed_bar(244*step, close=float('nan'), minutes=5))
    assert features.min_bollinger_width is None and not features.bollinger_widths
    features.reset()
    assert features.min_bollinger_width is None and not features.bollinger_widths


def test_width_window_starts_after_bollinger_warmup_and_restarts_after_gap():
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import completed_bar

    features = EthFeatureModel(make_config())
    step = 300_000_000_000
    for i in range(1, 259):
        features.on_five_minute_bar(completed_bar(i*step, close=100., minutes=5))
    assert features.min_bollinger_width is None and len(features.bollinger_widths) == 239
    features.on_five_minute_bar(completed_bar(259*step, close=100., minutes=5))
    assert features.min_bollinger_width == 0.
    features.on_five_minute_bar(completed_bar(261*step, close=100., minutes=5))
    assert features.bollinger is None and not features.bollinger_widths


def test_second_profit_distance_uses_negative_subset_p20_p70(monkeypatch):
    from collections import deque
    from tests.strategy_python.mean_reversion_short_5min.strategy_test import completed_bar

    features = EthFeatureModel(make_config())
    values = list(map(float, range(-10, 0))) + [0.] * 300 + [100.] * 266
    features.ma_distance.samples = deque(enumerate(values))
    monkeypatch.setattr(features.ma_distance, 'update', lambda timestamp, close: None)
    features.on_five_minute_bar(completed_bar(300_000_000_000, close=100., minutes=5))
    assert features.take_profit_distance_mean == -5.5
    features.ma_distance.samples = deque(enumerate([0.] * 576))
    features.on_five_minute_bar(completed_bar(600_000_000_000, close=100., minutes=5))
    assert features.take_profit_distance_mean is None
