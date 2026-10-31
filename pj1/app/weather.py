"""季节和天气。

* 季节按当前月份（北半球）：春 3~5、夏 6~8、秋 9~11、冬 12~2。
* 天气有两处：**她那边**和**你那边**（坐标在设置页，默认值在角色配置里）。
  联网开关开着就用 Open-Meteo（免费、不需要 key，只发出经纬度）；关掉或取不到时，按设置里的「每月气候表」模拟，
  而且天气有延续性——今天下雨，明天更可能继续下，不是每天独立随机。
* 注入给模型的只有「天气现象 / 温度 / 季节」，**真实地名不会出现在任何提示词里**。
"""

from __future__ import annotations

import json
import logging
import os
import math
import random
import time
from datetime import datetime, date
from typing import Any

import httpx

from . import character as C
from . import db, registry

log = logging.getLogger(__name__)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
PLACES = ("her", "user")
KIND_LABEL = {
    "clear": "晴", "cloudy": "多云", "overcast": "阴", "fog": "有雾", "rain": "下雨",
    "thunder": "雷雨", "snow": "下雪",
}
WET_KINDS = ("rain", "thunder", "snow")
HIST_KEEP = 16                   # 每处保留最近多少次温度记录（算「24 小时降温」用）
_booted = False                  # 这次启动之后是不是已经拉过一次


def season(month: int | None = None) -> str:
    m = month or datetime.now().month
    return "春" if m in (3, 4, 5) else "夏" if m in (6, 7, 8) else "秋" if m in (9, 10, 11) else "冬"


# ───────────────────────── 状态 ─────────────────────────
def _load() -> dict[str, Any]:
    try:
        data = json.loads(db.get_settings().get("weather_state") or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save(state: dict[str, Any]) -> None:
    db.save_settings({"weather_state": json.dumps(state, ensure_ascii=False)})


def current(place: str) -> dict[str, Any] | None:
    w = _load().get(place)
    return w if isinstance(w, dict) and w.get("kind") in KIND_LABEL else None


def climate_row(place: str, month: int) -> dict[str, float]:
    return registry.get("climate_her" if place == "her" else "climate_user")[month - 1]


def month_avg(place: str, now: datetime | None = None) -> float:
    """当月平均气温（按日期在相邻月份之间插值，免得月初月末突变）。"""
    now = now or datetime.now()
    here = climate_row(place, now.month)["t"]
    frac = (now.day - 1) / 30.0 - 0.5
    other = climate_row(place, (now.month - 2) % 12 + 1 if frac < 0 else now.month % 12 + 1)["t"]
    return here + (other - here) * abs(frac)


# ───────────────────────── 模拟（离线兜底） ─────────────────────────
def simulate(place: str, sim: dict[str, Any] | None, now: datetime | None = None, rng: random.Random | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """按气候表生成下一个天气，带延续性。返回 (天气, 新的模拟内部状态)。"""
    rng = rng or random
    now = now or datetime.now()
    row = climate_row(place, now.month)
    r = min(0.95, max(0.0, row["rain"]))
    sim = dict(sim or {})
    gap_h = (time.time() - float(sim.get("at") or 0)) / 3600
    if "wet" not in sim or gap_h > 18:
        sim["wet"] = rng.random() < r                      # 隔了很久：按平均概率重新来
        sim["anom"] = rng.gauss(0, row["swing"] * 0.5)
    # 下雨的延续：停留概率 b，开始概率 a，使长期下雨比例约等于 r
    b = min(0.9, 0.6 + 0.3 * r)
    a = min(0.5, r * (1 - b) / max(0.05, 1 - r))
    sim["wet"] = rng.random() < (b if sim["wet"] else a)
    # 气温：当月平均 + 一天里的起伏 + 会慢慢回归的随机偏差
    sim["anom"] = 0.75 * float(sim.get("anom", 0.0)) + rng.gauss(0, row["swing"] * 0.35)
    diurnal = row["swing"] * 0.6 * math.cos((now.hour - 15) / 24 * 2 * math.pi)
    temp = month_avg(place, now) + diurnal + sim["anom"]
    if sim["wet"]:
        snow_ratio = min(0.9, row["snow"] / max(0.05, r) * 3)
        if rng.random() < snow_ratio or temp <= 0.5:
            kind, temp = "snow", min(temp, 1.0)
        elif temp >= 24 and rng.random() < 0.15:
            kind = "thunder"
        else:
            kind = "rain"
        temp -= 1.0 if kind != "snow" else 0.0
    else:
        x = rng.random()
        kind = "clear" if x < 0.45 else "cloudy" if x < 0.78 else "overcast" if x < 0.95 else "fog"
        if kind == "fog" and temp > 12:
            kind = "cloudy"
    sim["at"] = time.time()
    return {"kind": kind, "temp": round(temp, 1), "source": "sim"}, sim


# ───────────────────────── 联网 ─────────────────────────
def _code_to_kind(code: int) -> str:
    if code == 0:
        return "clear"
    if code in (1, 2):
        return "cloudy"
    if code == 3:
        return "overcast"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "thunder"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    return "cloudy"


async def fetch_online(lat: float, lon: float) -> dict[str, Any]:
    if os.environ.get("CHAT_OFFLINE") == "1":
        raise RuntimeError("CHAT_OFFLINE=1：测试 / 离线环境，不联网")
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(OPEN_METEO, params={"latitude": lat, "longitude": lon, "current": "temperature_2m,weather_code"})
        r.raise_for_status()
        cur = r.json()["current"]
    return {"kind": _code_to_kind(int(cur["weather_code"])), "temp": round(float(cur["temperature_2m"]), 1), "source": "online"}


# ───────────────────────── 刷新 ─────────────────────────
def due() -> bool:
    if not _booted:
        return True                                       # 应用启动后第一次：拉一次
    last = float(_load().get("fetched_at") or 0)
    return time.time() - last >= registry.num("weather_refresh_hours") * 3600


async def refresh(force: bool = False) -> dict[str, Any]:
    """更新两处天气并存起来。返回新的状态。联网失败的那一处自动改用模拟。"""
    global _booted
    _booted = True
    state = _load()
    now = datetime.now()
    online = registry.flag("weather_online")
    sims = dict(state.get("sim") or {})
    for place in PLACES:
        w = None
        if online:
            try:
                w = await fetch_online(registry.num(f"{place}_lat"), registry.num(f"{place}_lon"))
            except Exception as exc:  # noqa: BLE001 —— 断网 / 接口挂了：用模拟，不影响别的
                log.info("取天气失败（%s），改用模拟：%s", place, exc)
        sim = sims.get(place)
        if w is None:
            w, sim = simulate(place, sim, now)
        else:                                             # 联网拿到的也记进模拟状态，下次离线时接得上
            sim = {"wet": w["kind"] in WET_KINDS, "anom": float(w["temp"]) - month_avg(place, now), "at": time.time()}
        sims[place] = sim
        w["at"] = time.time()
        hist = list(state.get(f"{place}_hist") or [])
        hist.append([time.time(), w["temp"]])
        state[f"{place}_hist"] = hist[-HIST_KEEP:]
        state[place] = w
    state["sim"] = sims
    state["fetched_at"] = time.time()
    _save(state)
    return state


# ───────────────────────── 给别的模块用 ─────────────────────────
def kind(place: str = "her") -> str | None:
    w = current(place)
    return w["kind"] if w else None


def is_raining(place: str = "her") -> bool:
    return kind(place) in ("rain", "thunder")


def is_snowing(place: str = "her") -> bool:
    return kind(place) == "snow"


def is_hot(place: str = "her") -> bool:
    w = current(place)
    return bool(w and w["temp"] >= registry.num("weather_hot_c"))


def is_cold_snap(place: str = "her") -> bool:
    w = current(place)
    return bool(w and w["temp"] <= month_avg(place) - registry.num("weather_cold_snap_delta"))


def temp_drop_24h(place: str = "her") -> float:
    """和大约 24 小时前比，降了多少度（没记录、或没降就是 0）。"""
    hist = _load().get(f"{place}_hist") or []
    w = current(place)
    if not w or not hist:
        return 0.0
    target = time.time() - 24 * 3600
    old = min(hist, key=lambda h: abs(h[0] - target))
    if abs(old[0] - target) > 8 * 3600:
        return 0.0
    return max(0.0, float(old[1]) - float(w["temp"]))


def describe(place: str) -> str:
    w = current(place)
    if not w:
        return ""
    return f"{KIND_LABEL[w['kind']]}，{w['temp']:g}°C"


def feel(place: str = "her") -> str:
    """一句体感：热 / 冷 / 寒潮。"""
    if is_hot(place):
        return "很热"
    if is_cold_snap(place):
        return "骤然变冷（寒潮）"
    w = current(place)
    if not w:
        return ""
    t = w["temp"]
    return "冷" if t < 5 else "凉" if t < 14 else "舒服" if t < 25 else "热" if t < 31 else "很热"


def context_block() -> str:
    """注入每一轮的易变上下文：季节 + 两处天气。不含任何真实地名。"""
    her, user = current("her"), current("user")
    if not her and not user:
        return f"【季节】现在是{season()}季。"
    lines = [f"【季节与天气】现在是{season()}季。"]
    if her:
        lines.append(f"- 你这边：{describe('her')}（体感{feel('her')}）。")
    if user:
        lines.append(f"- 她那边{('（' + C.user_place() + '）') if C.user_place() else ''}：{describe('user')}。")
    lines.append(
        "天气是真的，会影响你的穿着、吃什么喝什么、想不想出门（比如冬天偏爱热食热饮、下雨不想出门）。"
        "不用每次都提，聊到相关的时候自然带一句；偶尔可以问问她那边的天气。"
    )
    return "\n".join(lines)


# ───────────────────────── 下雪 ─────────────────────────
def snow_started() -> bool:
    """她这边刚开始下雪、而且这场雪还没通知过：返回 True 并记下（每场雪只返回一次）。"""
    state = _load()
    snowing = is_snowing("her")
    if snowing and not state.get("snow_notified"):
        state["snow_notified"] = True
        _save(state)
        return True
    if not snowing and state.get("snow_notified"):
        state["snow_notified"] = False
        _save(state)
    return False


def sun_times(day: date | None = None, lat: float | None = None) -> tuple[float, float]:
    """日出 / 日落的本地钟点（小数小时）。只按纬度和日期算昼长，正午固定在 12:15 左右——她的地方是虚构的，不管经度和时区。"""
    day = day or datetime.now().date()
    lat = registry.num("her_lat") if lat is None else lat
    n = day.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(2 * math.pi * (284 + n) / 365)
    x = -math.tan(math.radians(lat)) * math.tan(decl)
    half = 12.0 if x <= -1 else 0.0 if x >= 1 else math.degrees(math.acos(x)) / 15
    return 12.25 - half, 12.25 + half


def ambient() -> dict[str, Any]:
    """窗外和房间的光线：此刻的天气种类、钟点、日出日落。页面按它画窗外的天空、夜里把房间调暗。"""
    now = datetime.now()
    w = current("her")
    rise, sets = sun_times(now.date())
    return {"kind": w["kind"] if w else "clear", "label": KIND_LABEL.get(w["kind"], "") if w else "", "temp": w["temp"] if w else None,
            "hour": now.hour, "minute": now.minute, "sunrise": round(rise, 2), "sunset": round(sets, 2), "season": season()}


def status() -> dict[str, Any]:
    """给设置页看的当前天气。"""
    state = _load()
    out: dict[str, Any] = {"season": season(), "online": registry.flag("weather_online"),
                           "fetched_at": state.get("fetched_at") or 0}
    for place in PLACES:
        w = current(place)
        out[place] = ({"label": KIND_LABEL[w["kind"]], "temp": w["temp"], "source": w.get("source", "")} if w else None)
    return out
