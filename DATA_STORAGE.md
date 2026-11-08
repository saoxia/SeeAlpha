# 本机外置数据盘

业务数据使用可配置的 PostgreSQL 或 SQLite ORM 存储。原有云端数据保留；
本机已配置 PostgreSQL 连接并完成 ORM 迁移，连接文件位于 `.local/config/api/database_url`。
加密主密钥保留在 `.local/config/api/credential_key`，须随业务数据备份。
迁移前业务数据快照位于 `.local/state/api/backups/`，是本地记录快照而非完整数据库备份。
详情见 [API 持久化说明](api/README.md#通用-orm-业务持久化)。

配置日期：2026-10-26。外置 USB 硬盘约 500 GB，卷名 `ssd1`，APFS 文件系统、GUID 分区表。

项目 `data` 是指向 `/Volumes/ssd1/NautilusTrader/data` 的符号链接。
原始下载、Catalog、转换状态、分钟行情数据库等写入该目录的文件均存储在外置盘。
Web 任务记录在启用 ORM 后存于业务数据库，旧 JSON 保存在本机 `.local/state/web/`；
登录口令和服务锁分别保存在本机
`.local/config/web/` 和 `.local/run/web/`，任务日志保存在本机 `logs/web/`。
代码、`.venv`、`.local`、Rust 构建目录 `engine/pipeline/rust_converter/target/` 和报告生成源码仍在本机。
回测报告及账户、成交、持仓明细统一写入 `data/reports/backtest/`。

上述 Web 运行文件不跟随外置数据盘迁移，备份时应另外保留。
旧 `data/web_console/` 的自动迁移规则见 [Web 管理端说明](web/README.md#运行文件布局与迁移)。

数据映射由 macOS 符号链接完成，程序不识别卷名、不检查挂载，也不从数据位置推导构建路径。

已验证经项目 `data` 路径读写文件及 SQLite WAL 事务和完整性检查。
运行数据任务前应先连接外置盘并确认挂载在 `/Volumes/ssd1`；任务运行中不要拔盘。
卷名改变后需更新链接。未挂载时不要删除链接或重新创建本机 `data` 目录。

本机链接由 Git 忽略，其他机器需按实际数据盘路径建立自己的链接。
外置盘仍保留 `data/README.md`，其数据布局说明副本如下。

---

# 运行时数据目录

`data/` 存放原始行情、标准化行情、数据准备状态和回测报告；Web 运行状态、检查结果、日志与凭证单独存放。除本说明外，目录内容均被
Git 忽略。

```text
data/
├── raw/
│   └── binance/           # 官方 ZIP 和 CHECKSUM，镜像远端路径
├── catalog/               # Nautilus TradeTick 和由 trades 聚合的多周期 Bar
├── reports/
│   └── backtest/          # 单策略及组合回测报告、账户、成交和持仓明细
└── pipeline_state/
    └── binance/
        ├── converted/     # 原始归档到 Catalog 的转换状态
        └── failures/      # 校验失败清单和错误信息
```

## 保留与重建规则

- `raw/binance/` 是本地原始数据来源。删除后只能从上游重新下载，不要把它和
  派生数据一起清理。
- `catalog/` 默认只补缺失，已有文件不会因周期配置、转换器版本或源归档变化而自动重建。
  只有显式 `--force` 才重建指定交易对、UTC 日期的 TradeTick 和请求周期；其他数据保留。
  K 线可从已有低周期或原始 trades 聚合，不依赖单独的 klines 原始文件。
- `pipeline_state/binance/` 记录 Catalog 的物化进度。清理或迁移时，必须与 `catalog/`
  一起处理，不能单独把状态记录当作数据完成的证据。
- `pipeline_state/binance/failures/` 只保存轻量 JSON，不复制失败 ZIP。记录包含源路径、
  Checksum、转换器版本、失败时间、错误类型和错误信息；转换成功后会自动清除。

- `raw/binance/` 保留已下载的原始 ZIP 和配套 CHECKSUM，转换成功后也不会自动删除。
  下载中的文件以 `.<文件名>.part` 暂存在目标归档同一目录，可用于续传；校验通过后才
  替换正式文件，不另建独立下载暂存目录。此目录会在下载时按需创建。
- 旧 `project_checks/` 与 `dev_checks/` 已清理。Web 项目检查后续写入本机
  `logs/web/checks/`，不会重新创建这两个数据目录；检查功能保留。

行情管线目录由 `engine.pipeline.prepare_data` 按需创建，具体命令和失败规则见
[`engine/pipeline/README.md`](engine/pipeline/README.md)。


## 2026-10-29 行情服务干净切换

用户明确授权清除旧行情与参数后，已执行 `83b07b30f140` 配置表迁移，
将当前 ETHUSDC 的有效配置导入 `market_data_policies`，移除旧 JSON 参数文件。
本次显式清理旧 Catalog、`raw/binance`、`pipeline_state/binance`、
`market_data/binance_um_1m`、旧行情任务状态与日志；这是一次授权清理，不改变上面的默认保留规则。
数据挂载链接、用户、加密账户、操作账本、研究报告与凭据保持原位。
新行情服务重新生成来源与派生数据，监控与进度只写文件，不回填业务任务表。
清理清单位于 `.local/state/api/deployments/20261029-market-services-cleanup.json`。
