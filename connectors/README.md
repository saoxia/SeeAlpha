# SeeAlpha 市场连接器

本目录只维护项目需要的接入配置、原生适配器桥接和少量补充接口。
行情传输、历史请求及连接状态复用 NautilusTrader 原生适配器。

连续行情入口目前支持 Binance USD-M 公开行情，不配置账户或执行客户端。
账户查询独立通过 `binance/queries.py` 按需创建原生节点；不向现有行情节点加入账户或执行配置。

- `binance/native.py`：data-only LiveNode、原生订阅、历史请求和连接生命周期。
- `binance/transport.py`：共享原生节点的历史/实时入口，以及独立服务器时间查询。
- `binance/queries.py`：原生品种加载及只读账户快照（余额、已对账仓位）。使用公开
  LiveNode、配置和 factory，不调用适配器私有 HTTP 客户端，不依赖 API、数据库或旧后端。
- 公共分钟线与错误类型来自 `core.market.models`，市场命名来自 `core.market.instruments`。

连续性、补洞编排、数据就绪、回放交付和持久化属于 `feed/`；
历史归档下载、转换、校验、特征生成和入库属于 `pipeline/`。
连接器不依赖这两个业务模块，不在这里重复实现原生订单和账户接口。
账户快照复用原生启动对账并等待就绪，查询结束后关闭和释放节点。
对账可生成本地缓存条目以恢复外部仓位；观察策略不发送任何下单、撤单、杠杆或保证金修改命令。
测试使用本地 HTTP/WebSocket 模拟端点和虚构凭据验证实际原生客户端，覆盖空仓、单向及双向仓位。

原生标准仓位不提供旧页面全部字段，缺失的标记价、强平价、杠杆及逐仓模式不在 connector 中推算。
资产汇总、权限、凭据保存和缓存由上层服务负责。交易命令和历史归档不属于本轮查询接入。

面向 SeeAlpha 的多资产定位，新增交易所或经纪商时，在独立子目录优先接入其
NautilusTrader 原生适配器；行情接入与账户、执行接入分别说明和验收。
当前公共命名规则仍限于 Binance USD-M；新增市场须显式扩展映射，不能套用 Binance 命名。

```bash
.venv/bin/python -m pytest tests/connectors -q
```
