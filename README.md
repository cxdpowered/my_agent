# Minimal Agent Runtime

一个从零实现的**最小可用 Agent**：核心 runtime（自实现 loop / parse / tool registry）+ 交互式 CLI 表现层。真实调用 DeepSeek thinking 模型，支持工具调用、多用户/多 session 隔离、跨 session 长期记忆、上下文无损保存与阈值压缩、可观测 trace。



## 项目目标

- 接收用户输入，让真实 LLM 判断直接回复还是调用工具；
- 执行工具，根据结果继续 loop 或返回最终答案；
- 多用户、多 session 隔离；跨 session 的长期 memory；
- 上下文无损保存 + 阈值触发压缩（compaction）；
- 真实搜索（Tavily）、真实天气（高德）、本地文档读取、计算器、memory 工具；
- trace 日志与 CLI 内查看；mock-first 测试 + 少量真实 smoke。


本项目**不使用** LangGraph / LangChain Agent / OpenHands / OpenClaw 等现成 Agent Runtime。
目的是把 Agent 的核心机制（循环、解析、工具注册、上下文管理、记忆召回、压缩、trace）
**自己实现一遍**，做到可解释、可测试、可扩展；框架只承担通用能力（HTTP、JSON Schema 校验、
SQLite、CLI 渲染、测试）。
## 架构设计

```text
mini_agent/
  core/                 # 不依赖 cli
    runtime.py          # 自实现 Agent loop（LLM -> parse -> tool -> loop -> final）
    llm.py              # DeepSeek client（重试、thinking、usage 提取）
    parser.py           # assistant message -> AgentAction
    context.py          # ContextBuilder + token 估算 + compaction 触发判定
    compaction.py       # 压缩 prompt 调用与 summary 解析/回退
    sessions.py         # user/session/event 持久化与 message 重建
    memory.py           # user-scoped memory 文件存取 + 关键词索引/召回
    trace.py            # 白名单序列化 + JSONL 写/读
    store.py            # SQLite(WAL) 连接与 schema
    prompts.py          # system / compaction prompt
    tools/              # base + calculator/search/weather/read_docs/memory
  cli/                  # 只调用 core 暴露的接口
    app.py commands.py render.py
  tests/                # unit / integration（含 live smoke，默认跳过）
  docs/                 # prompts.md, problem-solving-log.md
```

核心原则：`core` 不依赖 `cli`；工具通过 registry 注册；session/memory/trace 都是 runtime 组成部分；所有状态本地持久化（SQLite + memory 文件目录）。

## Agent Loop

实现于 `core/runtime.py`：

```text
START_RUN（生成 run_id）
  -> 持久化 user message
  -> auto memory recall（关键词召回 top K，注入固定块）
  -> LOOP（step 从 1 开始，按 LLM 调用次数计）:
       -> maybe compact context（LLM 调用前估算 token，超阈值才压缩）
       -> build context（ContextBuilder 渲染 messages）
       -> call LLM（step += 1）
       -> parse assistant message -> AgentAction
       -> persist assistant message（含 reasoning_content）
       -> final_answer  : 写 trace、结束、返回
       -> tool_calls     : 若 step>=MAX_AGENT_STEPS 不执行工具直接收尾；否则执行工具、回传结果、继续
       -> invalid        : 组一条纠错提示重试一次，仍失败则收尾
  -> 异常：标记 run failed/partial，返回可恢复错误
```

- `MAX_AGENT_STEPS = 8`，**按 LLM 调用次数计**；第 8 次即便返回 tool_calls 也不执行工具，直接收尾（防死循环、便于测试与 trace 可读）。

## 工具注册机制

- `ToolSpec(name, description, parameters_schema, handler)`；
- `ToolRegistry`：注册、按名查找、**重复名称报错**、`to_openai_tools()` 输出**稳定顺序**（利于缓存）；
- 工具执行前做**本地 JSON Schema 校验**（`jsonschema`），不使用 DeepSeek beta strict mode；
- 校验失败返回 `ok=false`，错误作为 tool result 回传 LLM，让其自行纠正参数；
- 工具通过 `ToolContext`（user/session/run id、store、memory、trace、只读 env）访问运行时。

内置工具：`calculator`（AST 白名单求值，非 eval）、`search`（Tavily）、`weather`（高德）、`read_docs`（本地文件）、`memory`（跨 session 长期记忆）。

## DeepSeek Thinking Mode 与 reasoning_content 处理

- 使用 OpenAI-compatible Chat Completions，`extra_body={"thinking":{"type":"enabled"}}` 启用 thinking，`reasoning_effort=high`；
- thinking 会返回 `reasoning_content`（原始 CoT）。**关键约束**：当 assistant message 含 `tool_calls` 时，后续每一轮请求必须原样携带该 message 的 `reasoning_content`，否则 DeepSeek 返回 400；
- 落地：`session_events.raw_json` 存**完整 OpenAI message 对象**，按时序回放即得合法 messages；`ContextBuilder` 对**含 tool_calls 的 assistant 保留 reasoning_content**，对已完成的 final answer 历史则剥离；
- 不把 `reasoning_content` 展示给用户。CLI 只显示由 `tool_calls` 模板**确定性生成**的一句 `thought_summary`（如“正在调用 weather …（location=武汉）”），完整 CoT 只进 trace 与后续 API 回传。

## Session / User 管理（含 -c/-r 恢复）

三层标识：`user_id`（默认 `getpass.getuser()`）/ `session_id`（一个聊天窗口）/ `run_id`（一次输入触发的一次 loop）。

作用域：users/sessions/memory 为 user-scoped；conversation/summary 为 session-scoped；tool trace 为 run-scoped。窗口 1 与窗口 2 不共享 conversation，但都能召回同一 user 的 memory。

启动行为（Claude Code 风格，优先级从高到低）：

```text
--session <id> / -r <id>   直接进入指定 session
-r（不带 id）              列出最近 session 供交互挑选
-c / --continue            接续当前 user 最近一个未归档 session（无则新建）
默认                       新建一个 session 进入
```

## Memory 设计（user-scoped，路径限定）

- memory = **user-scoped**，存于 `data/memories/{user_id}/`；同一 user 不同 session 共享，不同 user 隔离；
- **路径安全**：`memory` 工具的 `path` 一律相对 user 目录解析，`os.path.realpath` 后做前缀校验，**拒绝绝对路径与 `..` 穿越**；
- **索引与召回**：`write/append` 成功后按 `##` 标题或段落切块，`DELETE + INSERT` 重建 `memory_index` 中该文件的行；每次 run 开始以 `query = 当前输入 + 最近 summary` 做规范化关键词匹配（小写、去标点、中文按子串），命中词数打分、`updated_at` 作 tiebreak，取 `MEMORY_TOP_K=5`，以固定【长期记忆】块拼在 system prompt 之后；
- LLM 仍可主动调用 memory 工具做 search/list/read/write/append。

**召回时机与放置方式**：召回发生在**每次 run 开始、首个 LLM 调用之前**；召回结果作为一个**独立的 system 消息块**放在 system prompt 之后、会话摘要之前，全 run 内保持稳定（利于缓存），不混入普通对话历史。

## Context 与 Compaction

渲染顺序（稳定，利于缓存）：`system prompt` → `【长期记忆】` → `会话摘要（最新 compaction 行）` → `最近 RECENT_TURNS_TO_KEEP 轮` → `本 run 消息`。工具 schema 通过 API 顶层 `tools` 传入。

- **触发时机**：每次 LLM 调用前估算将发送的 rendered context token；超 `CONTEXT_COMPACT_THRESHOLD_TOKENS=60000` 才压缩；
- **token 估算**：字符启发式（CJK≈1.6 tok/字，ASCII≈0.25 tok/字）为基线，用**上一轮 `usage.prompt_tokens` 与本地估算的比值**在线校正；
- **压缩流程**：选取 recent window 之前、且不破坏未闭合 tool 配对的旧 events → 调 LLM 生成结构化 JSON summary → 写入 `compactions`（`covered_until_event_id`）→ 更新 `sessions.last_summary_event_id`；**原始 event log 不删除**，压缩只影响后续渲染；
- **不可压缩**：最近 `RECENT_TURNS_TO_KEEP=8` 轮，以及任何未闭合的 `assistant(tool_calls)/tool_result` 对（含 reasoning_content），避免 DeepSeek 400；
- summary 解析失败时回退为“原文本 summary”并记 trace；压缩整体失败时回退为“不压缩、直接用原始 events”，不阻断本次回答。

## Trace 日志（白名单序列化）

- `/trace` 在 CLI 内查看当前 session 最近一个 run 的 trace；同时写 `logs/trace.jsonl`（每行一个 JSON）；
- **白名单序列化**：每种事件只写入枚举允许的字段（messages、tool 参数/结果、usage、cache 命中等业务内容），**从不序列化** request headers、Authorization、api_key、`.env` 原文——“不是事后擦除，而是结构上无法混入”；
- 业务内容（prompt/工具参数/结果原文）按要求保留，不脱敏。

## 环境配置与部署

这是一个**本地运行的交互式 CLI**（非常驻服务），部署 = 装依赖 + 配 key + 跑起来。全平台通用（Windows / macOS / Linux）。

### 1. 前置条件

- **Python 3.10+**（`match` 语法与类型注解需要）；
- 三个第三方 key：DeepSeek（LLM，必需）、Tavily（search 工具）、AMap 高德（weather 工具）。
  只想跑核心 loop / 测试可以只配 DeepSeek，search / weather 工具在缺 key 时会返回错误结果由 LLM 处理。

### 2. 安装依赖（建议用虚拟环境）

```bash
# macOS / Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows PowerShell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```


### 3. 配置 key

只通过**环境变量**读取 key，不写入代码 / README / trace。复制模板后填入真实值：

```bash
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

`.env` 内容（示例见 `.env.example`）：

```env
DEEPSEEK_API_KEY=your_deepseek_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro      # 公网当前可用的 thinking 模型为 deepseek-reasoner，可在 .env 覆盖
DEEPSEEK_REASONING_EFFORT=high
TAVILY_API_KEY=your_tavily_key
AMAP_API_KEY=your_amap_key
```

- key 获取：DeepSeek <https://platform.deepseek.com/>、Tavily <https://tavily.com/>、高德开放平台 <https://lbs.amap.com/>（Web 服务 API）。
- `.env` 已被 `.gitignore` 忽略，**永远不要提交**。程序启动时通过 `python-dotenv` 自动加载。
- 模型说明：设计文档默认 `deepseek-v4-pro`；`api.deepseek.com` 当前公开的 thinking 模型是 `deepseek-reasoner`，在 `.env` 里覆盖 `DEEPSEEK_MODEL` 即可。



### 4. 运行

```bash
python -m mini_agent                         # 默认：新建 session
python -m mini_agent --user za               # 指定 user
python -m mini_agent --user za -c            # 接续该 user 最近一个未归档 session
python -m mini_agent --user za -r            # 列出最近 session 供交互选择
python -m mini_agent --user za -r <id>       # 恢复指定 session
python -m mini_agent --user za --session <id># 非交互直接进入指定 session
```

首次运行会自动在项目根创建 `data/`（SQLite 状态库 + `memories/`）与 `logs/`（trace），两者均已 gitignore。

### 5. 验证部署

```bash
pytest                # mock 套件，不触发真实外部 API，应全绿
pytest -m live        # 可选：真实 DeepSeek/Tavily/高德 smoke（需 key 齐全）
```

`pytest` 全绿即代表核心 runtime、工具注册、session、memory、compaction、trace 均正常；再启动 CLI 问一句「武汉明天天气怎么样？」验证真实 LLM + 工具链路。

## CLI 命令

```text
/help                 查看命令
/whoami               查看当前 user
/user <user_id>       切换 user（切换后新建 session）
/new [title]          创建 session
/sessions             列出 session
/use <session_id>     切换 session（并回放历史）
/rename <title>       重命名当前 session
/archive              归档当前 session
/memory               查看 memory 文件列表
/memory search <q>    搜索 memory
/trace [run_id]       查看 trace（/trace --json 输出 JSON；/trace last 最近一次）
/exit                 退出
```

普通输入直接交给 Agent，例如：`武汉明天天气怎么样？`、`查一下 Tavily 是什么并总结三点`、`读取 C:\...\xxx.md 帮我总结`、`记住：我写周报喜欢先结论再细节`。

## 安全说明（read_docs 不限路径 vs memory 限定 user 目录）

- **read_docs 不限制路径**（本项目唯一无限制的工具）：可读取当前进程有权限访问的任意文件。能力较大，**仅适合本地受信任的 CLI demo，不宜暴露为远程服务**。文件内容不会自动写入 memory，除非模型明确调用 memory 工具且符合写入规则；
- **memory 严格限定在 `data/memories/{user_id}/`**：realpath 前缀校验，拒绝绝对路径与 `..` 穿越；
- API key 只从环境变量读取；trace 通过白名单序列化，结构上不含 credentials；
- `.env`、`data/`、`logs/` 已在 `.gitignore` 中，trace 含业务原文，不宜上传公共仓库。

## 测试方式

### 快速运行

```bash
pytest                                 # 默认：54 个 mock 测试，零网络/零 key，~0.2s，确定性
pytest -m live                         # 可选：真实 DeepSeek/Tavily/高德 smoke（需 key 齐全）
pytest mini_agent/tests/unit/test_calculator.py            # 跑单个文件
pytest mini_agent/tests/unit/test_calculator.py::test_basic # 跑单个用例
pytest -v                              # 显示每个用例名
```

> `pytest.ini` 里设了 `addopts = -m "not live"`，因此 `pytest` **永不触发真实外部 API**；只有显式 `-m live` 才会调真实服务。

### 测试策略（mock-first）

核心思想：**默认不碰真实外部 API，用「可编排的假 LLM + 假 HTTP」把整条 Agent loop 跑通并断言行为；另留少量真实 smoke 测试，默认跳过。** 好处是确定性、可 CI、不烧额度，同时又用真实 smoke 满足「需使用真实 LLM API」。

三层：

| 层 | 位置 | 作用 |
|---|---|---|
| 单元 | `tests/unit/` | 单模块：registry、calculator、parser、sessions、memory、context/compaction、trace、外部 HTTP mock |
| 集成 | `tests/integration/test_runtime_loop.py`、`test_memory_and_isolation.py` | 用假 LLM 驱动**完整 loop**，断言副作用与 context 结构 |
| 真实 smoke | `tests/integration/test_live_smoke.py`（`@pytest.mark.live`） | 真实各打一次 DeepSeek/Tavily/高德，只验证连通与返回结构 |

### 两个关键道具

- **`FakeLLM`（`tests/conftest.py`）**：脚本化的假 LLM，预先写好「第 1 次返回 tool_calls、第 2 次返回 final answer」。它实现与真 LLM 相同的 `complete(messages, tools)` 接口，Runtime 无法区分；并记录每次收到的 `messages`，从而可反向断言「Runtime 到底往 context 塞了什么」（如 `reasoning_content` 是否回传）。
- **隔离 fixtures**：`store` 用内存 SQLite（`:memory:`），memory/trace 写 `tmp_path`，每个测试全新一份、互不污染。外部 HTTP 用 `monkeypatch` 替换 `httpx.get/post` 为返回假响应的桩。

### 断言的是行为而非文字

因为 LLM 是假的，断言不看措辞，看**结构与副作用**。代表用例：

- `test_direct_reply` —— 1 次 LLM 调用即直接回复；
- `test_calculator_flow` —— 工具结果被持久化成 `role=tool` 事件，payload `ok=true`；
- `test_max_loop_stops` —— 第 8 次返回 tool_calls 也**不执行**工具，只有 7 次工具执行，状态 `partial`；
- `test_reasoning_content_replayed` —— 第 2 次请求里带 tool_calls 的 assistant **仍保留 `reasoning_content`** 且 tool 消息 id 配对（否则 DeepSeek 400）。

### 覆盖清单

ToolRegistry（注册/查找/重名/稳定顺序/校验）、calculator（正确计算 + 拒绝危险表达式）、parser（final/tool/保留 reasoning）、session 隔离与 message 重建、memory 写读搜索 + 路径越界被拒 + 索引重建、context builder 保留最近轮且不打散 tool 对、token 估算与压缩、compaction JSON 解析与回退、trace 白名单不含 credentials；外部 HTTP mock（Tavily 成功/401/429、高德成功/解析失败/adcode 直查、read_docs 文本/分块/不存在/二进制）；集成（直接回复、calculator、search、weather、read_docs、memory 跨 session、session 隔离、最大 loop、compaction、reasoning_content 回传）。



## 参考来源

本项目借鉴以下公开官方材料的**思想**，但不直接使用其中的 Agent Runtime / 框架：

1. OpenAI Codex harness / App Server 架构（core runtime 与 surface 分离）—— <https://openai.com/index/unlocking-the-codex-harness/>
2. OpenAI《A practical guide to building agents》（agent = 模型 + 工具 + 指令在循环中协作）—— <https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/>
3. OpenAI Function Calling（JSON Schema 描述工具、模型返回 tool call）—— <https://developers.openai.com/api/docs/guides/function-calling>
4. OpenAI Structured Outputs（JSON mode 不保证 schema，需应用侧校验）—— <https://developers.openai.com/api/docs/guides/structured-outputs>
5. OpenAI Compaction —— <https://developers.openai.com/api/docs/guides/compaction> ；Prompt Caching —— <https://developers.openai.com/api/docs/guides/prompt-caching>
7. DeepSeek Context Caching —— <https://api-docs.deepseek.com/guides/kv_cache>
8. Anthropic Memory Tool（独立于主上下文的持久文件系统，可跨 conversation 存取）—— <https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool>

