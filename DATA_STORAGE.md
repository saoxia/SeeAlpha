# 本机外置数据盘

配置日期：2026-10-26。外置 USB 硬盘约 500 GB，卷名 `ssd1`，APFS 文件系统、GUID 分区表。

项目 `data` 是指向 `/Volumes/ssd1/NautilusTrader/data` 的符号链接。
原始下载、Catalog、转换状态、分钟行情数据库等写入该目录的文件均存储在外置盘。
代码、`.venv`、`.local`、Rust 构建目录 `pipeline/rust_converter/target/` 和报告生成源码仍在本机。
回测报告及账户、成交、持仓明细统一写入 `data/reports/backtest/`。

数据映射由 macOS 符号链接完成，程序不识别卷名、不检查挂载，也不从数据位置推导构建路径。

已验证经项目 `data` 路径读写文件及 SQLite WAL 事务和完整性检查。
运行数据任务前应先连接外置盘并确认挂载在 `/Volumes/ssd1`；任务运行中不要拔盘。
卷名改变后需更新链接。未挂载时不要删除链接或重新创建本机 `data` 目录。

本机链接由 Git 忽略，其他机器需按实际数据盘路径建立自己的链接。
外置盘仍保留 `data/README.md`，其数据布局说明副本如下。

---

# 运行时数据目录

`data/` 只存放数据管道产生或管理的运行时数据。除本说明外，目录内容均被
Git 忽略。

```text
data/
├── raw/
│   └── binance/           # 官方 ZIP 和 CHECKSUM，镜像远端路径
├── catalog/               # Nautilus TradeTick 和由 trades 聚合的多周期 Bar
├── reports/
│   └── backtest/          # 单策略及组合回测报告、账户、成交和持仓明细
├── project_checks/        # 项目检查日志和结果，不包含研究收益
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

- `project_checks/` 保存各次源码、样式和全量测试结果；`latest.json` 保存最近一次检查内容。健康页和计数明细不代表收益验证通过。

行情管线目录由 `pipeline.prepare_data` 按需创建，具体命令和失败规则见
[`pipeline/README.md`](../pipeline/README.md)。
