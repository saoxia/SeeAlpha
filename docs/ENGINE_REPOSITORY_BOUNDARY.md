# Engine 独立仓库前的代码依赖边界

> 更新于 2026-11-09。以下描述第 8–14 批代码合并后的状态；不是对真实账户、正式环境迁移或长时间运行的验收结论。

## 当前代码与运行边界

- `api/` 业务服务不再直接导入 `engine/` 或 `nautilus_trader`。业务 API 的市场数据、交易所查询、操作与资产、策略包检查、回测及任务控制，通过 `EngineClient` 调用经认证的 Engine HTTP 契约；业务授权、账本核算、用户和租户归 Platform。
- `core/engine_contracts.py` 只共享轻量 DTO、异常及版本契约；不在 `core/` 复制交易所 Connector 或 Engine 实现。
- Engine Docker 构建包含 `engine/`、`core/`、`nautilus_support.py`、所需策略包及锁定依赖；不包含 `api/`、`web/` 或业务迁移源码。
- `tests/api/engine_isolation_test.py` 检查业务 API 导入边界，并在仅有 API/Core 的隔离目录建立 ASGI App。CI 的 Docker job 启动独立容器，在另一个仅含 API/Core 的 Python 进程通过 HTTP 检查租户身份、权限、错误语义与协议版本。
- Engine HTTP 主契约版本为 `1`，平台成功响应必须匹配 `X-SeeAlpha-Engine-Contract-Version`；不兼容或缺失版本不得降级回 Platform 本地执行。
- 仍为 **Monorepo**。上述代码路径和 CI 机制说明具备运行时解耦设计，并非已经拆分 Git 仓库，也不证明外部服务端到端运行已在正式环境验收。

## 已完成的迁移顺序

1. 将行情订阅、监控、数据准备、Catalog/预览和任务状态的运行事实交给租户 Engine；Platform 保留订阅意图及授权。
2. 将交易所只读市场、账户资产、操作记录及期权数据读取统一改由 Engine HTTP 承载；Platform 保留业务账本、对账和报告索引。
3. 将策略包检验转移至 Engine 有界 worker；不把容器资源限制误当不可信策略的完整沙箱。
4. 第 12–14 批加入 Platform 无 Engine/Nautilus 启动检测、HTTP 版本协商、独立 Docker 镜像与跨进程 CI 验收路径，最后合并的 [PR #38](https://github.com/saoxia/SeeAlpha/pull/38)。

## 尚待独立验证的范围

- 交易所真实凭据只读接口与完整账本/成交对账；当前不得据此宣称完成自动化实盘交易。
- Feed 长时间连续运行、策略预热、跨故障自动恢复、进程中断后订单/成交/持仓的真实状态重建。
- 数据卷、SQLite WAL、业务数据库和版本升级的可恢复备份；旧 Engine 状态不会自动迁到新租户容器。
- 第三方/AI 编写策略的安全隔离、授权与人工启动机制；现有策略检查 worker 不是任意代码执行的完整安全沙箱。
- 运行环境、网络、账户或长期观察条件受限时，将对应验收标记为**未验证**，不能视为已通过。仍不授权拆仓、生产数据库迁移或真实交易。

## 验证入口

- 本地：`uv run ruff check .`、`uv run pytest -q`。
- Web：`cd web && npm run test:frontend && npm run lint && npm run build`。
- Docker/隔离：`.github/workflows/tenant-engine.yml` 中的 `python`、`docker`、`full_acceptance` jobs。
- 若没有可核对的 GitHub Actions 结论，不得把 CI 定义文件存在当成 CI 已通过。

## 第八批：行情业务模块完全不接触 Engine 文件与源码

- `Subscriptions.resolve` 中不再访问 Binance REST、Nautilus Catalog、SQLite journal 和 Pipeline。
  规范化日期和删除数据影响检测由 Engine `configuration-plan` 完成。
- `Subscriptions.items/_public` 仅从 Engine `subscriptions` 接口获取当前运行状态。
- `ServiceMonitor` 仅通过 Engine HTTP 读取服务健康。无本地状态 JSON 兼容投影。
- 保存和删除订阅仍由 Platform 授权，Engine `sync` 负责验证删除访问锁。

## 第十三批：Engine HTTP 契约版本策略

- 当前支持的 Engine HTTP 主契约版本为 `1`，使用 `X-SeeAlpha-Engine-Contract-Version: 1` 请求/响应头，`GET /health` 的 `contract_version` 字段用于运维核查。
- 生产 Platform 的所有 Engine HTTP 请求（同步、异步、数据控制、任务、导出、估值与流水流式读取）均携带版本头。所有成功响应必须带相同版本；没有版本头、版本不同或 Engine 返回 426 时立即失败，**不允许自动降级或回退到 Platform 本地执行**。
- Engine 拒绝明确声明为不兼容版本的请求，响应 `426`，并在响应头返回其版本。为避免破坏尚未改造的内部运维客户端，Engine 暂时允许不带版本头的请求，但它们仍受原有令牌和租户鉴权；业务 Platform 不使用此兼容路径。
- `1` 的请求/响应字段及既有 401/403/409/422/503 等语义不得在小版本中无通知地改变。增加不兼容字段或错误语义时，需发布新主契约版本并验证支持矩阵；旧 Platform 与新 Engine 混用必须明确验证或拒绝，而不是凭镜像标签推断兼容。
- CI 测试覆盖有效版本、请求版本不匹配、响应版本缺失或不匹配，以及每租户 Engine 的独立 token 路由。此版本策略是服务契约约束，**并不等于实盘行为验收**。
