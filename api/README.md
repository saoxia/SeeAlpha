# SeeAlpha API

独立 FastAPI 服务。已迁入用户鉴权及 Binance 原生只读账户接口；不导入旧后端模块，
启动本项目操作记录同步循环，不启动旧版监控、历史重建或系统更新任务。前端独立位于 [`web/`](../web/README.md)。
账户查询按需通过 connector 创建原生节点，接口范围及限制见
[Binance 原生 API 说明](../docs/BINANCE_NATIVE_API.md)。

## 启动

在项目根目录安装锁定依赖，并启动仅本机可访问的开发服务：

```bash
uv sync --locked --group api
COOKIE_SECURE=false \
ALLOWED_ORIGINS=http://127.0.0.1:3000,http://localhost:3000 \
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

API 启动前必须显式配置 `DATABASE_URL` 或本机私有数据库连接文件，
并使用 Alembic 完成结构迁移。未配置连接时服务不会自动创建或切换到本地数据库。
服务不会自动创建默认用户或公开注册入口。

接口文档：<http://127.0.0.1:8000/docs>，OpenAPI：`/openapi.json`。

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 服务健康状态，不需要登录 |
| `POST /api/auth/login` | JSON 用户名、密码登录，设置会话 Cookie |
| `GET /api/auth/me` | 当前用户，未登录或过期返回 401 |
| `POST /api/auth/logout` | 撤销当前会话并清除 Cookie |

业务接口可依赖 `api.auth.dependencies.CurrentUser` 执行登录检查。
当前只提供身份鉴权，没有扩展角色权限模型。

## 项目操作记录

已提供 `/api/operations` 查询、详情、同步和 CSV 导出。前端操作记录列表已切换，
统一存储项目事件与交易所证据；外部记录不自动认定为手工。接口、来源定义、
Nautilus Python 接入限制与执行运行时接入方式见
[项目操作记录说明](../docs/OPERATIONS_API.md)。

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
密码重置以及迁移后用户、会话和旧业务数据兼容。

## 通用 ORM 业务持久化

`api/db/models/` 定义 SQLAlchemy ORM 模型；`api/db/orm.py` 管理连接池与事务。
用户、会话、账户配置、操作记录和 OLAP 设置可通过相同代码使用 PostgreSQL 或 SQLite。
行情 Catalog、ClickHouse 分析数据和 NautilusTrader 引擎状态仍由各自系统管理。
旧版 Web 管理端在启用 ORM 后将任务记录和回测报告索引保存在业务数据库；
报告原文件与任务日志继续留在文件系统。

数据库连接通过环境变量 `DATABASE_URL`，或本机私有文件
`.local/config/api/database_url` 配置；可通过 `DATABASE_URL_FILE` 指定文件路径。
文件仅存放一行数据库连接 URL，权限仅允许当前用户访问（例如 `chmod 600`），
不得提交 Git 或放进前端环境变量。显式环境变量优先，其次是连接文件；配置后
直接使用 ORM，不会因连接失败而自动切换到另一数据库。PostgreSQL 默认 schema
为 `seealpha`，可通过 `DATABASE_SCHEMA` 指定。

对新的空数据库运行：

```bash
.venv/bin/alembic upgrade head
```

迁移使用上述同一连接配置，SQLite URL 应使用绝对路径。ORM 启动前检查迁移版本，
不会自动创建或覆盖数据库。已有的 `seealpha` PostgreSQL 结构可通过同一命令检查并建立
Alembic 基线；迁移会检查字段、主键、外键与关键唯一索引，不兼容时停止，保留原记录。
已有数据库的权限保持原状；直连 PostgreSQL 账号需要具备相应表与序列权限，
如数据库启用了 RLS，也需要能通过现有策略。连接凭据只供服务端使用。
对于已配置的云库，切换前应停止旧版 API 和后台同步，
完成备份、基线与连接测试后再启动 ORM 版本，避免两种账本写入方式同时运行。

迁移本地 SQLite 时，先迁移结构到**独立的空目标数据库**，然后执行：

```bash
.venv/bin/python -m api.db.import_sqlite /绝对路径/旧业务数据库.db
```

导入先生成源数据库快照，单个事务导入用户、会话、账户、操作记录与 OLAP 设置；
目标有业务记录时拒绝覆盖，失败则回滚。快照与加密密钥需要安全保管。
ORM 启用后的旧版 Web 管理端会把已有的 `.local/state/web/jobs/` 记录复制到数据库，
同 ID 已存在时保留数据库版本，原 JSON 和任务日志不删除。回测报告索引随目录扫描更新，
数据盘不可用时已保存的索引不会被清除；页面只展示当前可访问的报告。
未启用 ORM 时，旧版 Web 管理端继续使用本地 JSON。

业务表结构在 `migrations/versions/` 由 Alembic 管理。
