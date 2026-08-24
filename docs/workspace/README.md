# Substrate — 文档索引

## 项目范围

个人数字基础设施：计算设备、软件工具栈、网络、数据资产、智能家居、车辆、**治理基础设施**。

## 目录结构

| 目录 | 内容 |
|------|------|
| `compute/` | 计算设备台账（MacBook, iPhone, iPad...） |
| `stack/` | 软件工具栈（Claude Code, MCP, CLI, SwiftBar...） |
| `network/` | 网络（VPN, DNS, 宽带, WiFi, 安全审计, `vpn-first-boot/`） |
| `data/` | 数据资产（备份策略、文件整理） |
| `home/` | 智能家居、家电（Terncy...） |
| `vehicle/` | 车辆保养 |
| `governance/` | 治理基础设施（harness 架构、ops 脚本速查） |

## 文档子目录

| 目录 | 内容 |
|------|------|
| `docs/workspace/logs/` | session 执行日志（YYYYMMDD 格式） |
| `docs/workspace/governance/` | 治理策略副本（TASK_LOG_SPEC.md 等） |
| `docs/workspace/references/` | 设计决策、讨论记录 |
| `docs/workspace/archive/` | 历史/已取代文档 |

## 入职门禁

新 Agent 在执行任何生产相关任务前，必须运行：

```bash
./ops/enforce_agent_onboarding_gate.sh
```
