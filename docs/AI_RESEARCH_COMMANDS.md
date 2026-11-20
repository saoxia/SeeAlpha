# AI Harness → Platform MCP → Engine MCP（开发环境，显式启用）

当前研究路径由 Harness SDK MCP 客户端经 Platform 任务级 MCP 代理调用 Engine 原生 MCP。无需独立 Gateway、MCP Bridge 或每任务 Docker。CLI 保留用于本地候选包登记和完整性校验后的 Parquet 快照下载，亦可执行固定命令。

## 本阶段新增

- Platform 根据当前登录用户选择的 **开发租户 ID、虚拟账户 ID、数据集 ID** 创建绑定 UUID 的 Harness 研究任务。普通会话原行为不变。
- 研究资格由 Platform 实时核验：开发租户必须 active/归属该用户；Harness + Development 资源获配；账户是该租户的虚拟账户；`market_data_policies` 中有匹配产品/品种、历史数据订阅及请求粒度；该账户已下发与品种一致的开发策略。Engine 还需独立验证实际数据与策略存在。
- Platform 将随机短期 **任务调用凭据**交给该用户的常驻 Harness Worker（不交 Engine 管理令牌或 Engine 签名 Grant）。只支持白名单研究命令与最多 80 次操作；凭据只在当前任务/当前轮次/15 分钟内有效。
- Platform 私有 `research-tasks/leases.sqlite3` 使用独立 0700 目录、0600 文件，保存哈希凭据/调用预算/任务所有者/轮次/事件；每次命令重新查询 Harness 的当前任务状态，接受 `starting`/`running`，拒绝取消、完成、失效、旧轮次以及越权。
- 每次调用再签发绑定任务/租户/数据集/策略/操作的**60 秒 Engine Research Grant**，经 Platform `EngineMCPResearchClient` 调用 Engine 原生 `/research/mcp` 模拟/研究工具。研究 Grant 不进入 Harness 环境、Web、Session 或成果。
- `/api/harness/tasks` 可选传 `research_tenant_id`、`research_account_id`、`research_dataset_ref`；前端 AI Harness 页可以选择打开“开发环境 Engine 研究工具”。Harness Worker 自动加载 `mcp__seealpha_research__*` SDK 工具：数据集发现、快照创建/查询、模拟回测/查询/取消及候选回测提交；CLI 仍负责候选 ZIP 登记与快照二进制下载。
- 研究令牌只持有研究能力。当前 Harness 的自由 Shell 能读取自身**任务级**调用凭据，仍不属于可供不可信客户共享的强隔离环境。不要启用生产数据、真实交易权限或任意 Engine Shell。

## 部署门禁与已知限制

**默认关闭。** 如选择为受信任开发用户启用：

1. 使用本地 **`ENGINE_DEPLOYMENT=docker`**，并配置 `RESEARCH_PROXY_ENABLED=true`。只对**新创建且已获明确授权**的开发 Engine 容器在私有 `engine.env` 中设置单独 `ENGINE_RESEARCH_SIGNING_KEY` 和 `ENGINE_RESEARCH_MCP_ENABLED=true`。现有开发 Engine 没有密钥会**拒绝启用**，不会自动改写已有 env 文件或重建容器。生产 Engine 永不因此配置研究密钥。远端/分布式 Engine 研究调用尚不支持。
2. 设置 `RESEARCH_PLATFORM_ORIGIN` 为从 Harness 可达的可信固定 Platform API Origin；该地址不能由浏览器、任务或 Agent 选择。内网 HTTP 不意味着抗嗅探安全，非回环连接建议使用可信 HTTPS/受限网络。
3. 确保 Platform/Engine 使用同一开发租户研究签名密钥，Platform 共享同一 `research-tasks/leases.sqlite3` 私有状态目录。当前单机场景使用 SQLite 事务预算，**不支持多宿主机上相互独立的 Platform API Worker**。
4. 只允许当前用户操作其专属 Harness。研究任务不排队、任务凭据 900 秒后失效；任务超时/取消/完成后不可继续使用原轮次凭据，续接任务会重新签发凭据。已发给 Engine 的 60 秒 Grant 不支持即时撤销，残余窗口需依赖短 TTL。
5. Harness Agent 工作区仍含不可信 Python/Shell，任务级凭据可能被同 UID 进程读取。只有被严格授权的开发数据和模拟回测可通过该凭据访问；真正的不可信多租户仍需单独的安全隔离与出网/模型密钥代持验收。不会自动下单。

## 本地辅助 CLI（SDK 默认走 MCP）

```bash
python -m seealpha_harness.platform_research datasets
python -m seealpha_harness.platform_research snapshot-create --start 2026-01-01 --end 2026-01-02
python -m seealpha_harness.platform_research snapshot-get --id SNAPSHOT_ID
python -m seealpha_harness.platform_research snapshot-fetch --id SNAPSHOT_ID
python -m seealpha_harness.platform_research backtest-submit --start 2026-01-01 --end 2026-01-02 --starting-balance 1000 --quote-currency USDT --idempotency-key experiment-1
python -m seealpha_harness.platform_research backtest-get --id BACKTEST_ID
python -m seealpha_harness.platform_research backtest-cancel --id BACKTEST_ID
```

快照单次转发上限 16 MiB；超过后拒绝，不能当作大规模流式数据通道。数据来源/时间窗口/完整性仍由 Engine 验证。绝不把明文任务凭据或管理令牌写入命令参数、日志和报告。

## 完整 Python 策略回测（新增，开发后端路由）

在研究代理已获授权的前提下，Platform 后端负责按租户 ID 决定**目标开发 Engine Docker**，不会把目标 URL/Engine Token 交给 Harness。策略 ZIP 的原始文件和 SHA-256 版本不可变地登记在 Platform 的任务作用域候选库。随后由 Platform 直接把打包的策略交给目标 Engine 的独立研究接口，Engine 内已有 Backtest Worker 执行 NautilusTrader 回测，结果仍走 `backtest-get`。该方案不需要新的 Docker 或 Gateway；由 Platform 代理调用原生 Engine MCP。

**显式配置与兼容限制**：

- `RESEARCH_PROXY_ENABLED=true` 且 `RESEARCH_PYTHON_ENABLED=true`，仅支持 `ENGINE_DEPLOYMENT=docker` 的本地可信开发租户。
- 后端为**新建**的开发 Engine 容器写入独立研究 HMAC 签名密钥和 `ENGINE_RESEARCH_PYTHON_ENABLED=true`，同时保留**每租户独立 Engine Token**、Docker 标签/用途和回环端口校验。现有容器不会被静默重建或覆盖，需要部署人员走已有升级流程。
- 权限：开发租户归属、开发虚拟账户、授权历史数据订阅、有效 Harness 任务；平台和 Engine 双重确认。生产 Engine、未授权策略、未知租户或错误回测 dataset 都必须拒绝。
- **自由 Python 不是强隔离沙箱**：容器隔离不同租户与宿主机，但开发 Engine 内子进程与 Engine 服务可能共享 UID、状态和操作系统访问权限。Engine Token/HMAC 防错误路由，不能阻止同 UID 不可信源码读取容器内部的文件。只向可信开发用户显式开放；不能将其宣传为适合任意陌生用户提交代码的安全多租户方案。

AI 写一个符合现有策略包规范的目录（比如参照 `strategy/demo_ema_crossover`），包含 `seealpha-package.json` 和 `strategy.py`，入口为继承 NautilusTrader `Strategy` 的 Python 类，manifest 的 `parameters` 必须明确 `symbol`、`allocation`、`risk_limit`，AI 可以增加其他必要的有限参数。然后：

```bash
python -m seealpha_harness.platform_research python-candidate-create --package ./ai-strategy
python -m seealpha_harness.platform_research candidate-get --id CANDIDATE_ID
python -m seealpha_harness.platform_research python-candidate-submit --id CANDIDATE_ID --start 2026-01-01 --end 2026-01-02 --starting-balance 1000 --quote-currency USDT --idempotency-key version-1
python -m seealpha_harness.platform_research backtest-get --id BACKTEST_ID
```

当前单品种 Python 回测执行支持 `futures_um` 或 `spot` 的 1m/5m 历史数据及已安装的 Engine Python 依赖。期权行情快照可按实际数据订阅授权，但通用期权/跨产品 Python 策略回测尚无对应 Engine 执行合同，不能伪装成已支持。策略代码只在开发 Engine 的 Worker 中执行；不会自动进入策略库、正式下发或实盘启动。部署后的真实 Harness/Platform/Engine/行情端到端验收与生产安全评审仍需单独完成。

## AI 候选策略实验（受限 Python 字面量版）

研究任务可先创建 `candidate.py`，只包含 `CANDIDATE = {...}` 单一字面量字典：

```python
CANDIDATE = {
    "kind": "ema_cross",
    "symbol": "ETHUSDT",
    "fast_period": 8,
    "slow_period": 21,
    "allocation": "1",
    "risk_limit": "1",
}
```

允许的策略规则仅有 EMA/SMA 交叉及反转（`ema_cross/ema_revert/sma_cross/sma_revert`），现有候选回测执行仅接受授权的单品种 `futures_um` 或 `spot` 1m/5m 数据；其它已订阅产品的数据仍可通过快照研究。Platform 通过 `candidate-create` 将 ZIP 的原始内容、候选 ID、SHA-256、所属用户/开发租户/任务持久保存；再次修改产生新 ID 和哈希，不覆盖旧版本。

```bash
python -m seealpha_harness.platform_research candidate-create --file candidate.py --name eth_research --version 1
python -m seealpha_harness.platform_research candidate-get --id CANDIDATE_ID
python -m seealpha_harness.platform_research candidate-submit --id CANDIDATE_ID --start 2026-01-01 --end 2026-01-02 --starting-balance 1000 --quote-currency USDT --idempotency-key iteration-1
python -m seealpha_harness.platform_research backtest-get --id BACKTEST_ID
```

该调用在 Platform 核验真实任务和开发范围，Engine 使用 `candidates:submit` 短期 HMAC Grant，重新静态解析 ZIP/Python AST 并校验包哈希；不 `import/exec/eval` 候选代码，而是复用可信 `CandidateSignal`、NautilusTrader 和现有独立回测 Worker。不会写入生产账户、不会自动发布策略。结果需校验 `research_candidate.candidate_id` 和 `package_hash` 才能对应版本。

**限制**：受限候选信号仅允许上述四种算法族；另有显式开关控制的自由 Python Strategy 开发回测，但其同容器 Worker **不是针对恶意代码的强沙箱**，不可开放给不可信租户。真实 Docker + NautilusTrader + DeepSeek 多轮联调仍需验证，CI Mock 不代表实盘或真实收益。

## 验收边界

已新增合成/Mock 测试；**不宣称**完成真实用户模型会话、跨容器网络、真实 Engine 数据或恶意代码隔离验收。原始 Engine `backtest-submit` 仍只允许已下发策略；新增 `candidate-submit` 支持上述受限字面量候选包，**不能**执行任意 AI Python 策略类。

第三项（动态 AI 策略回测）按 [评估](AI_DYNAMIC_BACKTEST_ASSESSMENT.md) 单独实施。
