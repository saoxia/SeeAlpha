# SeeAlpha 公共基础定义

本层服务于多资产量化交易系统；其中现有市场命名和分钟线模型仅覆盖当前实现，
不表示已经形成适用于股票、期货、现货、永续合约和期权的统一合约模型。
新增市场时须显式扩展合约定义和校验，不能默认套用 USDT 计价或全天交易假设。

- `market/instruments.py`：现有 Binance USD-M 交易对、周期、InstrumentId 和 BarType 命名规则。
- `market/models.py`：闭合分钟线 `MinuteBar`、时间单位和 `DataError`。
- `paths.py`：从源码位置确定项目根目录，供各模块解析逻辑路径。

本层不依赖 connectors、feed、pipeline 或 web，不负责网络、任务编排或配置集中管理。
`pipeline.models` 和 `feed.models` 继续导出已有公共类型，历史研究入口保持兼容。
只供单个模块使用的工具留在其所属模块；文件哈希校验位于测试，不建立通用工具杂物目录。
