"""Read existing historical Catalog by UTC day; never rewrite research data."""

from decimal import Decimal
from pathlib import Path

from pipeline.models import bar_type_for

from .models import DAY, MINUTE, DataError, MinuteBar


class CatalogHistory:
    def __init__(self, path):
        self.path = Path(path)

    def read_day(self, symbol, start_ms, end_ms):
        if not self.path.exists():
            return []
        from pipeline.catalog import ParquetDataCatalog

        catalog = ParquetDataCatalog(str(self.path))
        rows = catalog.bars(
            bar_types=[bar_type_for(symbol, "1m")],
            start=start_ms * 1_000_000,
            end=end_ms * 1_000_000 - 1,
        )
        result = []
        for bar in rows:
            close_ms, remainder = divmod(bar.ts_event, 1_000_000)
            if remainder or (close_ms + 1) % MINUTE:
                raise DataError("Catalog timestamp convention differs from close-minus-1ms")
            start = close_ms + 1 - MINUTE
            if start_ms <= start < end_ms:
                result.append(
                    MinuteBar(
                        symbol,
                        start,
                        *(
                            Decimal(str(v))
                            for v in (bar.open, bar.high, bar.low, bar.close, bar.volume)
                        ),
                    )
                )
        return result

    def chunks(self, start_ms, end_ms):
        cursor = start_ms
        while cursor < end_ms:
            end = min((cursor // DAY + 1) * DAY, end_ms)
            yield cursor, end
            cursor = end
