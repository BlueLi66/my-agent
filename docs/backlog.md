# 后续优化与暂缓方案清单（Backlog / Future Enhancements）

> [!NOTE]
> **这个文档在工程上叫什么？**
> 在真实软件工程项目中，这类专门存放“当下有意推迟（Deferred）、留待后续优化再评估”的文档，通常叫做 **`BACKLOG.md`**、**`docs/backlog.md`** 或 **`docs/future-enhancements.md`**。
>
> 它和本项目另外两类文档的分工如下：
> - **[learncc_rebuild_design.md](file:///d:/LearnCC/my-agent/docs/learncc_rebuild_design.md)**：当前阶段要落地的正式设计蓝图（What we build now）；
> - **`docs/design-log/`**（工程上称为 **ADR**, Architecture Decision Records）：记录某项具体设计“为什么选 A 不选 B”的决策背景与代价；
> - **本文档（`docs/backlog.md`）**：集中记录**暂时不进入当前主线、但有明确价值和触发条件的优化备选方案**，既避免遗忘，也防止在 M0–M2 阶段过度设计（YAGNI）。

---

## 一、本次技术选型中留待后续评估的备选方案

### 1. 轻量工程依赖增强（原“依赖流派 D2”）
- **当前阶段选择（D1）**：运行时仅使用 `anthropic`、`python-dotenv`、`pyyaml`，内部数据结构采用标准库 `@dataclass`，CLI 终端输出采用纯文本 + ANSI 转义，测试使用 `pytest`（+ `pytest-asyncio`）。
- **暂缓引入的优化包**：
  - `rich`：流式 Markdown 渲染、代码块高亮、结构化审批确认面板与进度状态条；
  - `pydantic`：配置校验、Session JSONL 持久化模型与复杂协议消息的强类型校验；
  - `jsonschema`：替代手写轻量校验器，用于工具入参、`Workflow` 结构化输出（`s16`）与自定义扩展配置。
- **何时重新评估**：
  - **M1 / M2 体验阶段**：若原生终端在流式输出、多 ToolCall 折叠显示或权限弹窗时可读性明显不足，优先在 `cli/` 层局部引入 `rich`（不影响 `core/`）；
  - **M5 / M6 复杂扩展阶段**：当工具与 Workflow Schema 规则变复杂、手写校验维护成本上升时，再评估引入 `jsonschema` 或 `pydantic`。

### 2. 并发模型演进：多线程 / 多进程隔离 vs 纯 `asyncio`（对应原“方案 C”相关思考）
- **当前阶段选择（方案 A）**：主循环、事件总线、Provider 流式请求与异步调度统一采用 **Python 3.13 + `asyncio`**。刻意**不沿用 `s15` 的“同步阻塞 SDK + 多线程 + 全局 `agent_lock`”主循环模式**，以避免多线程锁竞争、终端 `stdin` 抢占和难以精确取消的问题。
- **留待后续优化的并发增强点**：
  - **阻塞型组件卸载**：未来若接入同步第三方库、同步文件索引或本地大模型推理封装，可通过 `asyncio.to_thread()` / `ThreadPoolExecutor` 隔离到工作线程，避免阻塞主事件循环；
  - **Windows CLI 非阻塞输入**：交互循环中若标准 `input()` 妨碍异步唤醒事件（如 Cron/Teammate 自动输出）同时刷新终端，可在 `cli/` 层单独封装带异步队列的输入读取线程（或 `prompt_toolkit`），但不让线程耦合侵入 `core/`；
  - **CPU 密集或强隔离子执行**：在 `s13 Agent Teams` 或高并发子任务扩展到生产场景时，可评估将部分 Teammate 或工具沙箱从协程切换为独立子进程（`ProcessPoolExecutor` / IPC），获得真正的内存与工作目录隔离。
- **何时重新评估**：进入 M5/M6 接入真实并发后台任务/Teams，或阶段 C 做高并发压测时。

---

## 二、阶段 B 刻意暂缓、留待阶段 C 的工程优化项

以下能力已在 [learncc_rebuild_design.md](file:///d:/LearnCC/my-agent/docs/learncc_rebuild_design.md) 中明确划出阶段 B 范围，集中列在这里便于后续排期：

| 类别 | 暂缓项 | 预期收益 | 建议触发时机 |
|---|---|---|---|
| **多模型支持** | OpenAI 兼容端点、Gemini、本地 Ollama/vLLM Provider 适配器 | 摆脱单一 Anthropic 协议限制，适配国内多模型与低成本本地测试 | 阶段 C 初期（或 M4 后如急需多模型评测） |
| **配置体验** | `config.toml` 多 Profile 管理（如 `--profile fast/coding`） | 主模型、Fallback 模型、Goal 评估器可按场景一键切换 | 阶段 C 初期 |
| **持久化与可靠性** | 工具副作用幂等性与恢复重放检查（`write_file`/`bash` 崩溃去重） | 修复“工具已执行但回合未落盘时崩溃导致重放”的已知边界 | 阶段 C |
| **持久化与可靠性** | 跨进程重启的审批挂起与恢复 | 支持长耗时审批等待重启后继续 | 阶段 C |
| **可观测与评测** | Prompt Cache 命中率统计、固定评测集（成功率 / 延迟 / Token 成本对比） | 用量化指标验证压缩、Memory 注入和前缀稳定性设计是否真正有效 | 阶段 C 核心目标 |
| **外部生态** | 真实远程 MCP 传输（stdio / Streamable HTTP / SSE）与 Web/HTTP 服务接口 | 从单机 CLI 工具升级为可嵌入服务或接入真实社区 MCP Server | M5 验证架构后按需接入 / 阶段 C |
