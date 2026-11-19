# 第三项评估：让 Engine 回测 AI 新生成的策略

## 当前障碍（2026-11-19）

1. Engine `/research/v1/backtests` 当前要求 `strategy_ref=account:<id>`，并要求开发账户里已经下发的 `strategy_load`；只支持一个 `futures_um:<symbol>:1m|5m` 数据集、`parameters={}`，其身份与任务由 Engine 检验。
2. Platform 策略库已有上传 ZIP、策略版本、开发授权、`inspect_strategy` 与 `receive_strategy` 能力，但这属于普通用户显式登记/下发，不允许 AI 未经检查自动调用正式发布路径。
3. Harness 可生成 Python/策略包与 Artifact，研究命令只能回测**已下发**策略。研究态还没有临时包的审核、哈希、依赖白名单、隔离执行、版本绑定与实验历史。

## 最小实现选择：隔离的候选策略包 → 开发回测

- **不**增加一个新的回测引擎，也不让 Agent SSH/Shell 进入 Engine 主机；继续复用 NautilusTrader 与现有 Package/Backtest Worker。
- Harness 将生成的 `seealpha-package.json` 和 Python 代码打成受限 ZIP，交 Platform 建立**只读候选草稿**（限大小、文件类型、依赖、所有权、hash、关联任务）。必须在独立低权限开发沙箱进行静态校验/导入试运行；不得在带交易所凭据的常驻 Engine 主进程中执行任意 AI 代码。
- 设计 Engine 研究操作 `strategies:inspect` 与 `backtests:submit_candidate`，参数优先用 Platform 签发的不可变候选版本 ID + SHA-256，而不是任意脚本路径/URL、Shell 命令、源码文本或生产策略 ID。
- Engine 在开发回测 Worker 内加载候选策略：使用现有 `StrategyPackage`/NautilusTrader 模型，并隔离 Worker 身份、文件/网络/CPU/内存与超时；继续禁止生产账户、Engine admin token、交易所 API 密钥和宿主机 Docker socket。与当前 Engine 研究 v1 不兼容的更改应新增 v2 子集或单独路径，保留旧 v1。
- 每次结果绑定 `task_id / candidate_id / package_hash / dataset_ids / data_version / Engine_version / 参数 / 时间区间 / 回测结果哈希`，生成可追溯执行和失败记录，支持同一候选的幂等重复请求。
- AI 在研究任务内迭代：生成候选 → 策略检查 → 受控回测 → 结构化结果 → 新候选。每一轮消耗预算、不会自动进入生产。最终只生成**待用户审核**策略包，正式下发与实盘启动单独授权。

## 必测场景

正常：EMA 单品种 AI 代码生成、打包、开发回测、结果与候选哈希一致、第二轮修改后新版本结果。失败：非法 ZIP 路径/符号链接、任意依赖、导入时副作用、读取租户私有数据、尝试执行 Shell/访问网络、伪造候选 ID/旧哈希、恶意策略挂起/爆内存、取消/重启/超时后恢复、并发多任务隔离以及身份/预算/调用幂等。

## 开发拆分

A. Platform 候选策略草稿与授权归属（无需生产权限）。  
B. Engine 隔离导入/模拟执行入口，复用既有回测 Worker，明确失败与结果证据。  
C. Harness 生成/提交候选包、读取报告并自动迭代；上线仍须人工审核。  
D. 真实开发 Docker、真实 NautilusTrader 与跨仓库的无交易凭据验收。

**优先级：P0。** 先做单品种 EMA/ETH 回测闭环，再扩到多产品组合；不要把新候选代码直接下发到具有交易权限的 Engine。
