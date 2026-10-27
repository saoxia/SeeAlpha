import type { ExchangeMarket, MarketType } from "./exchange-account-types";

type ExchangeMarketSelectorProps = {
  markets: ExchangeMarket[];
  marketType: MarketType;
  search: string;
  selectedSymbols: string[];
  loading: boolean;
  error: string;
  onMarketTypeChange: (marketType: MarketType) => void;
  onSearchChange: (search: string) => void;
  onToggleSymbol: (symbol: string) => void;
};

const marketTabs: ReadonlyArray<readonly [MarketType, string]> = [
  ["spot", "现货"],
  ["linear", "U 本位合约"],
  ["option", "U 本位期权"],
];

export function ExchangeMarketSelector({
  markets,
  marketType,
  search,
  selectedSymbols,
  loading,
  error,
  onMarketTypeChange,
  onSearchChange,
  onToggleSymbol,
}: ExchangeMarketSelectorProps) {
  const normalizedSearch = search.trim().toUpperCase();
  const visibleMarkets = markets
    .filter((market) => market.market_type === marketType)
    .filter((market) => !normalizedSearch || market.symbol.includes(normalizedSearch))
    .slice(0, 120);

  return (
    <section className="exchange-market-picker field-wide" aria-label="交易对选择">
      <div className="exchange-market-picker-head">
        <div><b>交易对</b><small>已选择 {selectedSymbols.length}/30；操作记录将只查询这些市场。</small></div>
        <input aria-label="搜索交易对" value={search} onChange={(event) => onSearchChange(event.target.value.toUpperCase())} placeholder="搜索 BTC / ETH / USDT" />
      </div>
      <div className="exchange-market-tabs" role="tablist" aria-label="市场类型">
        {marketTabs.map(([id, label]) => (
          <button key={id} type="button" role="tab" disabled={id !== "linear"} title={id !== "linear" ? "该市场尚未接入当前账户服务" : undefined} aria-selected={marketType === id} className={marketType === id ? "active" : ""} onClick={() => onMarketTypeChange(id)}>{label}</button>
        ))}
      </div>
      {loading && <p>正在读取市场列表…</p>}
      {error && <p className="error">{error}</p>}
      {!loading && !error && (
        <div className="exchange-market-list">
          {visibleMarkets.map((market) => (
            <label key={market.symbol}>
              <input type="checkbox" checked={selectedSymbols.includes(market.symbol)} onChange={() => onToggleSymbol(market.symbol)} />
              <span>{market.symbol}</span>
              <small>{market.base}/{market.quote}</small>
            </label>
          ))}
          {visibleMarkets.length === 0 && <p>没有匹配的交易对。</p>}
        </div>
      )}
      {selectedSymbols.length > 0 && (
        <div className="exchange-market-selected">
          {selectedSymbols.map((symbol) => <button type="button" key={symbol} onClick={() => onToggleSymbol(symbol)}>{symbol}<span>×</span></button>)}
        </div>
      )}
    </section>
  );
}
