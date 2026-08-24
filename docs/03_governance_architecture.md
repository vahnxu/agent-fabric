# 第三部分 · 核心治理架构与自愈拓扑 (Governance Architecture)

---

## 1. 三层分层治理契约（L1 / L2 / L3 Tiering）

为了防止多项目之间规则混乱，系统实行严格的分层治理：

```mermaid
flowchart TD
    subgraph L1["L1 全局元宪法 (Global Invariants)"]
        L1_A["任何 Agent 在还不知道在哪个项目时就必须知道的绝对红线：<br>· 破坏性操作可逆性 (禁止 rm)<br>· 1Password 单一机密源 (禁止明文进对话)<br>· 否定式断言必须穷举证据<br>· 数字类事实强 Readback"]
    end
    
    subgraph L2["L2 跨项目协作与同步面 (Operations & Sync Plane)"]
        L2_A["跨项目导航、双机协作或多模型适配时才需要的治理规范：<br>· Git bare 双机对等秒级事件同步拓扑<br>· 多模型运行时解耦 (Core Decoupling)<br>· 待推提交全量自检门禁 (PUSH_REQUIRES_GREEN_SUITE)"]
    end

    subgraph L3["L3 单 Session 契约与总机调度 (Agent EC & Harness)"]
        L3_A["单次任务执行与长驻会话生命周期调度：<br>· 移动端语音总机 (Switchboard Pattern: 接线不干重活)<br>· 事务状态机账本 (Task Ledger: created → dispatched → verified → closed)<br>· 50MB 阈值 Fail-Closed 原子换壳自愈 (Supervisor)"]
    end

    L1 --> L2 --> L3
```

---

## 2. 多模型运行时解耦（Multi-Runtime Core Decoupling）

系统严禁在核心逻辑中写死特定 Agent 名字：

- **Core 规范**：事务账本、状态流转、Git 同步、安全拦截只按**能力标准（Capabilities）**与标准 I/O 运行；
- **Runtime Adapter**：Claude Code、Codex CLI、Gemini CLI 仅作为最外层适配器，用于映射各自的命令行参数与 Session 路径；
- **收益**：模型随时可热插拔切换，整个系统的治理语义和安全防线保持绝对恒定。

---

## 3. 双机对等 Canonical 架构（Dual-Canonical Topology）

对于拥有两台及以上工作站（如台式机 + 笔记本）的高级用户：

- **Git Bare 扇出**：主干仓库配置对等 bare 镜像；
- **Post-receive 秒级事件钩子**：一端 push，对端 2 秒内自动触发 fetch 和合并；
- **自愈冲突解决器**：自动识别由自动生成物引发的伪冲突，确保双端无感平滑协同。

---

## 4. 移动总机模式与 50MB 原子换壳自愈（Switchboard & Fail-Closed Auto-Rotation）

```mermaid
flowchart TD
    A["监测到总机 JSONL 超过 50MB 阈值"] --> B["后台冷起全新干净的总机 Session (临时名)"]
    B --> C["新 Session 完成账本对齐，并成功接管消息通道 Polling"]
    C -->|接管成功| D["新 Session 重命名为正式总机，旧 Session 平稳退役归档"]
    C -->|接管失败| E["🔴 触发自动回滚：销毁新 Session，原旧 Session 继续保持服务"]
```

1. **接线不干重活**：总机接收语音/文本意图后，仅生成轻量任务卡并分派给对应子项目的临时 Worker 执行；Worker 完成即销毁，总机 Context 增长极慢；
2. **50MB 阈值监控**：守护进程精确监控总机转录文件大小；
3. **Fail-Closed 切换防线**：新会话握手成功后才平滑切流；若启动失败立即自动回滚，消息通道 100% 永不中断。
