# Binance 原生查询接口迁移

日期：2026-10-27。使用项目锁定的 NautilusTrader 2.0.0rc7。

## 调用边界

`web` → `api/exchanges/router.py` → 账户配置/汇总服务 → `connectors/binance/queries.py`
→ NautilusTrader 公开配置、factory 和原生节点 → Binance。

新查询链路不依赖 CCXT 或适配器私有 HTTP 客户端。
行情连续性继续由 `feed` 管理；历史数据准备继续由 `pipeline` 管理。

## 已替换的前端路由

| 方法 | 路径 | 实现 |
| --- | --- | --- |
| GET | `/api/system/exchange-markets` | `load_binance_instruments`，缓存五分钟 |
| GET / POST | `/api/system/exchange-accounts` | 新 API 配置管理，凭据加密保存 |
| PUT / DELETE | `/api/system/exchange-accounts/{id}` | 修改/移除配置，失效查询缓存 |
| POST | `/api/system/exchange-accounts/{id}/test` | 原生账户与仓位读取测试，不下单 |
| GET | `/api/system/exchange-accounts/balances` | 分账户、分币种余额 |
| GET | `/api/exchange-accounts` | 首页账户导航，不解密凭据 |
| GET | `/api/exchange-accounts/{id}/portfolio` | 原生余额、账户权益和已对账仓位 |
| GET | `/api/exchange-accounts/portfolio` | 同口径账户汇总，错误账户明确排除 |
| GET | `/api/exchange-accounts/{id}/positions` | 新增独立仓位查询 |

这些路径通过 Next.js 同源 `/api` 代理转发到 `SEEALPHA_API_URL`，默认目标为
`http://127.0.0.1:8000`，不再回退到旧业务后端。
保持旧前端 JSON 字段兼容；不把缺失值改成零。

## 当前支持范围

- 首轮 UI 开放 Binance USD-M 的 USDT 结算永续市场，交易对沿用
  `BTC/USDT:USDT` 等接口表示；connector 内由原生品种定义确定身份。
- `production` 对应原生 LIVE，`sandbox` 对应 TESTNET；不同环境不共享缓存。
- 账户连接只在查询时创建；同账户并发查询合并，成功快照缓存十秒，失败不缓存。
- 原生启动使用完整 USD-M 品种范围，账户创建/编辑不配置交易对，不按历史关注列表过滤仓位。
- 新余额字段来自原生账户余额；权益使用原生 `AccountState.info` 的钱包、浮盈亏和保证金总计，
  校验 `钱包余额 + 未实现盈亏 = 总权益`。
- 当前只发布 USDT 抵押口径的权益汇总；存在其他非零抵押资产时，分币种余额仍可查，
  账户权益汇总明确标记未验收，不直接把不同币种相加。
- 原生标准仓位暂未提供标记价、强平价、每仓浮盈亏、实际杠杆、逐仓模式等全部页面字段；
  返回空值及 warning，仓位数量、方向、身份和开仓均价来自对账后的原生缓存。
- 节点只装载观察策略；不配置自动杠杆/保证金修改，不发送交易命令。
  原生对账生成的本地合成条目仅用于恢复仓位缓存，不作为真实成交记录对外发布。

## 凭据与权限

配置管理、余额批量查询、连接测试和市场配置列表要求管理员；首页查询要求已登录。
凭据沿用旧版 Fernet JSON v1 格式；响应仅含 Key 尾号，数据库保存密文。
请求验证失败也不回显提交的 API Key、Secret 或密码。

API Key 和 Secret Key 必须填写同一组 API 的两个不同值；前后端拒绝相同值，
失败的编辑不会覆盖已有凭据。历史账户若已保存相同值，读取/测试会在启动原生节点前
返回具体原因，需在账户编辑页重新填写整组凭据。凭据输入框关闭密码自动填充提示，
避免将 API 凭据作为新密码与确认密码处理；浏览器仍可能自行决定填充行为。

优先读取 `SYSTEM_CREDENTIAL_KEY`，其次 `ETL_CREDENTIAL_KEY`，否则使用
`SYSTEM_CREDENTIAL_KEY_FILE` 指定文件，默认 `.local/config/api/credential_key`。
空配置首次保存凭据时创建仅当前用户可读写的私有密钥文件；如果数据库已有加密账户，
缺少原密钥时返回错误，不生成替代密钥。备份账户数据库时须单独保管原加密密钥。

API 默认无跨域访问，前端继续通过同源 `/api` 代理。

## 尚未替换

操作记录查询、同步和导出已由新 API 提供；权益 K 线暂为示例数据，尚无真实历史数据。账户快照、权益重建和人工调整的前端功能已移除。
当前前端不再依赖旧业务后端；旧代码及其 CCXT 运行依赖已清理。
OKX、现货、币本位、期权，以及下单/改单/撤单接口不在本轮接入范围。

本轮账户读取采用按需、短生命周期的原生节点，适合当前页面读取；后续高频推送应迁入共享的
长连接运行服务，再由 HTTP、WebSocket 和 MCP 共同消费，而不是提高前端轮询频率。

## 验证

```bash
.venv/bin/python -m pytest tests/api tests/connectors -q
```

本地原生集成测试使用虚构凭据，真实运行原生客户端连接本机模拟的 Binance HTTP/WebSocket，
记录全部请求并断言仅有读取请求及用户数据会话的建立/保活/关闭。
这不替代真实 Binance 网络、密钥权限及真实账户的验收。

公开入口参考：[NautilusTrader Binance 集成](https://nautilustrader.io/docs/latest/integrations/binance/)。

## 账户交易对配置清理（2026-10-28）

新增、编辑账户仅提交账户名称、环境及凭据，`market_symbols` 已从请求契约移除。
旧客户端继续提交的同名额外字段按既有 Pydantic 默认行为忽略，不再校验或写入。
前端不再加载市场选择器、搜索品种或要求至少选择一个交易对，账户列表不再展示已选数量。

响应中的 `market_symbols` 标记为 deprecated，仅作为历史兼容元数据保留；
现有 `market_symbols_json` 列与已有内容不删除、不迁移。新账户保存空历史列表，
编辑保留原值，不清空旧数据，也不将其纳入查询快照的缓存身份。
`/api/system/exchange-markets` 作为现有公开品种查询接口继续保留，不再由账户表单调用。

余额、仓位与操作记录仍使用原生账户查询，不启用交易命令。
这不意味着自动采集全市场 Tick；行情采集、数据准备与策略品种继续各自配置。
