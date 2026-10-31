"""角色姿势调试器的数据（房间页面 Ctrl+Shift+P）：每个状态、每个视角，角色图的偏移 / 旋转 / 大小 / 镜像。

存在数据目录的 room_pose_tuning.json：
    { "<状态键>[#做法序号]": { "room": {"dx": 0, "dy": 0, "rot": 0, "scale": 1, "flip": false}, "desk": {...} }, ... }
dx / dy 是画面上的左右 / 上下（按那个位置的「厘米」换算成像素，所以缩放画面也不会跑偏）；rot 是度数（顺时针为正）；scale 是在原来大小上再乘。
调好以后让 Claude 把它并进 manifest.json 当默认值。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .config import DATA_DIR

PATH = Path(DATA_DIR) / "room_pose_tuning.json"
FIELDS = {"dx": (-200.0, 200.0, 0.0), "dy": (-200.0, 200.0, 0.0), "rot": (-180.0, 180.0, 0.0), "scale": (0.3, 3.0, 1.0)}


def load() -> dict[str, Any]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean_view(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    out: dict[str, Any] = {}
    for k, (lo, hi, dflt) in FIELDS.items():
        try:
            x = float(v.get(k, dflt))
        except (TypeError, ValueError):
            raise ValueError(f"{k} 不是数字") from None
        if not math.isfinite(x):
            raise ValueError(f"{k} 不是数字")
        x = max(lo, min(hi, x))
        if x != dflt:
            out[k] = round(x, 2)
    if v.get("flip"):
        out["flip"] = True
    return out or None


def clean(data: Any) -> dict[str, Any]:
    """校验整份数据：只留认识的视角和字段，默认值不存。"""
    if not isinstance(data, dict):
        raise ValueError("数据格式不对")
    out: dict[str, Any] = {}
    for key, views in data.items():
        if not isinstance(key, str) or not key or len(key) > 60 or not isinstance(views, dict):
            raise ValueError(f"状态「{key}」的数据格式不对")
        vv = {}
        for view in ("room", "desk"):
            c = _clean_view(views.get(view))
            if c:
                vv[view] = c
        if vv:
            out[key] = vv
    return out


def save(data: Any) -> dict[str, Any]:
    out = clean(data)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
