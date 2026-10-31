"""群聊的「闸门」：每条群消息先算一个回复概率，过了才调用模型。

回复概率 = 基础概率 × 状态系数 × 忙碌系数 × 心情系数 × 冷却系数
  * 基础概率按消息类型：
      - 主人 @ 她：**不走概率**，每句都回，也不计入发言频率（forced）；
      - 别人 @ 她 / 回复她：0.85；
      - 提到关键词（她的名字、她感兴趣的话题……）：用「关键词」表里那一行的概率，别人说的和主人说的不一样（主人更大）；
      - 主人说的其它话：0.5；普通群消息：0.05；
  * 状态系数读她现在的状态（睡觉 = 0，完全不回）；
  * 忙碌：主人最近几分钟在桌面里和她聊过，她的注意力在那边（单向：桌面影响 QQ，QQ 不影响桌面）；
  * 心情低落话更少；
  * 冷却：她刚在群里说过话，短时间内再开口的概率先低后慢慢恢复。
再加硬性上限：每个群每 10 分钟最多 X 次、每小时最多 Y 次。**关键词触发的回复都计入这些频率**；主人 @ 她的回复不计入。
这些全是纯函数（不碰数据库、不调模型），方便测试。
"""

from __future__ import annotations

import re
from typing import Any

CLASS_LABEL = {"forced": "主人 @ 她", "direct": "@她 / 回复她", "owner": "主人发的", "keyword": "提到关键词", "normal": "普通消息"}
RANK = {"forced": 0, "direct": 1, "owner": 2, "keyword": 3, "normal": 4}
_SPLIT = re.compile(r"[,，、;；\s]+")


def split_words(s: str) -> list[str]:
    return [w for w in _SPLIT.split(s or "") if w]


def match_keywords(text: str, cfg: dict[str, Any], is_owner: bool) -> dict[str, Any] | None:
    """命中了哪些关键词行；返回概率最大的那一行（{rule, word, p}）。"""
    t = (text or "").lower()
    best = None
    for rule in cfg.get("keyword_rules") or []:
        for w in split_words(rule.get("words", "")):
            if w.lower() in t:
                p = float(rule["p_owner"] if is_owner else rule["p_other"])
                if best is None or p > best["p"]:
                    best = {"rule": rule, "word": w, "p": p}
                break
    return best


def classify(msg: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """这条消息属于哪一档、基础概率是多少。返回 {cls, base, label, trigger}（trigger: name / topic / None）。"""
    owner = bool(msg.get("is_owner"))
    if owner and msg.get("at_self"):
        return {"cls": "forced", "base": 1.0, "label": CLASS_LABEL["forced"], "trigger": None}
    if msg.get("at_self") or msg.get("reply_to_self"):
        return {"cls": "direct", "base": float(cfg["p_direct"]), "label": CLASS_LABEL["direct"], "trigger": None}
    kw = match_keywords(msg.get("text") or "", cfg, owner)
    if kw is not None:
        base = max(kw["p"], float(cfg["p_owner"])) if owner else kw["p"]
        return {"cls": "keyword", "base": base, "label": f"提到「{kw['word']}」（{kw['rule'].get('label') or '关键词'}）", "trigger": "name" if kw["rule"].get("is_name") else "topic"}
    if owner:
        return {"cls": "owner", "base": float(cfg["p_owner"]), "label": CLASS_LABEL["owner"], "trigger": None}
    return {"cls": "normal", "base": float(cfg["p_normal"]), "label": CLASS_LABEL["normal"], "trigger": None}


def state_factor(cfg: dict[str, Any], state_key: str) -> tuple[float, bool]:
    row = (cfg.get("state_factors") or {}).get(state_key) or {}
    return float(row.get("factor", 0.5)), bool(row.get("short"))


def cooldown_factor(cfg: dict[str, Any], since_last_speak_s: float | None, direct: bool) -> float:
    cd = float(cfg["cooldown_minutes"]) * 60
    if since_last_speak_s is None or cd <= 0 or since_last_speak_s >= cd:
        return 1.0
    if direct and cfg.get("cooldown_exempt_direct"):
        return 1.0
    floor = float(cfg["cooldown_floor"])
    return floor + (1.0 - floor) * max(0.0, since_last_speak_s) / cd


def factors(cfg: dict[str, Any], state_key: str, busy: bool, mood: str, since_last_speak_s: float | None, direct: bool) -> dict[str, Any]:
    sf, short = state_factor(cfg, state_key)
    return {
        "state": sf, "short": short,
        "busy": float(cfg["busy_factor"]) if busy else 1.0,
        "mood": float(cfg["mood_factor_low"]) if mood and mood in (cfg.get("low_moods") or []) else 1.0,
        "cooldown": cooldown_factor(cfg, since_last_speak_s, direct),
    }


NEUTRAL = {"state": 1.0, "busy": 1.0, "mood": 1.0, "cooldown": 1.0}


def probability(base: float, f: dict[str, Any]) -> float:
    return max(0.0, min(1.0, base * f["state"] * f["busy"] * f["mood"] * f["cooldown"]))


def caps_ok(cfg: dict[str, Any], speaks_10min: int, speaks_hour: int) -> tuple[bool, str]:
    if speaks_10min >= int(cfg["cap_10min"]):
        return False, f"10 分钟内已经说了 {speaks_10min} 次（上限 {cfg['cap_10min']}）"
    if speaks_hour >= int(cfg["cap_hour"]):
        return False, f"这一小时已经说了 {speaks_hour} 次（上限 {cfg['cap_hour']}）"
    return True, ""


def pick_targets(candidates: list[dict[str, Any]], limit: int, rng: Any) -> list[dict[str, Any]]:
    """过了闸门的消息里挑她最想接的：主人 @ 她的 > 别人 @ 她 / 回复她的 > 主人的 > 关键词 > 普通，同一档里偏向新的。
    最多 limit 条，同一个人连着几条只回最后一条，按发出的先后排好。"""
    ranked = sorted(candidates, key=lambda m: (RANK.get(m["cls"], 9), -m["id"] + rng.random() * 0.01))
    chosen: list[dict[str, Any]] = []
    for m in ranked:
        if len(chosen) >= limit:
            break
        if any(c["user_id"] == m["user_id"] for c in chosen):
            continue
        chosen.append(m)
    return sorted(chosen, key=lambda m: m["id"])
