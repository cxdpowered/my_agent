# 录屏演示指令


## 1. 开场验证项目和测试

```powershell
cd C:\Users\za\Desktop\my_agent

python -m pytest

python -m pytest -m live -q
```


默认 pytest 是 mock-first 测试，不触发真实外部 API。
python -m pytest -m live -q 跑的是真实外部 API smoke 测试，不是 mock。
它会真实调用 DeepSeek 做直接回复，再验证 DeepSeek + calculator、DeepSeek + 高德 weather、DeepSeek + Tavily search 这四条链路。
这个命令证明真实 LLM API、工具调用和 runtime loop 能实际跑通；完整功能覆盖则看普通 pytest。


## 2. 准备干净演示数据

```powershell
$env:MINI_AGENT_DATA_DIR = "$PWD\.demo_data"
$env:MINI_AGENT_LOGS_DIR = "$PWD\.demo_logs"
Remove-Item -Recurse -Force .demo_data,.demo_logs -ErrorAction SilentlyContinue

```


这里把演示数据放到 .demo_data 和 .demo_logs，避免污染正式 data/ 与 logs/。
demo_doc.md 用于演示 read_docs 工具读取本地文件。


## 3. 启动 Agent 并演示基本能力

```powershell
python -m mini_agent --user video_demo3
```


```text
你好，用一句话介绍你能做什么。
```

```text
用计算器算一下 (123+877)*2 等于多少？
```

```text
搜索一下 Tavily 是什么，并总结一句话。
```

```text
武汉今天天气怎么样？
```

```text
读取C:\Users\za\Desktop\my_agent\mini_agent\docs\prompts.md ，并总结三点。
```

```text
记住：我录制 demo 时喜欢先展示测试通过，再展示工具调用。
```

```text
/trace
```

```text
/memory
```

```text
/sessions
```

```text
/exit
```




## 4. 演示新 session 下的 memory 召回

```powershell
python -m mini_agent --user video_demo3
```

进入交互界面后输入：

```text
我录制 demo 的偏好是什么？
```

```text
/exit
```

说明点：

```text
这是一个新的 session。普通对话不跨 session，但 user-scoped memory 会在每次 run 开始前自动召回。
```

## 5. 演示多session互不干扰
```powershell
python -m mini_agent --user video_demo3
```

```text
我回复1你回复2
```
```text
我回复1你回复3
```



