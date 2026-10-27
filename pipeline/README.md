# `pipeline` 数据准备模块

本目录保存 SeeAlpha 的数据获取、转换、校验和准备代码。`data/` 目录只保存运行时
数据，不放 Python 代码。

策略代码不得直接下载、修复或转换历史数据；回测、研究和后续实盘统一通过
`prepare_data()` 使用本模块。

本模块当前实现 Binance USD-M 永续合约数据准备；这是现阶段接入范围，
不代表 SeeAlpha 仅面向加密资产。其他市场与交易产品需要独立接入及验证。

## 当前处理流程

```text
DataRequest
    ↓
规划 Binance UTC 日归档
    ↓
下载 ZIP + CHECKSUM / 复用本地有效归档
    ↓
校验 trades CSV、Trade ID、时间和价格数量
    ↓
分块转换成 Nautilus TradeTick，并从同一成交流聚合多周期 Bar
    ↓
写入 ParquetDataCatalog
    ↓
验证每个 UTC 日归档均已完整物化
    ↓
按周期生成独立的行情指标 Parquet（已有有效结果直接复用）
    ↓
PreparedDataset
```

默认数据集是 Binance USD-M 永续合约的逐笔 `trades`，交易对为 `BTC-USDT`、
`ETH-USDT`、`SOL-USDT`，并固定使用日归档。配置中的带连字符写法会在内部规范化为 Binance
的 `BTCUSDT`、`ETHUSDT`、`SOLUSDT`。所有 K 线都从这份成交流聚合，统一准备入口不会下载
Binance `klines` 归档。

选择日归档是为了避免同一时间范围先由日包写入、之后又由月包重复写入 Catalog。
月包可以在数据规模需要时作为后续优化加入。

## 子文件用途

| 文件 | 用途 |
|---|---|
| `__init__.py` | 将目录声明为 Python 包。 |
| `README.md` | 说明目录结构、职责边界、命令和扩展规则。 |
| `binance_sources.toml` | 声明三个永续交易对、从 trades 聚合的 Bar 周期及运行时路径。 |
| `models.py` | 定义 `DataRequest`、`PreparedDataset`、归档模型，以及 InstrumentId/BarType 命名规则。 |
| `config.py` | 读取并验证 TOML，将相对路径统一解析到项目根目录。 |
| `sync_binance_file.py` | 同步单个官方归档，负责 HTTPS 限制、重试、断点续传、SHA-256 和原子替换。 |
| `sync_binance_batch.py` | 把交易对和日期范围展开成日归档，并批量调用单文件同步。 |
| `validate_data.py` | 校验 trades/klines CSV、时间、Trade ID、价格数量、OHLCV 和 Catalog 范围。 |
| `rust_trade_converter.py` | 调用并校验 Rust 转换器，管理 Python/Rust 边界和 staging manifest。 |
| `convert_binance_trades.py` | 调度 Rust 流式转换并原子安装结果；Rust 不可用时保留 Python 分块回退。 |
| `convert_binance_bars.py` | 保留单个旧 kline 归档转换及共享状态工具；闲置的批量封装已移除，统一准备入口不再调用它下载或生成 K 线。 |
| `bar_features.py` | 从 trades 补建扩展行情和滚动指标，按 UTC 日保存，自带续算状态、失效检查和按列读取。 |
| `prepare_data.py` | 对外统一入口；按顺序执行同步、合约定义准备、转换和最终验证。 |

`rust_converter/` 保存 Rust trades 转换器的源码、依赖声明和锁文件，Python 管道通过
`rust_trade_converter.py` 调用它。Python 运行产生的
`__pycache__/` 只是解释器缓存，已被 Git 忽略，不属于项目模块。

## 运行时数据目录

这些目录由管道按需创建，并已被项目的 `.gitignore` 忽略：

```text
data/
├── README.md              # 运行时目录的职责、保留和重建规则
├── raw/
│   └── binance/           # Binance ZIP 与配套 CHECKSUM，保留远端目录结构
├── catalog/               # Nautilus ParquetDataCatalog
└── pipeline_state/
    └── binance/
        ├── converted/     # Checksum、转换版本、时间范围和完成状态
        └── failures/      # 校验错误清单，不复制原始 ZIP
```

### 数据职责

- `data/raw/binance/` 是可重新生成派生数据的原始来源，保存下载的 ZIP 与配套 CHECKSUM，
  转换后继续保留。下载中的隐藏 `.part` 文件放在正式归档同一目录，无需独立暂存目录。
- `data/catalog/` 是回测和策略读取的物化结果。
- `data/pipeline_state/binance/` 只记录管道幂等状态，不作为行情事实来源；它必须与
  Catalog 一起保留或一起清空。
- `data/pipeline_state/binance/failures/` 保存源路径、Checksum、转换器版本、失败时间、
  错误类型和错误信息；原始 ZIP 始终留在 `data/raw/binance/`，不会生成失败副本。

## 配置

默认配置在 `binance_sources.toml`：

```toml
[dataset]
market = "futures_um"
data_type = "trades"
symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

[bars]
intervals = ["1s", "1m", "5m", "15m"]
```

`prepare_data()` 只规划 Binance `trades` 日归档，默认补齐 `TradeTick` 和请求的 Bar
周期；没有指定 `bar_intervals` 时才使用 `[bars].intervals` 的全部周期。已有数据直接
复用，新增周期不会重建已有 TradeTick 或其他周期。补高周期时优先从已有、可整除目标
周期的低周期 Bar 聚合；没有合适的低周期数据时才解析 trades，只写入缺失的 Bar。

### Rust 转换核心

生产转换器位于 `pipeline/rust_converter/`。它只保留一个固定大小的 Trade
RecordBatch 和每个 Bar 周期的聚合结果，不创建逐条 Python 对象；一个日归档最终
写成一个 TradeTick Parquet 和每周期一个 Bar Parquet。首次使用前构建 release 二进制：

```bash
cargo build --release \
  --manifest-path pipeline/rust_converter/Cargo.toml
```

管道会自动发现默认 release 二进制。部署到其他位置时设置
`NAUTILUS_RUST_CONVERTER=/absolute/path/to/nautilus-data-converter`。需要排查差异或
临时使用旧 Python 路径时，设置 `NAUTILUS_RUST_CONVERTER=off`。

Rust 先把完整结果写入 Catalog 根目录内的临时 staging 目录；Python 保存
`in_progress` 状态后才逐个原子安装文件。校验失败或 Rust 异常时，staging 会被清理，
不会留下可被 Catalog 误读的半成品。

## 常用命令

以下命令均在项目根目录执行，直接使用项目自带的虚拟环境，不依赖系统安装
`uv`。也可以先执行 `source ./activate.sh`，之后继续使用 `uv run python ...`。

只查看将要使用的 Binance 文件：

```bash
.venv/bin/python -m pipeline.sync_binance_batch \
  --start 2026-08-01 \
  --end 2026-08-03 \
  --symbols ETH-USDT BTC-USDT \
  --plan-only
```

只同步原始归档：

```bash
.venv/bin/python -m pipeline.sync_binance_batch \
  --start 2026-08-01 \
  --end 2026-08-03 \
  --symbols ETH-USDT BTC-USDT
```

完成同步、转换和 Catalog 校验：

```bash
.venv/bin/python -m pipeline.prepare_data \
  --start 2026-08-01 \
  --end 2026-08-03 \
  --symbols ETH-USDT BTC-USDT
```

时间范围采用左闭右开语义，上例包含 `2026-08-01` 和 `2026-08-02` 两个 UTC
自然日。请求按 UTC 日物化，不要求调用方时间与 K 线周期对齐。

默认情况下，本地 ZIP 和 CHECKSUM 校验通过后不会访问网络。若需要检查 Binance
是否修正过历史归档：

```bash
.venv/bin/python -m pipeline.prepare_data \
  --start 2026-08-01 \
  --end 2026-08-03 \
  --symbols ETH-USDT \
  --refresh-archives
```

显式重建一天的 TradeTick 和 1m Bar（不会重写 1s、5m、15m）：

```bash
.venv/bin/python -m pipeline.prepare_data \
  --start 2026-08-01 --end 2026-08-02 \
  --symbols ETH-USDT --bar-intervals 1m --force
```

## Python 调用

```python
from datetime import UTC, datetime

from pipeline.models import DataRequest
from pipeline.prepare_data import prepare_data


dataset = prepare_data(
    DataRequest(
        symbols=("ETH-USDT",),
        data_type="trades",
        start=datetime(2026, 8, 1, tzinfo=UTC),
        end=datetime(2026, 8, 3, tzinfo=UTC),
    ),
)
```

`PreparedDataset` 只描述 Catalog 路径、数据类型、InstrumentId、BarType 和覆盖范围，
不会把数百万条 TradeTick 装进内存。调用方随后按时间范围从 Catalog 查询。
`feature_bar_types` 描述本次已准备指标的周期，与请求的 `bar_types` 一致。

## 行情与指标 Parquet

`prepare_data()` 默认同时补齐请求周期的指标。标准 `TradeTick` / `Bar` 保持原来的
格式；指标独立保存在 `data/catalog/features/<BarType>/data/custom/BarFeatures/`，每个 UTC
日一个 Parquet。仅调用 `catalog.bars()` 不会读取指标文件。低层的
`convert_trade_archive()` 仍然只转换行情；需要完整结果时使用 `prepare_data()`。

保存以下 25 个字段，另带 `instrument_id`、`bar_type`、`type`、`ts_event`、`ts_init`：

```text
open high low close volume quote_volume
taker_buy_volume taker_sell_volume
taker_buy_quote_volume taker_sell_quote_volume
delta_volume delta_quote_volume trade_count
atr14 natr14 std20
boll_mid boll_upper boll_lower boll_width boll_pos
ret_5 ma99 buy_ratio delta_ratio
```

| 字段 | 固定口径（特征版本 1） |
|---|---|
| `volume` / `quote_volume` | 基础币数量 / 报价币成交额；成交额累加 trades 的原始 `quote_quantity`，不以收盘价近似。 |
| `taker_buy_*` / `taker_sell_*` | `buyer_maker=False` 为主动买入，`True` 为主动卖出；卖量等于总量减买量。 |
| `delta_*` | 主动买入量减主动卖出量，可为负。 |
| `trade_count` | 按现有管道规则去重后的逐笔成交数，整数。 |
| `atr14` | TR = max(high-low, abs(high-前收盘), abs(low-前收盘))；段首 TR=high-low，前 14 个 TR 的均值作为种子，随后 Wilder 递推 `(13*前ATR+TR)/14`。 |
| `natr14` | `100 * atr14 / close`，百分数。 |
| `std20` | 当前及过去共 20 个收盘价的总体标准差，`ddof=0`。 |
| `boll_mid` | 20 周期简单移动平均。 |
| `boll_upper` / `boll_lower` | `boll_mid ± 2*std20`。 |
| `boll_width` | `(boll_upper-boll_lower)/boll_mid`，比例而非百分数。 |
| `boll_pos` | `(close-boll_lower)/(boll_upper-boll_lower)`，不裁剪到 [0,1]。 |
| `ret_5` | `close(t)/close(t-5)-1`，过去 5 根 K 线的简单收益率，比例而非百分数。 |
| `ma99` | 当前及过去共 99 个收盘价的简单移动平均，没有 `ma120`。 |
| `buy_ratio` / `delta_ratio` | 基础币口径的 `taker_buy_volume/volume` 和 `delta_volume/volume`。 |

每个周期独立计算；高周期指标不从低周期指标求平均。指标包含当前已闭合 K 线，
`ts_event` / `ts_init` 都采用标准 Bar 的收盘边界前 1ms，不可用于该根 K 线收盘前决策。
预热不足和除零结果写成 Parquet NULL；不前视、不回填。缺失 K 线时不伪造行情，
而是重置滚动状态重新预热，这一点需要在策略接入时与原策略缺口处理口径比较。

连续 UTC 日通过同名 `.metadata.json` 中的滚动状态续算，包含 MA 历史收盘价和 Wilder ATR
状态。若前一天未准备该周期的指标，则从本次首根 K 线预热，不会自动下载更早历史。
需要开头即有指标时，应将准备范围向前扩展。补写更早日期、重新生成标准 Bar 或改变
算法版本会使相关后续缓存失效；从最早失效日开始重新运行 `prepare_data` 至所需结束日
即可补建。范围外文件不自动重写；安全读取接口发现失效依赖时会报错，不静默返回旧指标。

指标结果先写临时文件，再原子替换。元数据保存源归档 checksum、Bar 文件指纹、特征
版本和前一天依赖。普通调用复用有效缓存；`--force` 同时重建请求范围的行情和指标。

当前生产路径分块扫描原生 TradeTick 和原始 quote 补充列，一次扫描覆盖当天所有待建周期；
Rust 和 Python 回退均在首次转换时保留 quote_quantity，特征生成不再二次解压 CSV。
CSV 数值由解析器直接读取，每块只规范化一次；主动买卖标志按不同取值校验。
多周期先按请求周期的最大公约数聚合，再由中间桶汇总高周期，避免重复扫描全部成交。
滚动指标仍在每个缺口重新预热，其余派生列整天统一计算，减少短片段的建表开销。
浮点汇总顺序变化可能产生末位舍入差异，不改变指标定义；成交计数保持精确。
策略目前仍使用原有实时计算逻辑，此改动提供缓存，尚未自动切换策略指标来源。

以下为历史性能测量记录，当前不再提供独立基准命令。
2026-09-28 在本机 245 万笔真实成交、1s/1m/5m/15m 四周期上的对照结果：
读取至指标计算约 51.86 秒降至 8.88 秒，CPU 时间约 51.82 秒降至 8.87 秒，
峰值内存约 472 MiB 降至 396 MiB。该测量不含下载、Rust TradeTick 转换及 Catalog
写入，不能视为整个 `prepare_data()` 的加速倍数；不同成交密度和缺口数量会影响结果。

按列读取（包含缓存失效检查，时间范围左闭右开）：

```python
from datetime import UTC, datetime
from pipeline.catalog import ParquetDataCatalog
from pipeline.bar_features import read_bar_features

features = read_bar_features(
    ParquetDataCatalog("data/catalog"),
    "ETHUSDT", "5m",
    datetime(2026, 8, 1, tzinfo=UTC),
    datetime(2026, 8, 3, tzinfo=UTC),
    columns=["close", "ma99", "std20", "boll_upper", "boll_lower"],
)
```

`columns` 只读取选中的数据列，自动包含 `ts_event`。`BarFeatures` 同时注册为 Nautilus
自定义数据类型，可用 `catalog.custom_data(BarFeatures, instrument_ids=[bar_type])`
读取（这里的 identifier 是完整 BarType，用于区分周期）。该底层 Catalog 接口不执行
本项目的依赖失效检查，接入回测前应通过上面的安全读取接口验证所需范围。

只准备并返回指定周期，其他周期保持不变：

```bash
.venv/bin/python -m pipeline.prepare_data \
  --bar-intervals 1s 1m \
  --start 2026-08-01 \
  --end 2026-08-03 \
  --symbols ETH-USDT
```

## 幂等和失败规则

- ZIP 下载到隐藏的 `.part` 文件，校验成功后才替换正式文件。
- 本地 ZIP 每次复用前都会按配套 CHECKSUM 校验。
- 转换记录同时包含源 Checksum 和 `CONVERTER_VERSION`。
- 默认只补缺失，周期顺序、请求子集、源数据或转换器版本变化都不会自动重写已有数据。
- `--force` / Python `force=True` 才允许重建本次交易对和 UTC 日期内的 TradeTick、
  指定 Bar 周期；未请求的周期及其他日期保持不变。不指定周期时使用配置中的全部周期。
- `--refresh-archives` 只刷新原始归档，不隐含 `--force`。源归档变化且需要补数据时，
  停止并提示，避免混用不同来源；已有完整数据仍复用。
- 强制转换也先在临时目录完成转换和校验，再替换请求范围内的文件；转换校验失败保留旧数据。
- 若 Catalog 存在没有有效状态记录或不完整的重叠区间，默认停止并保留文件；显式 `--force` 才可替换。
- Rust trades 转换器使用 25 万行 Arrow batch 的有界内存流式转换，并按 UTC 日生成
  单个 TradeTick 文件；Python 回退仍保证同一毫秒成交不会拆到两个 Catalog 文件。
- Bar 的事件时间为周期结束前 1ms，与 Binance 历史 K 线约定一致；无成交周期不
  伪造 K 线。
- Trade ID 重复或倒序、时间倒序、非正价格/数量或非法 maker 标志会让管道在
  `data/pipeline_state/binance/failures/` 写入 JSON
  失败记录。源归档随后转换成功时，对应失败记录会自动删除。
- 只有全部日归档都有 `ready` 状态且对应 Catalog 文件存在，结果才会就绪。

## 当前边界与后续扩展

当前只接受已经结束的 UTC 日。`data.binance.vision` 不负责当天的实时补洞，因此
当请求包含当前 UTC 日时会明确失败。后续实盘阶段应新增：

1. Binance REST：补齐尚未发布日归档的近期 Trade。
2. WebSocket 缓冲：在历史预热期间暂存实时 Trade。
3. 历史/实时交接：按 `InstrumentId + trade_id` 排序和去重。
4. `WARMING_UP → READY → DEGRADED` 状态控制。
5. 出现实时缺口时暂停新的策略决策，补齐并验证后恢复；撤单和风险退出保持可用。

在加入 Funding、OrderBook、多交易所、特征物化和大规模分区回填前，继续
使用普通 Python 即可，不需要引入 Dagster。

### 已新增的分钟级衔接入口

独立的 [`feed`](../feed/README.md) 已实现闭合 1 分钟 OHLCV 的历史读取、
REST 补洞、WebSocket 缓冲、重连补齐和就绪控制。上面的逐笔 Trade 衔接仍是独立后续范围；
新入口不混用 trades/aggTrades 编号，不改历史 Catalog，不自动接入冻结策略。

2026-10-27: Native Arrow scans, quote supplements, feature metadata sidecars and cache rebuild requirements are documented in [native adoption](../docs/NATIVE_CAPABILITY_ADOPTION.md).

## 与行情服务、连接器的边界

本模块负责离线历史数据准备。持续行情服务位于 [`feed/`](../feed/README.md)，
交易所原生适配器桥接位于 [`connectors/`](../connectors/README.md)。公共市场命名和项目路径
位于 [`core/`](../core/README.md)，管道原有导出仍可使用。
