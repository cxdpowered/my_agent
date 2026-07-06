"""weather: real weather via AMap (高德地图) web service API.

Resolution chain:
  - 6-digit numeric location -> treated as adcode, query weather directly;
  - otherwise geocode first (v3/geocode/geo) -> take first result's adcode
    (note the chosen city), then query weather (v3/weather/weatherInfo).
"""
from __future__ import annotations

import httpx

from .base import ToolContext, ToolResult, ToolSpec

_GEO_URL = "https://restapi.amap.com/v3/geocode/geo"
_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"

_SCHEMA = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": "城市、区县、地址或 adcode，例如 武汉、洪山区、420100",
        },
        "extensions": {
            "type": "string",
            "enum": ["base", "all"],
            "description": "base 实况，all 预报，默认 base",
            "default": "base",
        },
    },
    "required": ["location"],
}


def _geocode(location: str, key: str) -> tuple[str | None, str | None, str | None]:
    """Return (adcode, formatted_city, error)."""
    try:
        resp = httpx.get(_GEO_URL, params={"address": location, "key": key}, timeout=15.0)
    except httpx.HTTPError as e:
        return None, None, f"地理编码请求失败: {e}"
    if resp.status_code >= 400:
        return None, None, f"地理编码返回 {resp.status_code}"
    data = resp.json()
    if data.get("status") != "1":
        return None, None, f"地理编码失败: {data.get('info')}"
    geocodes = data.get("geocodes") or []
    if not geocodes:
        return None, None, f"无法解析城市：{location}"
    first = geocodes[0]
    return first.get("adcode"), first.get("formatted_address") or location, None


def _run(arguments: dict, ctx: ToolContext) -> ToolResult:
    key = ctx.env.get("AMAP_API_KEY")
    if not key:
        return ToolResult(ok=False, content="", error="未配置 AMAP_API_KEY 环境变量")

    location = str(arguments["location"]).strip()
    extensions = arguments.get("extensions", "base")

    chosen_city = location
    if location.isdigit() and len(location) == 6:
        adcode = location
    else:
        adcode, chosen_city, err = _geocode(location, key)
        if err:
            return ToolResult(ok=False, content="", error=err)

    try:
        resp = httpx.get(
            _WEATHER_URL,
            params={"city": adcode, "extensions": extensions, "key": key},
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        return ToolResult(ok=False, content="", error=f"天气请求失败: {e}")
    if resp.status_code >= 400:
        return ToolResult(ok=False, content="", error=f"天气服务返回 {resp.status_code}")

    data = resp.json()
    if data.get("status") != "1":
        return ToolResult(ok=False, content="", error=f"天气查询失败: {data.get('info')}")

    if extensions == "all":
        forecasts = data.get("forecasts") or []
        if not forecasts:
            return ToolResult(ok=False, content="", error="未返回预报数据")
        fc = forecasts[0]
        city = fc.get("city", chosen_city)
        casts = fc.get("casts", [])
        today = casts[0] if casts else {}
        summary = (
            f"{city}未来天气：今天{today.get('dayweather','?')}，"
            f"{today.get('nighttemp','?')}-{today.get('daytemp','?')}°C。"
        )
        return ToolResult(ok=True, content=summary,
                          data={"city": city, "adcode": adcode, "forecasts": forecasts})

    lives = data.get("lives") or []
    if not lives:
        return ToolResult(ok=False, content="", error="未返回实况数据")
    live = lives[0]
    city = live.get("city", chosen_city)
    summary = (
        f"{city}当前{live.get('weather','?')}，{live.get('temperature','?')}°C，"
        f"{live.get('winddirection','?')}风{live.get('windpower','?')}级，"
        f"湿度{live.get('humidity','?')}%。（{live.get('reporttime','')}）"
    )
    return ToolResult(
        ok=True,
        content=summary,
        data={
            "city": city,
            "adcode": adcode,
            "weather": live.get("weather"),
            "temperature": live.get("temperature"),
            "winddirection": live.get("winddirection"),
            "windpower": live.get("windpower"),
            "humidity": live.get("humidity"),
            "reporttime": live.get("reporttime"),
        },
    )


def weather_tool() -> ToolSpec:
    return ToolSpec(
        name="weather",
        description="查询真实天气（高德地图）。支持城市名/区县/地址/adcode；extensions=base 实况，all 预报。",
        parameters_schema=_SCHEMA,
        handler=_run,
    )
