# AI Harness → Platform → Engine 研究命令（开发环境，显式启用）

这是选定的直接调用架构，不启动 Gateway/MCP 或每任务 Docker。

## 本阶段新增

- Platform 根据当前登录用户选择的 **开发租户 ID、虚拟账户 ID、数据集 ID** 创建绑定 UUID 的 Harness 研究任务。普通会话原行为不变。
- 研究资格由 Platform 实时核验：开发租户必须 active/归属该用户；Harness + Development 资源获配；账户是该租户的虚拟账户；`market_data_policies` 中有匹配产品/品种、历史数据订阅及 1m/5m；该账户已下发与品种一致的开发策略。Engine 还需独立验证实际数据与策略存在。
- Platform 将随机短期 **任务调用凭据**交给该用户的常驻 Harness Worker（不交 Engine 管理令牌或 Engine 签名 Grant）。只支持白名单研究命令与最多 80 次操作；凭据只在当前任务/当前轮次/15 分钟内有效。
- Platform 私有 `research-tasks/leases.sqlite3` 使用独立 0700 目录、0600 文件，保存哈希凭据/调用预算/任务所有者/轮次/事件；每次命令重新查询 Harness 的当前任务状态，接受 `starting`/`running`，拒绝取消、完成、失效、旧轮次以及越权。
- 每次调用再签发绑定任务/租户/数据集/策略/操作的**60 秒 Engine Research Grant**，经 Platform `DirectEngineResearchClient` 调用固定的 `/research/v1` 模拟/研究 API。研究 Grant 不进入 Harness 环境、Web、Session 或成果。
- `/api/harness/tasks` 可选传 `research_tenant_id`、`research_account_id`、`research_dataset_ref`；前端 AI Harness 页可以选择打开“开发环境 Engine 研究工具”。Harness 使用 `python -m seealpha_harness.platform_research` CLI 做数据发现、快照创建/查询/下载与开发模拟回测/状态/取消。
- 研究令牌只持有研究能力。当前 Harness 的自由 Shell 能读取自身**任务级**调用凭据，仍不属于可供不可信客户共享的强隔离环境。不要启用生产数据、真实交易权限或任意 Engine Shell。

## 部署门禁与已知限制

**默认关闭。** 如选择为受信任开发用户启用：

1. 使用本地 **`ENGINE_DEPLOYMENT=docker`**，并配置 `RESEARCH_PROXY_ENABLED=true`。只对**新创建且已获明确授权**的开发 Engine 容器在私有 `engine.env` 中设置单独 `ENGINE_RESEARCH_SIGNING_KEY`。现有开发 Engine 没有密钥会**拒绝启用**，不会自动改写已有 env 文件或重建容器。生产 Engine 永不因此配置研究密钥。远端/分布式 Engine 研究调用尚不支持。
2. 设置 `RESEARCH_PLATFORM_ORIGIN` 为从 Harness 可达的可信固定 Platform API Origin；该地址不能由浏览器、任务或 Agent 选择。内网 HTTP 不意味着抗嗅探安全，非回环连接建议使用可信 HTTPS/受限网络。
3. 确保 Platform/Engine 使用同一开发租户研究签名密钥，Platform 共享同一 `research-tasks/leases.sqlite3` 私有状态目录。当前单机场景使用 SQLite 事务预算，**不支持多宿主机上相互独立的 Platform API Worker**。
4. 只允许当前用户操作其专属 Harness。研究任务不排队、任务凭据 900 秒后失效；任务超时/取消/完成后不可继续使用原轮次凭据，续接任务会重新签发凭据。已发给 Engine 的 60 秒 Grant 不支持即时撤销，残余窗口需依赖短 TTL。
5. Harness Agent 工作区仍含不可信 Python/Shell，任务级凭据可能被同 UID 进程读取。只有被严格授权的开发数据和模拟回测可通过该凭据访问；真正的不可信多租户仍需单独的安全隔离与出网/模型密钥代持验收。不会自动下单。

## 研究命令

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

允许的策略规则仅有 EMA/SMA 交叉及反转（`ema_cross/ema_revert/sma_cross/sma_revert`），仅能研究授权的单品种 `futures_um` 1m/5m 数据。Platform 通过 `candidate-create` 将 ZIP 的原始内容、候选 ID、SHA-256、所属用户/开发租户/任务持久保存；再次修改产生新 ID 和哈希，不覆盖旧版本。

```bash
python -m seealpha_harness.platform_research candidate-create --file candidate.py --name eth_research --version 1
python -m seealpha_harness.platform_research candidate-get --id CANDIDATE_ID
python -m seealpha_harness.platform_research candidate-submit --id CANDIDATE_ID --start 2026-01-01 --end 2026-01-02 --starting-balance 1000 --quote-currency USDT --idempotency-key iteration-1
python -m seealpha_harness.platform_research backtest-get --id BACKTEST_ID
```

该调用在 Platform 核验真实任务和开发范围，Engine 使用 `candidates:submit` 短期 HMAC Grant，重新静态解析 ZIP/Python AST 并校验包哈希；不 `import/exec/eval` 候选代码，而是复用可信 `CandidateSignal`、NautilusTrader 和现有独立回测 Worker。不会写入生产账户、不会自动发布策略。结果需校验 `research_candidate.candidate_id` 和 `package_hash` 才能对应版本。

**限制**：未实现自由 Python 策略类及第三方包的安全执行；当前仅有四种固定算法族，AI 可以调整参数并迭代。现有 Worker 是独立进程而非任意恶意代码的安全容器，因此必须保持禁止导入 AI 代码的实现。真实 Docker + NautilusTrader + DeepSeek 多轮联调仍需验证，CI Mock 不代表实盘或真实收益。

## 验收边界

已新增合成/Mock 测试；**不宣称**完成真实用户模型会话、跨容器网络、真实 Engine 数据或恶意代码隔离验收。原始 Engine `backtest-submit` 仍只允许已下发策略；新增 `candidate-submit` 支持上述受限字面量候选包，**不能**执行任意 AI Python 策略类。

第三项（动态 AI 策略回测）按 [评估](AI_DYNAMIC_BACKTEST_ASSESSMENT.md) 单独实施。
