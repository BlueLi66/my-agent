# my-agent

基于 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 17 课主线（`s01`–`s17`）的工程化重建项目（阶段 B）。

本项目旨在将分散在各教学章节的 Agent Harness 机制整合进一个**分层、无全局可变状态、全异步、可离线确定性测试**的统一运行时中，并显式消解机制间的交互冲突。

---

## 核心设计特点

- **单向依赖与零全局状态**：`cli / host -> core -> (provider, tools)`，`extensions -> core 接口`；`core/` 不依赖具体扩展、持久化实现或 CLI。
- **State 与 Context 严格分离**：`Session.messages` 只追加不删改；压缩视图与临时提醒通过 `ModelRequest`（含 `injections`）在请求时组装，尽量保持 `system` 与排序后 `tools` 前缀稳定。
- **Hook（控制流）与 Event（只读流）解耦**：扩展点严格区分拦截型（`before_tool` / `before_stop` / `on_user_input`，fail-closed）与观察型（`after_model` / `after_tool` / `on_session_*`，fail-open）；通过 `EventBus` 顺序广播流式 `TextDelta` 与状态事件。
- **核心循环冻结**：`src/my_agent/core/loop.py`（150 行）自 **M2 结束起冻结**，后续 M3–M6 机制均通过 `Extension` 生命周期外挂接入。

---

## 里程碑进度

| 里程碑 | 范围 | 状态 | 说明 |
|---|---|---|---|
| **M0 骨架** | `s01` + 核心契约 | ✅ 已完成 | `types`、`Session`、`ModelRequest`、`HookDispatcher`、`EventBus`、`CancelToken`、`FakeProvider` |
| **M1 能用** | `s02` + 流式 + CLI | ✅ 已完成 | 工具注册表、6 个内置工具（含 Windows `cmd.exe` / UTF-8 / 进程树清理）、流式 `AnthropicProvider`（含重试与备用模型切换）、最简 CLI |
| **M2 扩展定型** | `s03`–`s04` | ✅ 已完成 | `PermissionExtension`（三道闸门 + 会话授权）、`UserHooksExtension`（`.my-agent/hooks.*`）、`TerminalApprovalBroker`；**冻结 `core/loop.py`** |
| **M3 复杂任务** | `s05`–`s07` | ⏳ 待开始 | `TodoExtension`、`SkillsExtension`、`SubagentExtension` |
| **M4 记忆与恢复** | `s08`–`s09` + 持久化 | ⏳ 待开始 | 四步非破坏性压缩、`MemoryExtension`、`.sessions/<id>.jsonl` 崩溃恢复 |
| **M5–M6 进阶扩展** | `s10`–`s17` | ⏳ 待开始 | Task 图、后台任务、Cron、MCP、Agent Teams + Worktree、Workflow Runtime、Goal Loop |

---

## 快速开始

### 1. 环境准备

需要 **Python 3.13** 与 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
```

### 2. 运行离线测试

核心与扩展测试均基于 `FakeProvider` 与模拟客户端离线运行（当前共 103 项单元与场景测试）：

```bash
uv run pytest
```

### 3. 配置与启动 CLI

复制环境变量模板并填入密钥与模型 ID：

```bash
cp .env.example .env
```

支持按以下优先级加载配置（后者覆盖前者）：
1. `~/.my-agent/.env`
2. `<workdir>/.env`
3. `--env-file <path>` 指定的配置
4. 进程环境变量

启动交互式 CLI：

```bash
uv run my-agent -C /path/to/workspace
```

---

## 目录结构

```text
my-agent/
├── AGENTS.md                        # 仓库开发守则与里程碑约束
├── pyproject.toml                   # D1 极简依赖配置（anthropic / python-dotenv / pyyaml）
├── .env.example                     # 环境变量配置模板
├── docs/
│   ├── learncc_rebuild_design.md    # 完整架构蓝图、接口契约与验收矩阵
│   └── backlog.md                   # 暂缓引入的备选方案与阶段 C 优化清单
├── src/my_agent/
│   ├── core/                        # 无状态依赖的核心运行时（loop.py 已冻结）
│   ├── provider/                    # Provider 抽象、Anthropic 流式适配与 FakeProvider
│   ├── tools/                       # 工具注册表、路径安全校验、进程管理与内置工具
│   ├── extensions/                  # 生命周期扩展（Permission / UserHooks / ...）
│   ├── persistence/                 # 会话存储接口与实现
│   └── cli/                         # 终端入口、流式渲染与交互式审批
└── tests/
    ├── unit/                        # 模块单元测试
    └── scenario/                    # 多机制联动场景测试
```

---

## 参考与文档

- 架构与接口设计：[docs/learncc_rebuild_design.md](./docs/learncc_rebuild_design.md)
- 暂缓优化清单：[docs/backlog.md](./docs/backlog.md)
- 上游教学项目：[shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)（MIT License）
- 开源协议：[LICENSE](./LICENSE)
