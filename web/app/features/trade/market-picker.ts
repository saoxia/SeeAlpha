import type { MarketSummary } from './api.ts';
export type MarketSort = 'symbol' | 'quote_volume' | 'last_price' | 'change_percent' | 'funding_rate';
export function selectMarkets(rows: MarketSummary[], query: string, quote: string, favorites: string[], onlyFavorites: boolean, sort: MarketSort, descending: boolean) {
  const search = query.trim().toUpperCase().replace(/[\s/:-]/g,'');
  return rows.filter(row => (!quote || row.quote === quote) && (!onlyFavorites || favorites.includes(row.symbol)) && (row.base+row.quote).includes(search))
    .sort((a,b) => {
      if (sort === 'symbol') return (descending ? -1 : 1)*a.symbol.localeCompare(b.symbol);
      const left = a[sort], right = b[sort];
      if (left === null || right === null) return left === right ? a.symbol.localeCompare(b.symbol) : left === null ? 1 : -1;
      return (descending ? -1 : 1)*(Number(left)-Number(right)) || a.symbol.localeCompare(b.symbol);
    });
}
export function compactVolume(value: string | null) {
  if (value === null) return '—';
  const n=Number(value);
  for (const [scale,suffix] of [[1e9,'B'],[1e6,'M'],[1e3,'K']] as const) if (n>=scale) return `${(n/scale).toFixed(2)}${suffix}`;
  return n.toLocaleString('en-US',{maximumFractionDigits:2});
}
