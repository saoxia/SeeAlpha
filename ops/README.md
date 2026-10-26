# 项目检查与运维开发

本目录保留 Web 管理端、项目检查、样本质量、备份、恢复及评估代码。
2026-10-26 已移除旧机器的专用后台服务、定时任务及宿主机守护安装脚本。
本机开发环境已验收，未注册后台任务，原观察数据库及冻结清单未迁移。
版本、安装命令和验收记录见 [开发环境说明](../DEV_ENVIRONMENT.md)。

## 开发入口

安装依赖后，在项目根目录执行：

```bash
bash handover/check_dev.sh
.venv/bin/python -m ops.web_console --host 127.0.0.1
```

管理端说明见 [WEB_CONSOLE.md](WEB_CONSOLE.md)。
开发检查不依赖旧行情或旧观察数据，不会启动采集。
`ops.check_project` 默认还检查原观察冻结状态，缺少数据时会失败；
本机开发使用 `--development`，管理端项目检查也明确采用此模式。

## 历史研究与运维代码

`project_health.py`、`sample_quality.py`、`study_backup.py`、`study_review.py`、
`forward_watchdog.py` 和 `recover_forward_feed.py` 保留用于研究与后续适配。
其中重启、停止后台服务的兼容逻辑仍面向原运行环境，未经 macOS 部署验收。
原观察缺失的数据库、证据、备份和验收页不能由历史报告重建。

`lint_baseline.json` 记录既有策略样式例外及文件哈希；
`source_updates.json` 登记历史共享管道更新。
历史研究源码、冻结 ZIP、参数和结果保留原样，检查仍核验对应哈希。
下一步开发前先完成本机依赖安装与测试，再按需要适配运行方式。
