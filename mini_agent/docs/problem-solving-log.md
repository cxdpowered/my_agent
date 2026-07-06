# 问题解决记录（设计决策与踩坑）

记录关键设计决策的“为什么”，以及实现过程中遇到的问题与解决办法。

## 为什么 core + CLI 分层

借鉴 OpenAI Codex harness 的 core runtime / surface 分离。`core` 不依赖 `cli`，
CLI 只调用 core 暴露的接口（`Runtime.run(...)` + `SessionManager` 等）。好处：
- runtime 可被测试直接驱动（集成测试用 fake LLM 跑完整 loop，无需 CLI）；
- 将来换 Web/其他表现层不动核心；
- 职责清晰，trace/session/memory 都归 runtime。
runtime 通过 `on_event` 回调把结构化事件推给 CLI，渲染与逻辑解耦。

## 为什么用 DeepSeek thinking mode

需求指定 DeepSeek 高质量 thinking 模型。thinking 会产出 `reasoning_content`，
对复杂工具编排更稳。代价是成本更高、且带来 reasoning_content 回传约束（见下）。

## 为什么不用 strict beta，改本地 JSON Schema 校验

Structured Outputs / provider strict mode 会增加耦合与不确定性。本项目用
`jsonschema` 在**应用侧**做参数校验：校验失败直接 `ok=false` 并把错误作为 tool
result 回传，让模型自行纠正参数。可控、可测、可解释。

## 如何处理 reasoning_content（关键踩坑点）

DeepSeek 约束：含 `tool_calls` 的 assistant message，其 `reasoning_content`
必须在后续请求原样回传，否则 **400**。落地：
- `session_events.raw_json` 存**完整 OpenAI message 对象**，按 event_id 升序回放
  即得合法 messages（tool_calls assistant 后自然紧跟匹配 tool_call_id 的 tool 消息，
  无需额外配对逻辑）；
- `ContextBuilder.render_message_for_api`：**含 tool_calls 的 assistant 保留
  reasoning_content**；已完成的 final answer 历史则剥离（避免多轮历史里堆 CoT）；
- 压缩时，任何未闭合的 `assistant(tool_calls)/tool_result` 对（含 reasoning_content）
  绝不被压缩掉。
- **绝不展示 reasoning_content**：CLI 的一句 thought_summary 由 tool_calls 模板
  确定性生成，完整 CoT 只进 trace 与 API 回传。集成测试 `test_reasoning_content_replayed`
  专门断言第二次请求的 messages 结构。

实测踩坑：真实环境走 SOCKS 代理（`ALL_PROXY=socks5://...`），openai/httpx 需要
`socksio`，否则报 “Using SOCKS proxy, but the 'socksio' package is not installed”。
已在 requirements 注明。另：设计文档默认模型 `deepseek-v4-pro`，公网当前可用的
thinking 模型是 `deepseek-reasoner`，用 `DEEPSEEK_MODEL` 覆盖（默认值仍按文档，
`.env` 里改为可用值）。

## 为什么 memory 是 user-scoped

参考 Anthropic Memory Tool：memory 是独立于主上下文、可跨 conversation 存取的
持久文件系统。这里定为 **user-scoped**：同一 user 不同 session 共享长期记忆，
不同 user 隔离。普通对话仍是 session-scoped，不跨 session；只有写入 memory 的
长期信息才被跨 session 自动召回。

## 为什么 read_docs 不限路径、而 memory 限定 user 目录

- read_docs 的价值在于“基于用户本地任意文件回答”，用户已明确要求**不限制路径**，
  它是本项目唯一无限制的工具，只适合本地受信任 CLI；
- memory 是 Agent 主动写入的持久存储，若不限路径会带来写越界风险，故**强制**
  相对 `data/memories/{user_id}/` 解析 + realpath 前缀校验，拒绝绝对路径与 `..`。
  这一“能力切分”在 README 安全说明中写明。

## 为什么 todo 不实现

需求明确范围外。todo 属于任务编排/状态管理，会显著增加复杂度，对“最小可用
Agent”的核心目标（loop/工具/记忆/上下文/trace）无必要贡献，故不做。

## 如何设计 trace 白名单

不采用“事后擦除”而是“结构上无法混入”：每种事件类型只枚举允许的 payload 字段
（见 `trace.py::_EVENT_FIELDS`），序列化时 `{k:v for k in allowed}`。credentials
（headers/Authorization/api_key/.env）从不作为 payload 传入这些字段，因此不可能
落盘。单测 `test_whitelist_drops_credentials` + 真实运行后 `grep` 校验 trace.jsonl
无 key。业务原文（prompt/参数/结果）按需求保留、不脱敏。

## 如何设计 session 隔离与 message 重建

- conversation/summary/runs 绑 `session_id`；users/sessions/memory 绑 `user_id`；
- 重建的唯一权威来源是 `session_events.raw_json`（完整 message 对象），`content`
  列只是冗余副本，不参与回放；
- 集成测试 `test_session_isolation` 断言 B 会话 context 不含 A 会话的普通对话；
  `test_message_rebuild_tool_pairing` 断言 tool 配对正确、reasoning_content 保留。

## 如何设计 compaction 与 token 估算

- **每次 LLM 调用前**估算将发送的 rendered context token，超阈值才压缩（懒触发、
  利于缓存）；
- token 估算：字符启发式（CJK≈1.6/字，ASCII≈0.25/字）作基线，用上一轮
  `usage.prompt_tokens` 与本地估算比值做**在线校正**（指数平滑，避免抖动），逐轮
  逼近真实值；
- 压缩选取 recent window 之前、不破坏未闭合 tool 对的旧 events，调 LLM 产出结构化
  JSON summary（解析失败回退原文本、压缩失败回退不压缩），写 `compactions`
  并更新 `sessions.last_summary_event_id` 镜像；**原始 event log 不删**；
- 当前 summary = `compactions` 中 `covered_until_event_id` 最大的一行；渲染时
  context = summary + 该 event 之后的 events（叠加最近窗口与本 run 消息）。

## 启动 -c/-r 恢复设计（Claude Code 风格）

优先级：`--session`/`-r <id>` > `-r`(交互挑选) > `-c`(接续最近) > 默认新建。
- `-c`：`latest_session(user)` 取最近未归档 session；
- `-r`（无 id）：列出最近 session，输入 id 恢复、回车新建；
- `--session`/`-r <id>`：校验 session 属于当前 user 后进入并回放历史。
切换 user 后 current session 置空并新建，memory scope 切换，不混用上一个 user 的
session。

## 并发（两窗口两进程）

SQLite `PRAGMA journal_mode=WAL; busy_timeout=5000; foreign_keys=ON;`，写入用短
事务。两个 CLI 进程共用 `data/agent.sqlite` 不冲突；两窗口 conversation 隔离，
但共享同一 user 的 memory（窗口 1 写入 → 窗口 2 自动召回，已实机验证）。
