# 常驻交易引擎

Phase 1 把行情、连接和订单事实从业务 API 进程里拆出来。本目录是那个常驻进程。

| 路径 | 职责 |
| --- | --- |
| `engine/connectors/` | Binance 适配器与只读/手动执行客户端。不依赖 feed、pipeline、api、web。 |
| `engine/feed/` | 连续行情。管理入口 `python -m engine.feed.managed`。 |
| `engine/pipeline/` | 数据管道：历史补齐 `python -m engine.pipeline.pair_data`，一次性任务 `python -m engine.pipeline.data_management`，以及归档、Catalog 和 `rust_converter/`。 |
| `app.py` / `__main__.py` | 给业务 API 用的内部 HTTP。 |

`strategy_python/` 仍在仓库根目录，本轮不加载实盘策略。

## 启动

在仓库根目录：

```bash
source ./activate.sh
python -m engine
```

默认监听 `127.0.0.1:8766`。状态库是 `.local/state/engine/book.sqlite`（WAL）。
可用环境变量：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `ENGINE_HOST` | `127.0.0.1` | 监听地址 |
| `ENGINE_PORT` | `8766` | 监听端口 |
| `ENGINE_TOKEN` | `seealpha-local-engine` | 服务间 Bearer 令牌，仅限本机开发 |
| `ENGINE_STATE` | `.local/state/engine` | SQLite 目录 |
| `ENGINE_PROJECT` | 仓库根目录 | 行情锁和数据路径 |

业务 API 使用同名 `ENGINE_URL`（默认 `http://127.0.0.1:8766`）和 `ENGINE_TOKEN`。
先启动引擎，再启动 API。公开行情不需要交易所密钥。手动交易和账户查询由 API 解密后，
按 `account_id` 把密钥放进当次请求；引擎不保存主密钥，也不把密钥写入 SQLite。

内部路由都要求 `Authorization: Bearer <ENGINE_TOKEN>`，并且路径带 `account_id`：

- `GET /health`
- `POST/DELETE /accounts/{account_id}/connections`
- `POST /accounts/{account_id}/snapshots`
- `POST /accounts/{account_id}/orders` 与 `GET /accounts/{account_id}/requests/{request_id}`
- `GET /accounts/{account_id}/orders|fills|positions|gaps|events`
- `POST /accounts/{account_id}/market/workers` 启动 feed 或历史进程
- `POST /accounts/{account_id}/valuation` 估值事件流

订单以 `request_id` 幂等。重复提交返回已保存的结果，不会再次发到交易所。

## 尚未纳入

策略商店、策略加载、实盘组合协调器、多租户隔离，以及把操作账本同步和资产校准 REST
改成纯事件消费。这两类只读连接器调用仍在 API 进程。研究回测子进程仍由 API 的管理任务启动。
冻结研究快照里的 `pipeline` 导入保持原字节，不是当前可运行入口。
