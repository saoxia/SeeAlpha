import { ClickhouseFreshnessMonitor } from "./clickhouse-freshness-monitor";

export function DataOpsPage() {
  return <section className="dataops-page" id="data-ops">
    <div className="dataops-heading"><div><span>DATA OPERATIONS</span><h1>Data Ops 监控</h1><p>跟踪 ClickHouse 数据新鲜度。</p></div></div>
    <ClickhouseFreshnessMonitor />
  </section>;
}
