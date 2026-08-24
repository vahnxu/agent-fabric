# New Agent Onboarding — Substrate

## 入职前置

在执行任何任务前，请运行以下命令确认治理状态：

```bash
./ops/enforce_agent_onboarding_gate.sh
```

通过后方可开始工作。

## 项目概述

Substrate 是个人数字基础设施管理项目，覆盖：
- `compute/` — 计算设备（MacBook, iPhone, iPad...）
- `stack/` — 软件工具栈（Claude Code, MCP, CLI 工具...）
- `network/` — 网络（VPN, DNS, 宽带）
- `data/` — 数据资产管理（文件整理, 备份）
- `home/` — 智能家居、家电、物业
- `vehicle/` — 车辆

## 工作模式

1. 排障 session 在 substrate 启动
2. 过程中产生的可复用结论，更新对应资产页面（如 `compute/macbook-pro.md`）
3. 执行日志写入 `docs/workspace/logs/`

## 治理规则

- 唯一真相源：本地 git `main`
- 日志格式遵循 `docs/workspace/governance/TASK_LOG_SPEC.md`
