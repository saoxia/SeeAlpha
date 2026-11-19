# 技术说明

- [Harness 接入说明](HARNESS_INTEGRATION.md)：独立常驻 Harness 的 Platform 鉴权代理、AI Harness 界面与当前开发阶段安全限制。
- [每用户独立 Harness](HARNESS_PER_USER.md)：可选的按用户 Docker 实例、Node Agent 节点分配及迁移边界。

- [Binance 原生 API](BINANCE_NATIVE_API.md)：已迁移路由、connector 边界、账户凭据与查询限制。

- [引擎升级与接口适配](ENGINE_UPGRADE.md)：升级当日版本、接口差异和兼容约定。
- [原生能力改造](NATIVE_CAPABILITY_ADOPTION.md)：现行实现边界、数据格式与重建要求。
- [改造前评估](NATIVE_CAPABILITY_REVIEW.md)：问题来源与改造依据，作为历史技术参考。

`validation/` 保留上述文档引用的性能和行为对照证据，均为当时测试结果。
日常开发与环境重建见 [开发环境说明](../DEV_ENVIRONMENT.md)。
最新项目检查记录位于 `logs/web/checks/latest.json`。

- [项目操作记录 API](OPERATIONS_API.md)：统一账本、交易所对账、来源关联和前端切换。
