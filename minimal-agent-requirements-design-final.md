# 最小可用 Agent：需求与技术方案定稿（可实现版）

日期：2026-07-06

本文档是面试题“从零实现一个最小可用 Agent”的需求与方案定稿。目标不是复刻 LangGraph、OpenHands、OpenClaw 等现成 Agent 框架，而是自行实现一个可解释、可测试、可扩展的最小 Agent Runtime，并用 CLI 做表现层。

本版为“可直接实现”定稿：所有原先模糊、需要二次决策的点都已明确落地，实现者照本文即可动手写代码，无需再回头做架构决策。已明确的关键决策集中列在 §0，细节分散在各章。

---

## 0. 关键决策速查（实现前必读）

| 主题 | 决策 | 位置 |
|---|---|---|
| LLM | DeepSeek `deepseek-v4-pro` thinking mode，OpenAI-compatible Chat Completions | §7 |
| reasoning_content | tool-call 轮次必须原样回传，否则 DeepSeek 返回 400；保存于完整 message 内 | §7.2 |
| thought_summary | **从 tool_calls 模板生成**，绝不显示 reasoning_content（不泄露 CoT） | §6.4 |
| message 持久化 | `session_events.raw_json` 存**完整 OpenAI message 对象**，按时序回放即得合法 messages | §11.4 |
| MAX_AGENT_STEPS | 8，**按 LLM 调用次数计**；第 8 次即便返回 tool_calls 也不执行工具，直接收尾 | §6.3 |
| compaction 触发 | **每次 LLM 调用前**估算 token，超阈值才压缩 | §12.3 |
| token 估算 | 字符启发式 + 上一轮 `usage.prompt_tokens` 实测校正 | §12.3 |
| session summary | **取 `compactions` 表最新一行为当前 summary**，`sessions.last_summary_event_id` 为其镜像 | §12.2 |
| memory 作用域 | user-scoped，写入路径**强制限定在 `data/memories/{user_id}/` 下**（realpath 前缀校验） | §9.5 |
| memory 召回 | 写入时更新 `memory_index`；召回 query = 当前输入 + 最近 summary，关键词打分取 top K=5，注入固定块 | §9.5 / §12.1 |
| read_docs | **不限制路径**（唯一无限制的工具），README 明确标注 | §9.4 |
| 启动 session | **默认新建**；`-c/--continue` 接续最近；`-r/--resume [id]` 恢复指定或挑选 | §10.4 |
| 并发 | SQLite `WAL` + `busy_timeout=5000`（支持两窗口两进程） | §11.1 |
| trace 脱敏 | **白名单序列化**：只写入枚举的字段，credentials 结构上无法混入 | §13.2 |
| 工具参数校验 | 本地 JSON Schema 校验，不使用 DeepSeek beta strict mode | §8 |

---

## 1. 项目目标

实现一个从零构建的最小可用 Agent，具备以下能力：

- 接收用户输入；
- 让真实 LLM 判断直接回复还是调用工具；
- 执行工具；
- 根据工具结果继续 loop 或返回最终答案；
- 支持多用户、多 session 的隔离；
- 支持跨 session 的长期 memory；
- 支持上下文无损保存与阈值触发压缩；
- 支持真实搜索、真实天气、本地文档读取、计算器、memory 工具；
- 支持 trace 日志与 CLI 内查看；
- 构建测试用例验证主要功能。

最终交付内容包括：

- Python 代码仓库；
- 交互式 CLI；
- README；
- 系统设计说明；
- memory 召回时机与放置方式说明；
- AI Prompt 与问题解决记录；
- 测试用例；
- 终端操作录屏。

## 2. 参考来源与设计依据

本项目借鉴以下公开官方材料，但不直接使用其中的 Agent Runtime 或 Agent 框架。

1. OpenAI Codex harness / App Server 架构 —— core runtime 与 surface 分离。
   参考：https://openai.com/index/unlocking-the-codex-harness/

   本项目采用类似分层：

   ```text
   agent_core: runtime / loop / tools / session / memory / trace
   cli: user input / command parser / event renderer
   ```

2. OpenAI《A practical guide to building agents》—— agent = 模型 + 工具 + 指令在循环中协作。
   参考：https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/

   核心 loop：

   ```text
   user input -> build context -> call LLM -> parse -> (tool? execute & continue : return)
   ```

3. OpenAI Function Calling —— JSON Schema 描述工具、模型返回 tool call、应用执行并回传结果。
   参考：https://developers.openai.com/api/docs/guides/function-calling
   本项目采用 OpenAI-compatible function calling 协议。

4. OpenAI Structured Outputs —— JSON mode 只保证合法 JSON，不保证 schema；需要 adherence 时用应用侧校验。
   参考：https://developers.openai.com/api/docs/guides/structured-outputs
   本项目不使用 DeepSeek beta strict mode，本地做 JSON Schema 校验。

5. OpenAI Compaction 与 Prompt Caching —— 超阈值压缩、稳定前缀利于缓存。
   参考：
   - https://developers.openai.com/api/docs/guides/compaction
   - https://developers.openai.com/api/docs/guides/prompt-caching
   本项目采用“压缩前无损保存，阈值触发后压缩渲染上下文”。

6. DeepSeek Thinking Mode 与 Tool Calls —— `deepseek-v4-pro` 支持 thinking mode，`extra_body={"thinking":{"type":"enabled"}}` 启用；thinking 支持 tool calls；**发生 tool call 时 `reasoning_content` 必须在后续请求完整回传，否则 API 返回 400**。
   参考：
   - https://api-docs.deepseek.com/guides/thinking_mode
   - https://api-docs.deepseek.com/guides/tool_calls

7. DeepSeek Context Caching —— 复用前缀命中缓存。
   参考：https://api-docs.deepseek.com/guides/kv_cache

8. Anthropic Memory Tool —— memory 是独立于主上下文的持久文件系统，可跨 conversation 存取。
   参考：https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
   本项目采用 user-scoped memory 文件目录，允许跨 session 自动召回。

9. Tavily Search API。
   参考：
   - https://docs.tavily.com/documentation/api-reference/introduction
   - https://docs.tavily.com/documentation/api-reference/endpoint/search

10. 高德地图天气查询 API。
   参考：https://lbs.amap.com/api/webservice/guide/api/weatherinfo

## 3. 最终技术选型

```text
语言：Python 3.11+
运行形态：交互式 CLI
架构：core runtime + CLI presentation layer
LLM：DeepSeek 高质量 thinking 模型，默认 deepseek-v4-pro
LLM API 形态：OpenAI-compatible Chat Completions
strict mode：不使用 DeepSeek beta strict，本地校验工具参数
搜索：Tavily 真实 API
天气：高德地图真实 API
memory：Anthropic-style user-scoped memory，写入限定在 user 目录，允许跨 session 自动召回
todo：不实现
read_docs：读取指定路径，不限制路径范围
trace：CLI 内 /trace 命令 + logs/trace.jsonl（白名单序列化）
测试：大部分 mock LLM，少量真实 DeepSeek smoke test
存储：SQLite（WAL 模式）+ memory 文件目录
```

敏感信息处理：

- API key 只通过环境变量读取；不写入代码、README、录屏脚本文案、trace；
- trace 保留原始 LLM 请求 messages、响应、工具参数与结果，但**请求 headers、Authorization、API key、`.env` 原文永不记录**（靠白名单序列化从结构上保证，见 §13.2）。

## 4. 范围边界

### 4.1 必做

自实现：Agent Runtime、loop、工具注册、LLM 输出解析、session 管理、context 构建与压缩、memory 召回与写入、trace、CLI 表现层。
真实外部：DeepSeek API、Tavily 搜索、高德天气。
构建测试。

### 4.2 不做

不使用 LangGraph / LangChain Agent / OpenHands / OpenClaw；不做 Web UI、多 Agent、任务队列、权限系统、复杂 RAG；不实现 todo；read_docs 不做路径限制。

### 4.3 可使用的普通库

`openai`、`requests` 或 `httpx`、`pydantic` 或 `jsonschema`、`sqlite3`、`python-dotenv`、`pytest`、`rich`、`typer` 或 `argparse`。可选 `chardet`（read_docs 编码兜底）。

这些库不承担 Agent 主流程，只承担 API 调用、数据校验、存储、测试、CLI 渲染等通用能力。

## 5. 总体架构

```text
mini_agent/
  core/
    runtime.py          # Agent loop
    llm.py              # DeepSeek client wrapper（重试、参数、usage 提取）
    parser.py           # LLM 响应 -> AgentAction
    context.py          # ContextBuilder + token 估算 + compaction 触发
    compaction.py       # 压缩 prompt 调用与 summary 解析
    sessions.py         # user/session/event 持久化与 message 重建
    memory.py           # memory 文件存取 + 索引 + 关键词召回
    trace.py            # trace 白名单序列化 + JSONL 写 + 读
    store.py            # SQLite 连接（WAL）、schema 初始化
    tools/
      base.py           # ToolSpec / ToolRegistry / ToolContext / ToolResult
      calculator.py
      search.py
      weather.py
      read_docs.py
      memory_tool.py
  cli/
    app.py              # 交互式 REPL、启动参数解析
    commands.py         # /trace /sessions /new /use /memory 等
    render.py           # 终端渲染
  tests/
    unit/
    integration/
  docs/
    prompts.md
    problem-solving-log.md
  README.md
```

核心原则：

- `core` 不依赖 `cli`；CLI 只调用 `core` 暴露接口；
- 工具通过 registry 注册；
- session、memory、trace 都是 runtime 组成部分；
- 所有状态本地持久化。

## 6. Agent Runtime 设计

### 6.1 Loop 状态机

```text
START_RUN（生成 run_id）
  -> 持久化 user message（session_events）
  -> auto memory recall（关键词召回 top K）
  -> LOOP（step 从 1 开始）:
       -> maybe compact context（LLM 调用前判定，见 §12.3）
       -> build context（ContextBuilder 渲染 messages）
       -> call LLM（step += 1）
       -> parse assistant message -> AgentAction
       -> persist assistant message（含 reasoning_content，见 §7.2）
       -> if final_answer:
            write trace; mark run finished; return answer
       -> if tool_calls:
            if step >= MAX_AGENT_STEPS: 收尾并返回上限文案（不执行工具）
            execute tools; persist tool_result messages; write trace; continue
       -> if invalid:
            记录 parse 失败; 组一条纠错 tool/system 提示继续，或收尾（见 §15.1）
  -> on exception: 标记 run failed/partial，返回可恢复错误
```

### 6.2 run 与 step

- 每个用户输入触发一个 `run`（唯一 `run_id`）；
- 一个 run 内允许多次 LLM/tool 子循环。

### 6.3 最大循环限制

```text
MAX_AGENT_STEPS = 8   # 按“LLM 调用次数”计
```

- 每调用一次 LLM 记 1 step；
- 当已达到第 8 次调用且该次仍返回 tool_calls，则**不再执行工具**，直接收尾，返回：

```text
当前任务达到最大工具调用轮次，已停止继续执行。下面是已经完成的部分和最后一次工具结果。
```

原因：防死循环、便于测试、trace 可读。

### 6.4 内部 Action 类型

```python
class AgentAction:
    kind: Literal["final_answer", "tool_calls", "invalid"]
    content: str | None
    reasoning_content: str | None      # DeepSeek 原始 CoT，仅内部保存/回传，绝不显示
    thought_summary: str | None        # 展示给用户，来自 tool_calls 模板（见下）
    tool_calls: list[ToolCall]
    raw_response: dict                  # 用于 trace
```

**thought_summary 生成规则（决策）**：不从 `reasoning_content` 派生，改为**由 tool_calls 模板确定性生成**，避免暴露完整 CoT：

```text
单工具:  正在调用 {tool} …（附一句参数摘要，如 查询“武汉”的天气）
多工具:  正在调用 {tool_a}、{tool_b} …
无工具:  （final_answer 时不显示思考摘要）
```

CLI 只显示动作级摘要；`reasoning_content` 只进入 trace 和后续 API 回传。

## 7. LLM 设计

### 7.1 DeepSeek 调用方式

默认配置：

```text
base_url = https://api.deepseek.com
model    = deepseek-v4-pro
reasoning_effort = high
thinking = enabled
tool_choice = auto
max_tokens = 4096        # 显式设置，防超长
temperature = 未设置      # tool 场景用默认，不强制降温
```

SDK 调用形态：

```python
client.chat.completions.create(
    model=model,
    messages=messages,
    tools=tools,
    tool_choice="auto",
    reasoning_effort="high",
    max_tokens=4096,
    extra_body={"thinking": {"type": "enabled"}},
)
```

环境变量覆盖：

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_REASONING_EFFORT=high
```

重试策略（`llm.py`）：仅对 **429 / 5xx / 网络超时**重试，最多 2 次，指数退避（1s、2s）；4xx（除 429）、鉴权失败、parse 前的其他错误即时失败。每次 LLM 响应提取 `usage`（prompt_tokens/completion_tokens）与 provider 可能返回的缓存命中字段，供 trace 与 token 估算校正。

### 7.2 Thinking Mode / reasoning_content 处理（关键）

DeepSeek 约束：thinking 返回 `reasoning_content`；**assistant message 若含 tool_calls，后续每一轮请求必须原样携带该 message 的 reasoning_content，否则返回 400**；无 tool_calls 的轮次可省略，但本项目为实现统一与 trace 完整，一律保存完整 message。

runtime 落地：

```text
assistant message with tool_calls
  -> 以“完整 OpenAI message 对象”持久化（content + reasoning_content + tool_calls）
  -> 追加 tool_result messages（携带匹配的 tool_call_id）
  -> 下一次请求按时序回放，assistant.reasoning_content 原样带回
```

约束：compaction 压缩历史时，**任何“未闭合的 tool_call/tool_result 对”及其 assistant 的 reasoning_content 不得被压缩掉**（见 §12.3）。

### 7.3 Prompt 设计

系统 prompt 目标：说明能力、可用工具、工具使用原则、memory 写入原则、不泄露完整 CoT、final answer 直接简洁、工具失败要解释并给替代方案。

核心规则：

```text
你是一个 CLI Agent。你可以直接回答，也可以调用工具。
你必须根据工具 schema 调用工具。
你不需要解释完整隐藏推理；如需展示，只给一句简短摘要。
长期事实、用户偏好、跨 session 有价值的信息可写入 memory；普通临时对话不要写入。
工具失败时，解释失败原因并给出替代方案。
```

完整 prompt 文本落在 `docs/prompts.md`。

## 8. 工具系统设计

### 8.1 ToolSpec

```python
class ToolSpec:
    name: str
    description: str
    parameters_schema: dict                      # JSON Schema，用于本地校验与 API tools 参数
    handler: Callable[[dict, ToolContext], ToolResult]
```

ToolRegistry：注册、按名查找、重复名称报错；`to_openai_tools()` 输出**稳定顺序**的 tools 数组（利于缓存）。

### 8.2 ToolContext

```python
class ToolContext:
    user_id: str
    session_id: str
    run_id: str
    store: StateStore        # SQLite 访问
    memory: MemoryStore      # memory 存取/索引
    trace: TraceWriter
    env: Mapping[str, str]   # 只读环境变量视图（用于取 API key）
```

### 8.3 ToolResult

```python
class ToolResult:
    ok: bool
    content: str             # 回传给 LLM 的紧凑摘要（由 handler 生成，见下）
    data: dict | list | None # 结构化数据，进 trace；可选择性回传
    error: str | None
```

**摘要由 handler 负责生成**：回传给 LLM 的 tool message 保持紧凑，`content` 是人读/模型读友好的短摘要，`data` 与完整原始结果进 trace。回传示例：

```json
{"ok": true, "tool": "weather", "summary": "武汉今天多云，25-32°C。", "data": {"...": "..."}}
```

工具执行前先做 JSON Schema 校验（`jsonschema`/`pydantic`），校验失败直接返回 `ok=false`，错误作为 tool result 回传 LLM 让其纠正参数（见 §15.2）。

## 9. 工具清单

### 9.1 calculator

用途：基础数学计算（加减乘除、括号、幂、常见函数）。

参数：

```json
{"type":"object","properties":{"expression":{"type":"string","description":"要计算的数学表达式，例如 (12 + 8) * 3"}},"required":["expression"]}
```

安全实现（明确白名单）：

- 不使用裸 `eval`，用 `ast.parse` + 递归求值；
- 允许的运算符：`+ - * / // % **` 与一元 `+ -`、括号；
- 允许的函数/常量：`sqrt log log10 exp sin cos tan asin acos atan floor ceil abs round pow`、`pi e`（映射到 `math`）；
- 禁止：变量名（除上列常量）、属性访问、下标、调用非白名单名、lambda、comprehension；
- 防 DoS：`**` 指数上限（如绝对值 ≤ 1e6），结果非有限（inf/nan）返回错误。

### 9.2 search（Tavily）

用途：真实 Web 搜索，用于当前/外部信息、事实校验。

参数：

```json
{"type":"object","properties":{
  "query":{"type":"string","description":"搜索查询"},
  "max_results":{"type":"integer","description":"返回结果数量，默认 5","default":5},
  "search_depth":{"type":"string","enum":["basic","advanced"],"description":"搜索深度，默认 basic","default":"basic"}
},"required":["query"]}
```

配置：`TAVILY_API_KEY`。

实现：

- `POST https://api.tavily.com/search`，`Authorization: Bearer <key>`；
- 请求体传 `query / max_results / search_depth`；不附加匿名用户标识/`X-Session-Id`（MVP 省略）；
- 回传给 LLM 只保留 `title / url / content(snippet) / score`，不默认拉全文；
- 完整响应进 trace。

### 9.3 weather（高德）

用途：真实天气查询。

参数：

```json
{"type":"object","properties":{
  "location":{"type":"string","description":"城市、区县、地址或 adcode，例如 武汉、洪山区、420100"},
  "extensions":{"type":"string","enum":["base","all"],"description":"base 实况，all 预报","default":"base"}
},"required":["location"]}
```

配置：`AMAP_API_KEY`。

实现（明确解析链）：

- 若 `location` 为 6 位数字 → 视作 adcode，直接查天气；
- 否则先地理编码：`GET https://restapi.amap.com/v3/geocode/geo?address=<location>&key=<key>`，取首个结果的 `adcode`（多结果取第一个，并在回答中注明采用的 city）；地理编码失败 → 返回 `ok=false`，提示无法解析城市；
- 再查天气：`GET https://restapi.amap.com/v3/weather/weatherInfo?city=<adcode>&extensions=<base|all>&key=<key>`。

返回字段：`city / adcode / weather / temperature / winddirection / windpower / humidity / reporttime`；`extensions=all` 时附 `forecasts`。`content` 摘要由 handler 组装（如“武汉今天多云，25-32°C”）。

### 9.4 read_docs

用途：读取用户指定路径下的文档，让 Agent 基于本地文件回答。

**用户已确认：read_docs 不限制路径**（本项目唯一无限制的工具）。

参数：

```json
{"type":"object","properties":{
  "path":{"type":"string","description":"要读取的本地文件路径，可为绝对或相对路径"},
  "start_line":{"type":"integer","description":"从第几行开始读取，默认 1","default":1},
  "max_chars":{"type":"integer","description":"最多返回字符数，默认 20000","default":20000}
},"required":["path"]}
```

实现细节：

- 不限制路径，允许读取当前进程有权限访问的任意文件；
- 编码检测顺序：`utf-8` → `gbk` →（可选）`chardet` 兜底；
- 二进制判定：检测 NUL 字节或解码失败 → 返回 `ok=false`；
- 大文件按 `start_line` 与 `max_chars` 分块返回，并在 `data` 标记是否被截断、下一个 `start_line`；
- trace 记录读取路径、范围、字符数。

风险说明：能力较大，仅适合本地受信任 CLI demo；README 明确这是用户主动授权的本地行为；文件内容不自动写入 memory，除非模型明确调用 memory 工具且符合写入规则。

### 9.5 memory

用途：跨 session 保存与召回长期信息，参考 Anthropic Memory Tool，与主对话 history 分离。

作用域：

```text
memory        = user-scoped
session history = session-scoped
```

即：同一用户不同 session 共享 memory；不同用户隔离；session 普通聊天不跨 session；只有写入 memory 的长期信息才被跨 session 召回。

参数：

```json
{"type":"object","properties":{
  "operation":{"type":"string","enum":["search","list","read","write","append"],"description":"memory 操作"},
  "query":{"type":"string","description":"search 时的查询"},
  "path":{"type":"string","description":"memory 文件相对路径，例如 preferences.md"},
  "content":{"type":"string","description":"write/append 写入的内容"}
},"required":["operation"]}
```

**路径安全（决策）**：`path` 一律相对 `data/memories/{user_id}/` 解析，`os.path.realpath` 后校验仍在该前缀下，拒绝绝对路径与 `..` 穿越。read_docs 无限制、memory 受限，这一切分在 README 写明。

memory 文件目录：

```text
data/memories/{user_id}/
  profile.md
  preferences.md
  projects.md
  facts.md
```

写入原则：用户明确“记住”、用户偏好、长期身份/项目背景、常用城市/格式/长期约束、可跨 session 复用的信息。
不写入：临时闲聊、一次性任务结果、API key、密码、身份证/银行卡等高敏信息、未经确认的推测。

**索引与召回（决策，MVP 关键词）**：

- 索引更新点唯一：memory 的 `write/append` 成功后，对该 `path` 文件按标题(`##`)或段落切块，`DELETE + INSERT` 重建 `memory_index` 中该文件对应行；
- 自动召回：每次 run 开始，`query = 当前用户输入 + 最近 session summary`；对 `memory_index.chunk` 做规范化关键词匹配（小写化、去标点，中文按子串），按命中词数打分，`updated_at` 作 tiebreak，取 `MEMORY_TOP_K=5`；
- 注入：召回结果以固定块拼在 system prompt 之后（见 §12.1），格式：

```text
【长期记忆】
- (preferences.md) 写周报喜欢先结论后细节
- (facts.md) 常用城市：武汉
```

- LLM 仍可主动调用 memory 工具做 search/read/write/append/list。

## 10. User / Session 管理

### 10.1 三层标识

```text
user_id      真实/本地用户身份，例如 za
session_id   一个聊天窗口或任务上下文
run_id       用户每输入一次消息触发的一次 agent loop
```

### 10.2 作用域

```text
users / sessions        : user-scoped
conversation / summary  : session-scoped
tool traces             : run-scoped
memory                  : user-scoped
```

窗口 1 与窗口 2 不共享 conversation，但都能召回同一 user 的 memory。

### 10.3 本地用户模型

MVP 不做登录认证。默认 `user_id = 当前操作系统用户名`（`getpass.getuser()`）。

CLI 显式指定：`python -m mini_agent --user za`。

交互命令：`/whoami`、`/user <user_id>`。

切换 user 后：current session 置空（按 §10.4 默认新建）；memory scope 切换；不混用上一个 user 的 session。

### 10.4 Session 生命周期与启动（决策）

session 字段：

```text
session_id / user_id / title / created_at / updated_at / archived / last_summary_event_id
```

CLI 命令：

```text
/new [title]          创建新 session 并切换
/sessions             查看当前 user 的 session 列表
/use <session_id>     切换 session
/rename <title>       重命名当前 session
/archive              归档当前 session
```

**启动行为（Claude Code 风格）**：

```text
默认（无 session 相关参数）        : 新建一个 session 进入
-c / --continue                   : 接续当前 user 最近一个未归档 session；若无则新建
-r / --resume [session_id]        : 恢复指定 session_id；不带 id 时列出最近 session 供选择
--session <session_id>            : 直接进入指定 session（等价 -r <id> 的非交互形式）
```

优先级：`--session`/`-r <id>` > `-r`(交互挑选) > `-c` > 默认新建。启动后在终端打印一行当前 user / session 提示。

## 11. 存储设计

### 11.1 SQLite（WAL）

主状态：`data/agent.sqlite`。连接初始化执行：

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
```

以支持两窗口两进程并发（§14.3 演示）。写入用短事务。

建表：

```sql
users(
  user_id text primary key,
  display_name text,
  created_at text
);

sessions(
  session_id text primary key,
  user_id text not null,
  title text,
  archived integer default 0,
  created_at text,
  updated_at text,
  last_summary_event_id integer
);

session_events(
  event_id integer primary key autoincrement,
  session_id text not null,
  run_id text,
  role text,                 -- user / assistant / tool / system
  event_type text,           -- user_message / assistant_message / tool_result / system_note
  content text,              -- 展示/检索用冗余副本
  raw_json text,             -- 完整 OpenAI message 对象（回放的唯一权威来源）
  created_at text
);

runs(
  run_id text primary key,
  user_id text not null,
  session_id text not null,
  user_input text,
  status text,               -- running / finished / failed / partial
  started_at text,
  ended_at text,
  error text
);

tool_traces(
  trace_id integer primary key autoincrement,
  run_id text not null,
  session_id text not null,
  tool_name text,
  arguments_json text,
  result_json text,
  ok integer,
  started_at text,
  ended_at text,
  error text
);

compactions(
  compaction_id integer primary key autoincrement,
  session_id text not null,
  covered_until_event_id integer,   -- 该 summary 覆盖到哪个 event
  summary text,                     -- 结构化 summary 的 JSON 文本
  created_at text
);

memory_index(
  id integer primary key autoincrement,
  user_id text not null,
  path text not null,
  chunk text,
  updated_at text
);
```

### 11.2 Memory 文件

保存于 `data/memories/{user_id}/`；`memory_index` 为其关键词检索索引，随写入重建（§9.5）。MVP 用关键词检索，后续可升级 FTS 或 embedding。

### 11.3 Trace JSONL

同时写入 `logs/trace.jsonl`，每行一个 JSON 对象：

```json
{"timestamp":"...","run_id":"...","user_id":"...","session_id":"...","event":"llm_response","payload":{}}
```

trace.jsonl 是可观察性日志，不是唯一状态源（权威状态在 SQLite）。

### 11.4 session_events ↔ messages 重建（关键）

- 写入：每条 user/assistant/tool 消息落一行 `session_events`，`raw_json` 存**完整 OpenAI message 对象**（assistant 含 `content + reasoning_content + tool_calls`；tool 含 `tool_call_id + content`）。`content` 列只是冗余副本，不参与回放。
- 读取：按 `session_id`、`event_id` 升序取 `raw_json` 直接拼成 messages 数组即为合法请求序列——`tool_calls` assistant 后自然紧跟匹配 `tool_call_id` 的 tool messages，无需额外配对逻辑。
- ContextBuilder 在此基础上做“system + summary + memory + 最近窗口”的裁剪（§12），但绝不打散未闭合的 tool 配对。

## 12. Context 管理

### 12.1 Context 组成

每次 LLM 请求由 `ContextBuilder` 渲染，顺序稳定（利于缓存）：

```text
1. system prompt（稳定）
2. 【长期记忆】memory snippets 固定块
3. session summary（若有，取最新 compaction 行）
4. recent conversation turns（最近 RECENT_TURNS_TO_KEEP）
5. current user message
6. 本 run 内产生的 assistant(tool_calls, reasoning_content) 与 tool_result messages
```

工具 schema 通过 API 顶层 `tools` 参数传入，顺序稳定。

### 12.2 session summary 与 compactions 的关系（决策）

- **当前 summary = `compactions` 表中该 session `covered_until_event_id` 最大的一行**；
- `sessions.last_summary_event_id` 是该 `covered_until_event_id` 的镜像，便于快速判断“哪些 event 已被 summary 覆盖”；
- 渲染时 context = `summary` + `covered_until_event_id` 之后的 events（再叠加最近窗口与本 run 消息）。

### 12.3 压缩策略（决策）

原则：

```text
压缩前无损保存；接近阈值才压缩；压缩后不删原始 event log；压缩只影响后续 prompt 渲染。
```

默认参数：

```text
CONTEXT_COMPACT_THRESHOLD_TOKENS = 60000
RECENT_TURNS_TO_KEEP = 8
MEMORY_TOP_K = 5
```

**触发时机**：在**每次 LLM 调用前**估算当前将要发送的 rendered context token 数；超阈值才压缩。

**token 估算**：字符启发式（CJK≈按每字 ~1.6 token、ASCII≈每 4 字符 ~1 token 的粗略系数求和）作为基线；并用**上一轮 API 返回的 `usage.prompt_tokens` 与本地估算的比值**做在线校正系数，逐轮逼近真实值。不追求精确。

**压缩流程**：

```text
estimate rendered tokens
  -> 若低于阈值: 不压缩
  -> 若高于阈值:
       选取 recent window 之前、且不破坏未闭合 tool 配对的旧 events
       调用 LLM 生成结构化 session summary（见 compaction prompt）
       写入 compactions（covered_until_event_id = 被覆盖的最大 event_id）
       更新 sessions.last_summary_event_id
       后续 context 使用 summary + 之后的 events
```

**不可压缩的部分**：最近 `RECENT_TURNS_TO_KEEP` 轮、以及任何未闭合的 `assistant(tool_calls)/tool_result` 对（含其 reasoning_content），必须保留，避免 DeepSeek 400。

**summary 结构（决策：本地 JSON，不用 strict）**：让 LLM 输出如下字段的 JSON，本地 `json.loads` 解析，解析失败则回退为“原文本 summary”并记 trace：

```text
用户目标 / 已完成事项 / 未完成事项 / 关键事实 / 关键文件路径 / 关键工具结果 / 用户偏好 / 需保留的指代关系
```

### 12.4 Cache 友好

- system prompt 稳定；tool schema 顺序稳定；
- 动态 memory snippets 放 system 之后；recent messages 按时间追加；
- 不每轮重写完整历史；压缩只在阈值触发；压缩结果稳定保存不每轮重生成。

## 13. Trace 设计

### 13.1 CLI /trace

```text
/trace            当前 session 最近一个 run 的 trace
/trace last       同上
/trace <run_id>   指定 run
/trace --json     以 JSON 展示
```

展示内容：run_id、user_id、session_id、用户输入、LLM 请求 messages、原始响应、reasoning_content、tool calls、tool arguments、tool results、final answer、error、token usage、cache 命中字段（若 provider 返回）。

### 13.2 trace.jsonl 与白名单序列化（决策）

事件类型：

```text
run_started / memory_recalled / context_built / compaction_started / compaction_finished
llm_request / llm_response / action_parsed / tool_started / tool_finished / tool_failed
run_finished / run_failed
```

脱敏做法：payload 采用**白名单序列化**——每种事件只写入枚举允许的字段（messages、tool 参数、tool 结果、usage 等业务内容），**从不序列化** request headers、Authorization、api_key、`.env` 原文。即“不是事后擦除，而是结构上无法混入”。用户要求业务内容不脱敏，故 prompt/工具参数/结果原文保留。

## 14. CLI 设计

### 14.1 启动

```bash
python -m mini_agent
python -m mini_agent --user za
python -m mini_agent --user za -c                 # 接续最近 session
python -m mini_agent --user za -r                 # 列出最近 session 供选择
python -m mini_agent --user za -r <session_id>    # 恢复指定 session
python -m mini_agent --user za --session <id>     # 非交互进入指定 session
```

只提供交互式模式，不提供一次性 `ask` 命令。

### 14.2 REPL 命令

```text
/help                 查看命令
/whoami               查看当前 user
/user <user_id>       切换 user
/new [title]          创建 session
/sessions             列出 session
/use <session_id>     切换 session
/rename <title>       重命名 session
/archive              归档当前 session
/memory               查看 memory 文件列表
/memory search <q>    搜索 memory
/trace [run_id]       查看 trace
/exit                 退出
```

普通输入直接交给 Agent：

```text
武汉明天天气怎么样？
查一下 Tavily 是什么，并总结三点
读取 C:\Users\za\Desktop\xxx.md，然后帮我总结
记住：我写周报喜欢先写结论再写细节
```

### 14.3 两窗口隔离演示

窗口 1：`/new weather` → 问天气 → 触发 weather。
窗口 2：`/new weekly` → 读周报文档 → 触发 read_docs。

验证：两窗口 conversation 互不进入；窗口 1 写入 memory（“记住我常用城市是武汉”）后，窗口 2 能自动召回。两进程共用 `agent.sqlite`（WAL）不冲突。

## 15. 异常处理

### 15.1 LLM 异常

覆盖：API key 缺失、网络失败、超时、429、5xx、provider 返回格式异常、thinking/tool 拼接错误（400）。
策略：trace 写完整错误；CLI 返回简洁错误；可重试错误（429/5xx/超时）最多重试 2 次；不无限重试。parse 得到 `invalid` 时，记录并可组一条纠错提示再试一次，仍失败则收尾。

### 15.2 工具异常

覆盖：工具不存在、参数 JSON 解析失败、schema 校验失败、Tavily 401/429/5xx、高德 key 错误/城市解析失败、read_docs 不存在/编码失败/二进制、calculator 非法表达式、memory 读写失败/路径越界。
策略：工具返回 `ok=false` 的结构化结果；错误作为 tool result 回传 LLM，允许其修正参数或向用户解释。

### 15.3 Runtime 异常

覆盖：session 不存在、user 切换导致 session 不匹配、SQLite 写入失败、compaction 失败、超过最大 loop。
策略：不丢失已写入 event；run 标记 failed/partial；trace 记录错误。compaction 失败时回退到“不压缩、直接用原始 events”，不阻断本次回答。

## 16. 测试方案

### 16.1 Unit Tests

ToolRegistry 注册/查找/重名报错；calculator 正确计算 + 拒绝危险表达式（属性访问、超大幂、非法名）；parser 解析 final answer / tool calls / 保留 reasoning_content；JSON Schema 校验；session 创建/切换/隔离；session_events → messages 重建（tool 配对正确）；memory 写入/读取/搜索 + 路径越界被拒；memory 索引在写入后重建；context builder 保留最近 turns 与不打散 tool 对；token 估算与 compaction 阈值判断；compaction summary JSON 解析与回退；trace 白名单序列化不含 credentials；trace writer 写 JSONL。

### 16.2 Tool Tests（mock 外部 HTTP）

Tavily 成功 / 401 / 429；高德天气成功 / 城市解析失败 / adcode 直查；read_docs 文本 / 大文件分块 / 不存在 / 二进制。

### 16.3 Integration Tests（fake LLM 固定返回）

1. 直接回复（无工具）；
2. calculator：12*8 → 96 → final；
3. search：tool_call → mock results → 总结；
4. weather：tool_call → mock weather → 回答；
5. read_docs：tool_call → 文件内容 → 总结；
6. memory 跨 session：session A 写“常用城市武汉”，session B 追问 → 自动召回武汉；
7. session 隔离：A 谈天气、B 谈周报，B 的 context 不含 A 的普通 conversation；
8. 最大 loop：fake LLM 一直返回 tool_call → 第 8 次 LLM 调用后停止、不再执行工具；
9. compaction：构造长 session 触发压缩 → 原始 event 不删、rendered context 用 summary + recent turns、未闭合 tool 对保留；
10. reasoning_content 回传：含 tool_call 的 assistant 在下一轮请求中带回 reasoning_content（断言 messages 结构）。

### 16.4 Real LLM Smoke Tests

只做少量：直接回复、calculator、weather、search。默认跳过，环境变量齐全时手动运行：

```bash
pytest -m live
```

原因：外部 API 不稳定、成本高、CI 不应依赖真实 LLM。

## 17. README 必须包含

```text
# Minimal Agent Runtime
## 项目目标
## 为什么不使用现有 Agent 框架
## 架构设计
## Agent Loop
## 工具注册机制
## DeepSeek Thinking Mode 与 reasoning_content 处理
## Session / User 管理（含 -c/-r 恢复）
## Memory 设计（user-scoped，路径限定）
## Context 与 Compaction
## Trace 日志（白名单序列化）
## 运行方式
## 环境变量
## CLI 命令
## 安全说明（read_docs 不限路径 vs memory 限定 user 目录）
## 测试方式
## 录屏演示步骤
## 已知限制
## 参考来源
```

环境变量示例只写占位：

```env
DEEPSEEK_API_KEY=your_deepseek_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
TAVILY_API_KEY=your_tavily_key
AMAP_API_KEY=your_amap_key
```

## 18. Prompt 与问题解决记录

提交 `docs/prompts.md`（system prompt、compaction prompt、memory 写入判断 prompt、fake LLM 测试样例）与 `docs/problem-solving-log.md`（为什么 core+CLI 分层、为什么 DeepSeek thinking、为什么不用 strict beta、为什么 memory user-scoped、为什么 todo 不实现、为什么 read_docs 不限路径而 memory 限定、如何处理 reasoning_content、如何设计 trace 白名单、如何设计 session 隔离与 message 重建、如何设计 compaction 与 token 估算、启动 -c/-r 恢复设计）。

## 19. 录屏演示脚本

1. 展示 README 与项目结构；
2. 设置环境变量；
3. 启动 CLI（默认新建 session）；
4. `/new weather-demo`；
5. 问天气，展示 weather tool call；
6. 问搜索，展示 search tool call；
7. 读本地文档，展示 read_docs tool call；
8. 让 Agent 记住一个长期偏好，展示 memory 写入；
9. 退出并用 `-c` 或新窗口启动（进入新 session）；
10. 追问长期偏好，展示 memory 自动召回；
11. 用 `-r` 恢复第 4 步的 weather-demo，展示历史 conversation 回来；
12. 展示两个 session 的 conversation 不串；
13. `/trace` 展示 trace；打开 `logs/trace.jsonl` 展示结构化日志（无 credentials）；
14. 运行 `pytest`。

## 20. 最终验收标准

1. Agent Loop 自己实现？是，`core/runtime.py`：LLM → parse → tool → loop → final。
2. 依赖现成 Agent 框架？否。
3. LLM 真实？是，DeepSeek API。
4. 工具由模型自主选择？是，OpenAI-compatible function calling。
5. 有工具注册机制？是，`ToolRegistry`。
6. 解析 LLM 输出？是，content / reasoning_content / tool_calls / final / 错误结构。
7. session 隔离？是，conversation/summary/runs 绑 session_id。
8. memory 跨 session？是，绑 user_id，同用户不同 session 自动召回。
9. context 能压缩？是，阈值触发、原始 event 无损保留。
10. 有 trace？是，`/trace` + `logs/trace.jsonl`，白名单序列化。
11. 有测试？是，mock LLM 为主，真实 smoke 为辅。
12. reasoning_content 正确回传？是，含 tool_call 的 assistant 后续请求原样携带。
13. 能恢复历史 session？是，`-c` 接续最近、`-r` 恢复指定/挑选。

## 21. 已知限制

- 不是生产级认证系统；
- memory 检索 MVP 用关键词，后续可换 FTS/embedding；
- read_docs 不限路径，适合本地受信任 CLI，不宜暴露为远程服务；memory 则限定 user 目录；
- trace 保留业务原文（不含 credentials），不宜上传公共仓库；
- DeepSeek thinking mode 成本更高；
- token 估算为启发式，非精确；
- compaction summary 可能损失细节，但原始 event log 完整保存；
- 外部 API 错误会影响 live demo。

## 22. 最终定稿结论

```text
Python CLI
  -> 自研 Agent Runtime（自实现 loop / parse / registry）
  -> DeepSeek v4-pro thinking mode（正确回传 reasoning_content）
  -> OpenAI-compatible function calling + 本地 JSON Schema 校验
  -> calculator / search / weather / read_docs / memory
  -> SQLite(WAL) session store + 完整 message 回放
  -> user-scoped memory 文件（路径限定 + 关键词索引召回）
  -> threshold-based compaction（每次 LLM 前判定 + 保留未闭合 tool 对）
  -> /trace + trace.jsonl（白名单序列化，无 credentials）
  -> 启动 -c/-r 恢复历史 session
  -> pytest with mock LLM（+ live smoke）
```

工程亮点：core 与表现层分离；自实现 loop；schema-based tool calling；thinking model 的 reasoning_content 处理；session 隔离与 message 无损回放；user-level memory 与自动召回；无损 event log 与阈值压缩；trace 白名单可观测性；mock-first 测试；Claude Code 风格 session 恢复。
