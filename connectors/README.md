# SeeAlpha 市场连接器

本目录只维护项目需要的接入配置、原生适配器桥接和少量补充接口。
行情传输、历史请求及连接状态复用 NautilusTrader 原生适配器。

当前仅支持 Binance USD-M 公开行情，不配置账户或执行客户端。

- `binance/native.py`：data-only LiveNode、原生订阅、历史请求和连接生命周期。
- `binance/transport.py`：共享原生节点的历史/实时入口，以及独立服务器时间查询。
- 公共分钟线与错误类型来自 `core.market.models`，市场命名来自 `core.market.instruments`。

连续性、补洞编排、数据就绪、回放交付和持久化属于 `feed/`；
历史归档下载、转换、校验、特征生成和入库属于 `pipeline/`。
连接器不依赖这两个业务模块，不在这里重复实现原生订单和账户接口。

面向 SeeAlpha 的多资产定位，新增交易所或经纪商时，在独立子目录优先接入其
NautilusTrader 原生适配器；行情接入与账户、执行接入分别说明和验收。
当前公共命名规则仍限于 Binance USD-M；新增市场须显式扩展映射，不能套用 Binance 命名。

```bash
.venv/bin/python -m pytest tests/connectors -q
```
