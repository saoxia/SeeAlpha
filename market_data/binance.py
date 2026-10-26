"""Public USD-M REST and WebSocket transport, for the project market-data service."""

import asyncio
import json
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import DataError, MinuteBar

REST_URL = "https://fapi.binance.com/fapi/v1/"



def parse_ws(item, symbols):
    """Validate a normalized native delivery, after the venue adapter decoded it."""
    if not isinstance(item, tuple) or len(item) != 3:
        raise DataError("Expected normalized native market data")
    symbol, event, bar = item
    if symbol not in symbols or type(event) is not int:
        raise DataError("Unexpected native market data identity")
    if bar is not None and (not isinstance(bar, MinuteBar) or bar.symbol != symbol
                            or event < bar.end_ms - 1):
        raise DataError("Invalid native closed minute")
    return symbol, event, bar


class PublicREST:
    def __init__(self, *, symbols=("BTCUSDT", "ETHUSDT"), timeout=12, attempts=4, minimum_interval=0.3):
        from .native import NativeMarketData
        self.source = NativeMarketData(symbols, timeout=timeout)
        self.timeout, self.attempts = timeout, attempts
        self.minimum_interval = minimum_interval
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    def _request(self, endpoint, params):
        request = Request(
            REST_URL + endpoint + "?" + urlencode(params),
            headers={"User-Agent": "Nautilus-unified-minute-feed/1"},
        )
        with urlopen(request, timeout=self.timeout) as response:
            if not response.url.startswith(REST_URL):
                raise DataError("Unexpected REST redirect")
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise DataError("Oversize REST response")
        result = json.loads(raw)
        if isinstance(result, dict) and "code" in result:
            raise DataError("Binance public API error: " + str(result))
        return result

    async def get(self, endpoint, **params):
        if endpoint != "time":
            raise ValueError("Only public time and klines are allowed")
        async with self.lock:
            for attempt in range(self.attempts):
                await asyncio.sleep(
                    max(0, self.last_request + self.minimum_interval - time.monotonic())
                )
                self.last_request = time.monotonic()
                try:
                    return await asyncio.to_thread(self._request, endpoint, params)
                except HTTPError as exc:
                    if exc.code not in {418, 429, 500, 502, 503, 504}:
                        raise
                    delay = max(min(2**attempt, 30), float(exc.headers.get("Retry-After", "1")))
                except (OSError, TimeoutError):
                    delay = min(2**attempt, 30)
                if attempt + 1 == self.attempts:
                    raise DataError("Public REST retries exhausted")
                await asyncio.sleep(delay)

    async def bars(self, symbol, start_ms, end_ms):
        return await self.source.bars(symbol, start_ms, end_ms)


class ServerClock:
    def __init__(self):
        self.base_ms = 0
        self.monotonic_at = 0.0
        self.uncertainty_ms = 0
        self.valid = False

    async def sync(self, rest):
        self.valid = False
        start = time.monotonic()
        result = await rest.get("time")
        end = time.monotonic()
        elapsed = int((end - start) * 1000)
        server = int(result["serverTime"])
        if elapsed > 2000 or server <= 0:
            raise DataError("Server clock roundtrip exceeds 2s")
        if self.base_ms and abs(server + elapsed // 2 - self.now()) > 5000:
            raise DataError("Server clock shifted by more than 5s")
        self.base_ms = server + elapsed // 2
        self.uncertainty_ms = elapsed // 2 + 1
        self.monotonic_at = end
        self.valid = True

    def now(self):
        if not self.base_ms:
            raise DataError("Clock not synchronized")
        return self.base_ms + int((time.monotonic() - self.monotonic_at) * 1000)

    def confirmed(self):
        return self.now() - self.uncertainty_ms

    @property
    def healthy(self):
        return self.valid and bool(self.base_ms) and time.monotonic() - self.monotonic_at < 120


class KlineSocket:
    """Subscription facade over a shared native data-only Binance node."""

    def __init__(self, symbols, on_message, on_reconnect, *, source=None):
        from .native import NativeMarketData
        self.source = source or NativeMarketData(symbols)
        self.source.on_message, self.source.on_reconnect = on_message, on_reconnect

    async def connect(self):
        await self.source.start()

    @property
    def active(self):
        return self.source.active

    async def close(self):
        await self.source.close()
