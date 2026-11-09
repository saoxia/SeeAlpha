# SeeAlpha API

独立 FastAPI 服务。已迁入用户鉴权及 Binance 原生只读账户接口；不导入旧后端模块，
启动本项目操作记录同步与资产估值采集，不启动旧版监控、历史倒推或系统更新任务。前端独立位于 [`web/`](../web/README.md)。
首页账户服务启动后每分钟采集并原子保存最新展示快照，普通 portfolio 请求只读持久化结果；
首次无快照返回等待提示，失败保留旧值并在两分钟后标记过期，显式刷新资产仍可立即更新。
快照在 API 本地状态目录的 `portfolios/`，无需数据库迁移，不用于交易或资产曲线取证。
期权持仓单独从初始基准和后续真实成交、行权推算，不写入分钟文件；
操作同步复用现有账本及同步状态，重启保留基准。详见 [期权操作与持仓](../docs/OPERATIONS_API.md#期权操作与持仓推算2026-11-08)。
账户创建/编辑不要求交易对。账户快照、手动交易和估值 LiveNode 由常驻
[交易引擎](../engine/README.md) 执行；API 解密后按账户下发凭据，不把主密钥交给引擎。
`/api/*` 给前端的形状保持不变。接口范围及限制见
[Binance 原生 API 说明](../docs/BINANCE_NATIVE_API.md)。


### 可选：租户独立 Docker Engine

`ENGINE_DEPLOYMENT=docker` 时，API 根据 `tenant_id` 分发到各自的常驻 Engine 容器，
并为每个租户使用独立 Token、SQLite、历史数据目录及 CPU/内存限制。
默认 `single` 模式不变；统一目录与多正式租户调整要求全新初始化业务数据库。
**旧单 Engine 的订单/持仓/幂等状态不会自动搬迁。本轮仅支持全新部署，不能覆盖真实交易证据。**
部署、镜像构建、独立数据目录及停机迁移要求见
[每租户 Docker Engine 部署说明](../docs/TENANT_DOCKER_RUNTIME.md)。

## 启动

在项目根目录安装锁定依赖，并启动仅本机可访问的开发服务：

```bash
uv sync --locked --group api
.venv/bin/python -m engine
COOKIE_SECURE=false \
ENGINE_URL=http://127.0.0.1:8766 \
ENGINE_TOKEN=seealpha-local-engine \
ALLOWED_ORIGINS=http://127.0.0.1:3000,http://localhost:3000 \
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`ENGINE_URL` 默认就是 `http://127.0.0.1:8766`。本地开发令牌只用于本机进程互认，
不能当作交易所密钥。公开行情的订阅意图仍写在业务表 `market_data_policies`；
引擎独占行情数据任务的执行、取消、日志、任务状态和中断恢复（含
`engine.pipeline.data_management` 的准备、删除和裁剪）。业务 API 通过 Engine HTTP
读取任务并保留业务授权；删除/裁剪成功后的业务配置调整以独立幂等结算标记记录，
不再写入或恢复 API 本地任务记录。
操作账本的只读同步和资产校准 REST 仍在 API 进程调用连接器。

API 启动前必须显式配置 `DATABASE_URL` 或本机私有数据库连接文件，
并使用 Alembic 完成结构迁移。未配置连接时服务不会自动创建或切换到本地数据库。
服务不会自动创建默认用户或公开注册入口。
局域网打开前端时，API 仍然只监听 `127.0.0.1`。把当前局域网地址放进 `ALLOWED_HOSTS` 和 `ALLOWED_ORIGINS`，
前端用 `npm run build` 与 `npm run start:lan` 提供页面，不要用 `next dev` 对局域网监听。步骤见 [前端说明](../web/README.md#前端启动)。

接口文档：<http://127.0.0.1:8000/docs>，OpenAPI：`/openapi.json`。

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 服务健康状态，不需要登录 |
| `POST /api/auth/login` | JSON 用户名、密码登录，设置会话 Cookie |
| `GET /api/auth/me` | 当前用户，未登录或过期返回 401 |
| `POST /api/auth/logout` | 撤销当前会话并清除 Cookie |
| `POST /api/auth/impersonate` | 管理员把当前会话切换为另一用户的视图 |
| `POST /api/auth/impersonate/stop` | 管理员退出该视图，回到自己的会话 |
| `GET /api/users` | 管理员列出用户及其正式租户。正在查看其他用户时不可用 |
| `POST /api/users` | 管理员创建用户。创建时自动准备一个初始正式租户 |
| `GET /api/tenants` | 当前视图用户自己的正式和开发租户 |
| `POST /api/tenants` | 新建开发租户；额外正式租户仅管理员可创建，按用途创建独立 Engine |
| `POST /api/tenants/{id}/copy` | 把行情订阅复制成自己的新开发租户，不复制密钥或虚拟资金 |
| `POST /api/tenants/{id}/share` | 把行情订阅分享成其他用户的新开发租户 |
| `GET /api/management/state` | 检查快照、策略、任务和可访问报告，需要登录 |
| `GET /api/management/jobs/{id}` | 任务详情及日志尾部，需要登录 |
| `POST /api/management/jobs` | 启动快速检查或完整检查，需要管理员。策略回测不再从这里启动。 |
| `POST /api/management/jobs/{id}/cancel` | 取消正在运行的任务，需要管理员 |
| `GET /api/management/artifacts/{path}` | 下载已发布的报告或检查结果，需要登录 |
| `POST /api/tenants/{id}/accounts/{id}/backtests` | 开发租户用虚拟账户回测已下发的策略包，需要管理员 |
| `GET /api/tenants/{id}/accounts/{id}/backtests/latest` | 读取该账户最近一次回测结果，需要管理员 |

历史回测只接受开发租户、该租户的虚拟账户和已经下发到该账户的包。
租户须先声明对应品种的行情订阅。K 线读取现有 Catalog，不另建任务表，不使用交易所密钥。

## 管理任务与进程锁

检查、取消和重启恢复只由一个持锁 API 进程执行。启动时非阻塞独占
`.local/run/web/server.lock`。不再读取、迁移 `data/web_console/`
中的旧密码、任务和锁文件；该目录即使存在也不影响新管理端。

锁已由其他进程持有时，当前 API 进程仍可提供鉴权、行情与只读管理查询；
在无管理任务锁的进程上启动或取消管理任务返回 409。
持锁进程重启后，仍会从当前持久化记录中恢复管理任务状态，并将无法确认的
`running` 任务标为 `interrupted`，不会盲目操作历史子进程。

当配置 ORM 业务库时，`management_jobs` 是唯一的任务元数据真源，
日志和报告原文件仍在文件系统，启动不再导入旧 JSON 状态。
仅测试用本地非 ORM 模式继续从 `.local/state/web/jobs/` 读取任务快照。

## 手动交易

`/api/trading/*` 是单独的管理员手动执行入口，复用现有加密账户、鉴权和来源保护，
经 connector 的公开 Nautilus RiskEngine / ExecutionEngine 提交 USD-M 永续限价、市价和撤单。
查询接口仍只读；启动不提交交易，不修改交易所杠杆、保证金或持仓模式，不迁移数据库。
请求幂等与执行证据保存于本地 API 状态目录，事件复用既有账本。
手动账户快照提供可空的 `leverage` 正整数，来自所选合约的交易所只读配置，读取失败为空；
不以仓位名义金额或保证金倒算，不改变下单/撤单请求契约。
详见 [功能、恢复与验收边界](../docs/MANUAL_TRADING.md)。

## 项目操作记录

已提供 `/api/operations` 查询、详情、同步和 CSV 导出。前端操作记录列表已切换，
统一存储项目事件与交易所证据；外部记录不自动认定为手工。接口、来源定义、
Nautilus Python 接入限制与执行运行时接入方式见
[项目操作记录说明](../docs/OPERATIONS_API.md)。

资金变动复用同一账本和后台同步循环，接入 USD-M 官方收支历史，按币种保存有符号金额、
交易所类型和流水身份。首次自动补齐最近 87 天，检查点存于既有 `ledger_sync` JSON；
不启动新的后台服务。已有库须先经授权应用 `3debb89b484a`，扩展事件类型检查约束；
不新增表或删除记录。原生订单查询失败不阻止资金读取；实际保存仍要求数据库约束已升级。
管理员可向 `/api/operations/sync` 传 `cashflows_only=true` 单独补齐；查询、详情、导出
使用原 `/api/operations`，`event_type=cashflow`。覆盖语义和产品边界见上述说明。

## 用户管理

使用本地交互命令，密码不通过命令行参数传递：

```bash
.venv/bin/python -m api.auth.manage create-user admin
.venv/bin/python -m api.auth.manage reset-password admin
```

新建、重置密码要求 12–256 个字符；迁移保留原密码哈希，不改变已有账户的密码。
重置密码会撤销该用户全部登录会话；退出只撤销当前会话。
旧迁移用户及会话已清除；需要登录时请先通过上述交互命令创建用户。

## 会话与请求保护

- 使用 Argon2 哈希保存密码，数据库只保存会话令牌的 SHA-256 摘要。
- 会话有效期为七天；Cookie 使用 HttpOnly、SameSite=Strict，默认开启 Secure。
- 每次会话验证只读查询，并即时核对过期与撤销；不缓存鉴权结果。过期记录在创建登录会话时清理。
- `COOKIE_SECURE=false` 仅用于本机 HTTP 开发；正式环境保留默认值并使用 HTTPS。
- 登录失败五次后，同一来源与用户名在十五分钟窗口内被限制；不信任客户端提供的转发 IP。
- 鉴权响应，包括错误响应，均禁止缓存。
- 有副作用的请求校验 Origin 和 Sec-Fetch-Site；`ALLOWED_ORIGINS` 设置允许的前端完整来源。
- `ALLOWED_HOSTS` 默认为 `localhost,127.0.0.1`，正式部署应显式设置实际域名。
- 前端通过同域 `/api` 代理访问，无需开放浏览器跨域访问。

## 验证

```bash
.venv/bin/python -m pytest tests/api -q
```

测试使用临时数据库，覆盖会话持久化、过期与撤销、失败限流、请求来源、Cookie 属性、
密码重置，以及全新初始化的用户与会话持久化。

## 通用 ORM 业务持久化

`api/db/models/` 定义 SQLAlchemy ORM 模型；`api/db/orm.py` 管理连接池与事务。
用户、会话、账户配置、操作记录和 OLAP 设置可通过相同代码使用 PostgreSQL 或 SQLite。
行情 Catalog、ClickHouse 分析数据和 NautilusTrader 引擎状态仍由各自系统管理。
研究任务与报告索引保存在 `management_jobs`、`backtest_reports`；
报告原文件、任务日志仍在文件系统。

通过 `DATABASE_URL` 或私有连接文件 `.local/config/api/database_url` 配置数据库，
也可用 `DATABASE_URL_FILE` 指定连接文件。连接文件应仅供服务端用户读取
（例如 `chmod 600`），不得提交到 Git 或暴露给前端。
PostgreSQL 默认 schema 为 `seealpha`，可用 `DATABASE_SCHEMA` 调整。

**当前版本仅支持全新空的业务数据库/schema 初始化**。先配置连接信息，再执行：

```bash
.venv/bin/alembic upgrade head
```

ORM 启动前会检查 Alembic 版本；不会自动创建、覆盖或接管旧业务表。
已移除旧 PostgreSQL pre-Alembic 接管，以及旧 SQLite 业务数据导入 CLI。
如已有未备份的本地实验数据，应先独立留存；不要把本轮清理当作原地升级。
数据库后续投入真实运营后仍需正规的备份、迁移与恢复流程。

`ENGINE_URL` 在部署环境必须为 HTTP(S) 地址，禁止进程内 Engine 回退；
不配置时默认 `http://127.0.0.1:8766`。当前 `EngineClient` 在生产和测试中
均只支持 HTTP 契约；API 集成测试使用注入的 HTTPX 测试传输连接真实 Engine ASGI 应用，
而非在业务进程内创建 Engine Runtime。注意行情管理等其他 API 模块的剩余
Engine 源码依赖不因此自动消除。

## 行情数据管理

`/api/data/*` 提供已接入 trades 归档及 Tick / K 线的覆盖查询、预览、
准备计划、异步数据准备、逐笔校验、派生 K 线、Tick 导出和任务状态。
复用 Engine Pipeline，任务状态由租户 Engine 文件持有；业务订阅配置保存在 `market_data_policies`，不提供强制重建。
管理员可预览完整 UTC 日范围并确认删除，默认保留原始归档；删除同步清理相关物化与依赖缓存。
历史数据管理已取消固定 31 天范围上限，能力接口 `max_range_days=null`；跨月、跨年任务沿用
现有逐日处理、任务锁、取消和六小时运行超时，预览仍限制为 15 分钟，删除仍需文件快照及明确确认。
统一清单通过 all_levels 查询展示各级别历史区间，状态由 Engine 按品种发布并保留过期降级；Engine 按业务同步的实时配置启动固定 Feed 子进程。
详见 [数据管理 API](../docs/DATA_MANAGEMENT_API.md)。

任务文件与锁由各租户 Engine 保管。Platform 的 `.local/state/api/market_settlements/`
仅保存已完成删除/历史裁剪对业务配置结算的幂等标记，**不是任务执行状态**。
本次不迁移现有实验任务记录；旧 `.local/state/api/data_jobs/` 不会自动导入 Engine。

数据任务进度：管理员使用 `WS /api/data/jobs/stream`，可附 `job_id` 获取详情日志；
复用会话及来源校验，支持断线后获取当前快照，详见 [数据管理 API](../docs/DATA_MANAGEMENT_API.md#任务实时进度)。

期权数据使用相同 `/api/data/*` 路由和任务鉴权，传 product=options。
目录包含标的、到期、方向、行权价及单位；提供近期最多 500 笔和交易所历史 K 线，
不承诺完整历史 Tick。既有本地期权可离线查看、校验和确认删除，无新增迁移。

行情管理按产品/交易对保存采集配置。Platform 仅负责 ORM 意图、认证、期权目录发现与 Engine HTTP 同步；
固定 Feed/历史/计算工作进程全部由 Engine 托管。业务 API 重启不终止采集，Engine 自行恢复启用订阅。
本轮不迁移真实行情或业务数据库，也不启动交易。
范围、存储及接口见 [数据管理说明](../docs/DATA_MANAGEMENT_API.md#按交易对配置与分钟实时管理2026-10-29)。

交易对配置保存于 `market_data_policies`，只管理实时开关、历史起点、最小粒度和所选周期。
启用历史补齐并保存配置时，全部历史、指定起点和追加天数统一解析为固定的 UTC 零点开始日期，
复用 `history_start` 保存，`history_mode` 统一为 `start`；进度仍由文件持有。
Engine 托管接入、补齐和计算进程，并写入运行状态证据；Platform 每 15 秒同步业务配置意图，
监控读取 Engine HTTP 状态而不在 API 内维护进程或生成采集快照。
历史不再由数据库任务启动，失败从文件检查点续跑；兼容 `/jobs` 状态也仅写本地文件。
切换须先授权迁移并显式导入旧配置，详见[四个服务与配置切换](../docs/DATA_MANAGEMENT_API.md#四个服务与配置切换2026-10-29)。


### 删除交易对及全部行情数据

行情数据管理每行的操作菜单新增“删除交易对”。管理员先点击“停止采集并预览删除”，
服务会关闭该交易对的实时与历史追加配置，列出全部日期、全部周期的 Catalog Tick / K 线、
来源归档（含未完成文件）、转换 / 失败记录、指标缓存和该交易对 managed 目录内的实时数据。
预览后取消不会自动恢复采集。输入 `DELETE 交易对` 后创建固定 `delete_pair` 数据任务，
沿用任务互斥、文件指纹核对、逐文件身份检查、取消和超时机制。没有行情文件的配置也可删除。

只有文件删除成功后才移除配置；失败或中断保留已关闭的配置，可能已有部分文件被删除，
须重新预览后处理。删除期间拒绝该交易对配置变更；最后一个配置删除后也推送空订阅列表。
保留合约定义、账户、操作记录、任务审计、研究报告及已导出的独立文件；不操作独立运行的
旧版共享分钟 Feed 存储。执行前须停止手动采集、Pipeline 和相关回测。

接口为管理员 `POST /api/data/pairs/delete/plan`（symbol、product，停止采集并预览）和
`POST /api/data/pairs/delete`（另需 deletion_snapshot、confirmation，返回 202 任务）。
既有“删除历史数据”仍按日期范围工作，默认保留归档。

行情清单与预览读取持有该交易对的共享 access 许可，删除期间返回 409，避免 SQLite
读取与文件删除交错。删除确认快照保留数据库及 WAL 的完整文件指纹；SQLite 共享内存
`.sqlite-shm` 的读标记会随只读查询变化，仅其修改时间不参与确认快照，文件身份和大小仍校验。
实际删除仍使用最新清单逐文件核对身份、大小和时间，防止自动刷新造成确认快照失效。


## 2026-10-29 按交易对协调数据任务

数据任务不再使用项目级 `data.lock` 串行。锁位于
`.local/run/api/data/<product>/<symbol>.job.lock` 与同目录的 `<symbol>.access.lock`：

- 兼容准备、校验、生成、导出与删除操作持有该交易对的独占 job 锁；历史源与计算服务持共享许可。
- 历史任务与实时采集持有共享 access 许可，允许同交易对实时与历史并行。
- 主动删除持有独占 access 许可；“删除交易对”先停止该交易对采集，再独占预览和删除。
- worker 继承其锁文件描述符，API 退出后仍保留保护，直到子进程停止。
- 任务恢复逐交易对核实锁，并重新读取终态，不影响其他交易对正在运行的任务。

前端仅阻止同产品 / 同交易对的冲突任务，运行状态未知的任务也按该范围保护。
保存历史追加参数、但实时采集参数未变化时，不重启已有采集进程。
同一交易对的非重叠日期任务目前也串行：日归档、跨日 K 线和后续指标缓存的真实文件范围
可能重叠，尚不按页面日期拆锁。手动 Pipeline / 回测仍沿用原并发约定，操作目标数据前需自行协调。

原协调锁改动不引入消息队列；后续四服务调整及配置表迁移见上述切换说明。
升级前须完成或停止旧版本的数据任务，不能让仅持旧全局锁的 worker 与新版混跑。


## 数据页加载与数据库排队

数据页的常规状态 / 清单轮询不再因权限加载、弹窗切换或 WebSocket 连接状态改变而中断重启。
任务详情的 HTTP 回退单独跟随当前任务；账户列表仅在进入账户记录页时加载。
PostgreSQL 连接池继续保持 4 个连接、不额外溢出，等待可用连接最多 5 秒；
连接池等待超时按既有 StorageUnavailable 返回 503，避免请求排队 30 秒才失败。
PostgreSQL 新建连接最多等待 5 秒；启用 TCP 保活（空闲 30 秒后探测，间隔 10 秒，最多 3 次），
并在连接使用超过 300 秒后的下一次借出时回收，继续保留借出前的连接检测。
保活由操作系统支持，回收不打断已借出的连接；这些措施不保证代理已确认但未转发的请求能及时失败。
连接错误只记录异常类别、SQLSTATE 和连接池计数，不记录异常原文、SQL、参数或连接地址。
不自动重试业务事务，不改变 SQL 执行超时或交易 / 数据任务的运行时限，也不替代云数据库网络排查。

状态列表复用本次恢复检查读取的任务快照，已结束任务不再在一次请求中重复查库；
孤立运行任务仍在取得对应交易对锁后重新读取终态，避免覆盖刚完成的结果。


2026-11-08：显式 USD-M 管理配置固定本地 1s 与全部 15 个官方 K 线周期；`intervals` 保留输入兼容，
响应与进程参数使用这 16 个周期。`1s` 旧基础值仍读入为 `1m`；原 `auto` 配置恢复行为保留，
再次保存时转为显式配置。行情能力列表包含本地秒线，期权能力保持原范围。历史服务分层核验、定位后补齐，
比较已保存历史 K 线的 OHLCV；完成日期按配置绑定标记后不再自动复查，仅未解决日期
保留五分钟重试。逐日证据、完成标记及进度仍写原文件，不增加 ORM 运行状态。


## 配置开始日期与范围裁剪（2026-10-30）

`POST /api/data/subscriptions/plan` 将配置解析为固定 UTC 开始日期；调早直接保存并重启历史
核验进程，实时参数相同时不重启采集。调晚先暂停该交易对服务，获取独占许可，返回绑定
实际文件与新旧配置的删除快照。`POST /api/data/subscriptions` 兼容原字段，另接受可选
`deletion_snapshot` 和 `confirmation=DELETE 交易对`；需要删除时二者缺一不可。

已确认的裁剪复用现有固定 `engine.pipeline.data_management trim` 子进程及文件任务记录。
成功后才保存新配置并恢复所选服务；失败、取消或 API 中断保留旧日期及暂停状态，
不得自动恢复旧补齐范围。数据或配置变化后须重新预览。新输入的确认文字和快照不存业务配置。
不迁移数据库，不新增后台服务。旧 backfill / 显式范围删除入口继续兼容。


`GET /api/data/chart?symbol=ETHUSDT&interval=15m` 为登录用户提供本地显示用 K 线，
最多最近 300 个周期，合并 Catalog、保留采集库和有效形成中状态，携带闭合、完整性和
运行标记；无数据返回空列表，存储不可用继续返回 503。只读查询持共享 access 许可，
不触发下载、修复或数据重建，不提供策略就绪资格。TRADE 本地 K 线每 1 秒轮询；
旧 `/api/trading/market` 快照入口及其上游查询实现已按用户要求移除。
合约选择器仍通过 `/api/trading/markets` 获取目录与汇总行情。


## 预估资产曲线

USD-M 账户余额、仓位和标记价格变化触发估值，证据与派生点保存于业务 ORM。
账户沿现有只读 REST 每 5 秒检测变化，原生公开标记价每 250ms 合并观察；不操作私有 listenKey。
每五分钟独立查询对账；余额/数量不一致立即校准，其他超阈值金额偏差连续两次校准。
LIVE 使用真实权益 K 线并支持单账户一键重算；缺价和断线保留空白，不补造历史。
新增迁移 `a9f42e716c08` 只准备文件，须明确授权后应用；生产默认采集，
`EQUITY_ENABLED=false` 可关闭。职责、锁、接口、阈值及部署说明见
[资产估值与重算](../docs/EQUITY_VALUATION.md)。


2026-11-07：期权全选配置可启用 `realtime_enabled` 与 `history_enabled`，沿用原采集
及历史进程托管。自动发现的新合约立即沿用开关；保存全选配置统一更新现有期权，
不绕过正在删除合约的排他保护。期权直接保存交易所闭合 K 线，无独立本地计算进程。
历史 Tick 不保证完整，空历史来源不推进检查点；详情见
[期权全选与采集](../docs/DATA_MANAGEMENT_API.md#期权全选与新合约自动纳入2026-11-07)。

期权批量托管由 Engine 独占：每租户只运行一个 `engine.feed.managed --options-config` 采集进程
及一个 `engine.pipeline.pair_data --service --options-config` 历史进程。配置快照在 Engine 的租户状态目录，
参数真源仍为 `market_data_policies`。新增、修改和暂停动态生效；删除只等待对应合约释放
自己的访问锁，不停止其他合约。原鉴权、删除快照、来源检查与 ORM 配置职责不变。


期权管理按标的交易对汇总（2026-11-07）：`DataPolicy.symbol` 另支持 `BTCUSDT` 等
标的范围，保存时统一应用其下所有到期日和行权价，新合约沿用该范围设置。
`GET /api/data/subscriptions?group_options=true` 返回标的级配置及状态，默认查询保留合约级兼容。
删除交易对接受同一标的范围，沿用预览、指纹、确认文字和逐合约排他锁。
标的级预览与提交均先暂停其下全部合约，并在共同的 5 秒窗口内逐一等待采集及补齐释放访问锁，
再生成文件快照；超时返回 409、保留暂停配置并提示重新预览，不启动删除任务，不停止其他标的。
删除该标的后停止旧 ALL 自动发现，其他已纳入标的的配置与服务保留，避免删除后立即重新加入。

## Engine 仓库解耦进度

Engine HTTP DTO、异常合同与容器构建边界已提取，但行情、操作记录与部分交易所
查询仍直接依赖 `engine/` 源码。**当前不能移走 `engine/` 或认定 API 已完全脱离 Engine。**
剩余依赖和验收条件见 [Engine 独立仓库边界](../docs/ENGINE_REPOSITORY_BOUNDARY.md)。
