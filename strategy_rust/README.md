# Rust 策略目录

本目录用于保存 NautilusTrader 原生 Rust 策略及其 Cargo workspace/crate。

当前可加载的 Python 策略包位于 `packages/`。Rust 版本开始迁移后，建议按
聚合器、风险模型、恢复状态机、订单适配层的顺序实现，并用同一组场景测试与
Python 版本逐项比较。
