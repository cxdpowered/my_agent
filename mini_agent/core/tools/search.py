"""search: real web search via the Tavily API.

Returns a compact list of {title,url,content,score} to the LLM; the full
response goes to trace via `data`.
"""
from __future__ import annotations

import httpx

from .base import ToolContext, ToolResult, ToolSpec

_ENDPOINT = "https://api.tavily.com/search"

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "搜索查询"},
        "max_results": {
            "type": "integer",
            "description": "返回结果数量，默认 5",
            "default": 5,
        },
        "search_depth": {
            "type": "string",
            "enum": ["basic", "advanced"],
            "description": "搜索深度，默认 basic",
            "default": "basic",
        },
    },
    "required": ["query"],
}


def _run(arguments: dict, ctx: ToolContext) -> ToolResult:
    api_key = ctx.env.get("TAVILY_API_KEY")
    if not api_key:
        return ToolResult(ok=False, content="", error="未配置 TAVILY_API_KEY 环境变量")

    query = arguments["query"]
    body = {
        "query": query,
        "max_results": arguments.get("max_results", 5),
        "search_depth": arguments.get("search_depth", "basic"),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = httpx.post(_ENDPOINT, json=body, headers=headers, timeout=20.0)
    except httpx.HTTPError as e:
        return ToolResult(ok=False, content="", error=f"搜索请求失败: {e}")

    if resp.status_code == 401:
        return ToolResult(ok=False, content="", error="Tavily 鉴权失败(401)，请检查 TAVILY_API_KEY")
    if resp.status_code == 429:
        return ToolResult(ok=False, content="", error="Tavily 触发限流(429)，请稍后重试")
    if resp.status_code >= 400:
        return ToolResult(ok=False, content="", error=f"Tavily 返回错误 {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except Exception:
        return ToolResult(ok=False, content="", error="Tavily 响应不是合法 JSON")

    raw_results = payload.get("results", []) or []
    slim = [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "content": r.get("content"),
            "score": r.get("score"),
        }
        for r in raw_results
    ]
    if not slim:
        return ToolResult(ok=True, content=f"未找到关于“{query}”的结果。",
                          data={"query": query, "results": [], "raw": payload})

    lines = [f"“{query}”的搜索结果（{len(slim)} 条）："]
    for i, r in enumerate(slim, 1):
        lines.append(f"{i}. {r['title']} — {r['url']}\n   {(r['content'] or '')[:200]}")
    summary = "\n".join(lines)
    return ToolResult(ok=True, content=summary,
                      data={"query": query, "results": slim, "answer": payload.get("answer")})


def search_tool() -> ToolSpec:
    return ToolSpec(
        name="search",
        description="使用 Tavily 进行真实 Web 搜索，用于获取当前/外部信息或事实校验。",
        parameters_schema=_SCHEMA,
        handler=_run,
    )
