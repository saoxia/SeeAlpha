# SeeAlpha macOS / Linux 开发环境（Phase 20）

本仓库只负责 Platform。Engine 的 Python/NautilusTrader/Rust 运行环境在
[saoxia/seealpha-engine](https://github.com/saoxia/seealpha-engine)，
不再使用 SeeAlpha 根目录的 `pyproject.toml`、`uv.lock`、
`engine/` 或 `docker/engine.Dockerfile`。

## Platform 安装与检查

在仓库根目录：

```bash
source ./activate.sh
uv sync --project platform --locked
platform/.venv/bin/ruff check .
platform/.venv/bin/python -m pytest -c platform/pyproject.toml -q platform/tests
```

`activate.sh` 只尝试激活 `platform/.venv`，并将工具缓存、
临时目录与 Python 管理文件放在 `.local/`。不要重新激活遗留
`.venv`；这个旧目录不再属于当前工程。

平台服务需要一份显式指定、已迁移且隔离的业务数据库，并通过
`ENGINE_URL`/`ENGINE_TOKEN` 访问独立 Engine。示例：

```bash
uv sync --project platform --locked --no-default-groups
export ENGINE_DEPLOYMENT=single
export ENGINE_URL=http://127.0.0.1:8766
export ENGINE_TOKEN='<独立 Engine 私有令牌>'
export DATABASE_URL='<业务数据库连接串>'
platform/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

启动前确认不会有两个 API 实例同时消费相同正式账户和周期任务。
前端在 `web/` 运行 `npm ci`、`npm run dev`，只绑定本机地址。
需要局域网访问时按 [Web 手册](web/README.md) 使用正式启动方式，
不要开放 `next dev`。数据库与外置数据路径详见
[数据存储](DATA_STORAGE.md)。

## 独立 Engine 与跨仓库兼容回归

在独立 Engine 仓库使用它自己的 `uv sync --locked`、Cargo 和 Dockerfile，
不能在 Platform 根目录直接启动 `python -m engine`。
唯一对外镜像构建入口是 `./scripts/build_external_engine.sh`；
构建前锁定 Engine 完整 Git SHA 并核对两仓库的
`core/engine_contracts.py` 和 `core/trading_contracts.py`。

CI 有三种互不混淆的 Python 环境：日常 Platform 独立测试只安装
`platform/uv.lock`；独立 Engine 仓库 CI 验证它的原生测试；
过渡期 `tests/legacy_engine_integration/` 在独立 Engine
的临时兼容虚拟环境中运行，并叠加 Platform 的锁定依赖。
根目录 `pytest.ini` 和 `ruff.toml` 是测试配置，不是依赖清单。

`SEEALPHA_VERIFY_PLATFORM_STRATEGY=1`、
`SEEALPHA_VERIFY_PLATFORM_BACKTEST=1` 和
`SEEALPHA_VERIFY_BACKTEST=1` 均为额外的**隔离 Docker 验收开关**。
它们仅对本次测试创建一次性容器；普通策略回测复用常驻 Engine。
命令与边界见 [跨仓库验收](docs/EXTERNAL_ENGINE.md)。

## 历史环境记录

2026 年 10 月 26 日的单机 Python/Rust/NautilusTrader 安装与
1,345 条历史测试属于拆仓前快照，不代表当前仓库、当前 Engine
版本或实盘验收结果。原记录可通过 Git 历史查看，不能作为
Phase 20 发布依据。当前提交的检查结果以 GitHub Actions 为准。
