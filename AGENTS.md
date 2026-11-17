# Repository Guidelines for `my-agent`

本项目是 [../learn-claude-code](../learn-claude-code)（17 课主线 `s01`–`s17`）的工程化重建项目（阶段 B）。
在编写任何代码前，**必须先通读以下两份设计文档**：
- `docs/learncc_rebuild_design.md` — 架构蓝图、接口契约、JSONL 持久化规范、M0–M6 里程碑与验收矩阵
- `docs/backlog.md` — 刻意推迟到后续优化再评估的备选方案（未获明确指令前禁止提前引入）

## 硬性约束

1. **严格按里程碑逐步推进**：一次只实现用户指定的当前里程碑（如 M0、M1），完成全部单元与场景测试后再进入下一个里程碑，不要跨里程碑偷跑。
2. **依赖基线（D1）**：
   - 使用 `Python 3.13 + asyncio + uv`（独立 `.venv`）与 `src/my_agent/` 布局。
   - 运行时仅使用 `anthropic>=0.25.0`、`python-dotenv`、`pyyaml`；测试使用 `pytest` / `pytest-asyncio`。
   - **禁止擅自引入 `docs/backlog.md` 里暂缓的第三方库**（如 `pydantic`、`jsonschema`、`rich` 或 Agent 框架）。
3. **单向依赖与零全局状态**：
   - `cli / host -> core -> (provider, tools)`，`extensions -> core` 接口；`core/` 严禁 `import` 任何具体扩展、`persistence` 实现或 `cli`。
   - 禁止使用模块级可变全局状态（对照 `../learn-claude-code/s15_integrated_harness/code.py` 的反面教训）。
   - `Session.messages` 只追加不删改，压缩视图仅变换 `ModelRequest.messages`。
4. **M2 后的核心冻结铁律**：
   - 自 **M2 结束**起，`src/my_agent/core/loop.py` 进入冻结状态；后续里程碑（M3–M6）不得修改 `core/loop.py`。
   - 若确实受阻，必须先暂停并记录原因到 `docs/design-log/NNNN-标题.md`，提请架构审阅后再修改。
5. **参考原版细节与 Windows 兼容**：
   - 迁移具体机制（如 `s08` 四步压缩、`s09` Memory、`s10` Task、`s13` Worktree、`s16` Workflow、`s17` Goal）时，先阅读 `../learn-claude-code/sNN_*/code.py` 与 `README.zh.md` 对齐行为边界。
   - 当前开发机为 **Windows**，命令工具需兼顾 `cmd.exe`、子进程组清理与 UTF-8 容错解码（`errors="replace"`）。
6. **测试与提交门禁**：
   - 除显式要求的真实 API 冒烟测试外，所有核心与多机制联动必须使用 `FakeProvider` 离线可重复通过。
   - 禁止把 `.env`、`.venv/`、`.sessions/` 等运行时产物提交进 Git。
