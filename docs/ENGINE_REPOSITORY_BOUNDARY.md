# Engine 独立仓库前的代码依赖边界

## 本轮完成的依赖切割

- 跨 HTTP 的 `EngineConflict`、`QueryError`、`AccountSnapshot` 提取到轻量
  `core/engine_contracts.py`，API 和 Engine 使用同一实现；旧 Engine 导入位置继续
  导出相同类型，避免改变异常捕获、序列化与现有测试行为。
- `api/engine_client.py` 已删除进程内 `EngineRuntime` 构造、调用及兜底，
  始终通过 `httpx` 调用 Engine 的 HTTP 契约；独立 Engine 未启动时不能回退为业务
  进程内执行。业务 API 不再拥有 Engine Runtime 生命周期。
  API 集成测试使用 Engine FastAPI 应用和 HTTPX 测试传输，验证相同的 HTTP 路由，
  但此类测试不等同于两个真实独立进程的全链路验收。
- Engine Docker 构建只复制 `engine/`、`core/`、
  `nautilus_support.py`、策略示例 `packages/` 与锁定依赖，不再复制
  `api/`、`web/`、迁移或业务后台源码。

本轮**不**拆分仓库、不改变业务 API、不改变线上部署模式，也不迁移真实账户。

## 拆仓前仍需完成的解耦（不能宣称已独立）

当前业务 API 的以下模块仍直接依赖 Engine 实现代码：

| API 模块 | 剩余依赖及建议处理方式 |
| --- | --- |
| `api/exchanges/service.py` | Binance 账户资产、品种及现货/合约行情查询；在 Engine 增加受限只读 HTTP 能力 |
| `api/exchanges/trading.py` | 手动交易市场行情；改为 Engine 查询接口 |
| `api/equity/service.py` | 资产估值外部取价及对账查询；改为受授权的 Engine HTTP 查询，API 保留核算逻辑 |
| `api/operations/service.py` 与 `options.py` | 交易所操作与期权历史；增加受控分页查询接口 |
| `api/market_data/contract(s).py`、`router.py`、`service.py`、`subscriptions.py`、`monitor.py` | Catalog 覆盖、查询、导出、行情子进程、期权数据与管理任务；将数据执行/读取下沉 Engine，业务配置与授权仍在 API |
| `api/strategies/service.py` | 动态策略包校验与参数准备依赖 `engine.strategies.package`；需将校验放在受隔离的研究运行时或通过 Engine 授权接口调用 |

**禁止仅将 import 路径改名、复制 Connector/回测实现到 API 或把所有
Engine 实现移动到 `core` 来掩盖耦合。** 需要真正的远程协议隔离和现有
调用/异常/幂等行为回归验证。

建议下轮优先隔离交易所只读查询，再迁移操作记录，最后迁移数据管理链路；
这些功能改造与未来独立仓库发布应分别验收。

## 完全独立的验收条件

1. API 生产环境及所有业务 API 模块导入无需 Engine 源码及 NautilusTrader runtime。
2. Engine Docker 镜像只包含 Engine 及共享契约所需的最小依赖。
3. 不启动 Engine 时 API 可返回明确的 503，生产下单和幂等不回落本地。
4. 回测、交易、期权、资金费率、市场数据管理、历史数据与策略包
   在两个独立进程中完成全链路测试。
5. 版本化的 Engine 镜像与 HTTP 契约兼容性验证通过，再拆为独立仓库。
