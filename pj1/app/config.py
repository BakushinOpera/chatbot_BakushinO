"""全局配置：路径、默认设置、默认人设。

设计原则
--------
* 「人设」和「配置」全部存在 SQLite 的 settings 表里，本文件只提供**首次运行时的默认值**。
  运行期没有任何硬编码的判断逻辑去决定"该不该提时间""该不该回忆过去"，
  那些规则写在人设（system prompt）里，由模型自己决定。
* 修改人设 = 更新 settings 表里的一行，永远不会碰聊天记录表。
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
DATA_DIR = Path(os.getenv("CHAT_DATA_DIR") or (BASE_DIR / "data"))
DB_PATH = Path(os.getenv("CHAT_DB_PATH") or (DATA_DIR / "chat.db"))
# 素材目录（角色图、家具贴图……按类别分文件夹，有一份 manifest.json 记着每个素材的用途和锚点）：放在项目外面，方便你直接替换文件。
# 开发时默认是项目旁边的 assets/；桌面应用由 main.js 通过环境变量 CHAT_ASSETS_DIR 指过来。
ASSETS_DIR = Path(os.getenv("CHAT_ASSETS_DIR") or (BASE_DIR.parent / "assets"))
# 当前角色：按角色存的新数据（小本子……）放在 DATA_DIR/characters/<角色>/。现在只有 phal；旧数据（记忆、账本……）还在原处
CHARACTER_ID = os.getenv("CHAT_CHARACTER") or "phal"

APP_NAME = "AI 聊天"

# --------------------------------------------------------------------------
# 默认人设（system prompt）—— 用户可随时在界面右上角「设置」里改掉
# --------------------------------------------------------------------------
def _load_default_persona() -> str:
    """默认人设：角色目录里有 persona.md 就用它（换角色包时）；否则用 app/default_persona.md（Phal 的人设）。界面里改过的优先。"""
    own = DATA_DIR / "characters" / CHARACTER_ID / "persona.md"
    path = own if own.exists() else BASE_DIR / "app" / "default_persona.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "你是一个安静的、住在本地设备里的 AI 伙伴。说话简短自然。"


DEFAULT_PERSONA = _load_default_persona()


# --------------------------------------------------------------------------
# 默认设置：一切可在界面里改，改完写回 settings 表
# --------------------------------------------------------------------------
DEFAULT_SETTINGS: dict[str, str] = {
    # —— 人设 ——
    "persona_name": "小",
    "system_prompt": DEFAULT_PERSONA,
    # —— 上下文 ——
    "recent_limit": "24",      # 短期上下文条数（建议 20~30）
    "memory_limit": "12",      # 注入的长期记忆条数
    "context_char_budget": "12000",  # 短期上下文的字数预算（超了从最旧的开始丢）
    "enable_tools": "1",       # 允许模型检索历史记录 / 写长期记忆
    "split_replies": "1",      # 把长回复拆成多条短消息（像聊微信一样）
    "enable_auto_memory": "1", # 每轮对话后自动提取长期记忆（三类具体条目）
    "auto_memory_every": "20",
    "memory_extract_every": "1",   # 每几轮对话提取一次（1 = 每轮；调大省模型调用）
    # —— 用词列表（存成 JSON 数组字符串，界面上随时加减）——
    "banned_words": "[]",      # 禁词：不许说的词 / 说法
    "common_words": "[]",      # 常用词：希望它多用的词 / 口头禅
    # —— 模型接口（OpenAI 兼容）——
    "api_base": os.getenv("CHAT_API_BASE", "https://api.deepseek.com/v1"),
    "api_key": os.getenv("CHAT_API_KEY", ""),
    "model": os.getenv("CHAT_MODEL", "deepseek-chat"),
    "temperature": "0.9",
    "max_tokens": "2048",
    # —— 说话风格学习 ——
    "enable_style_learning": "1",   # 从过往全部聊天里学说话风格
    "style_notes": "",              # 学到的风格笔记（界面里可直接改）
    "style_marker": "0",
    # —— Phal 的生活（状态栏 / 看书 / 创作 / 主动消息）——
    "life_enabled": "1",
    "life_state": "",
    "life_wish_day": "",
    "writing_ask_day": "",      # 她哪天问过我要新文档（一天最多问一次）
    "writing_waiting": "0",     # 1 = 她在等我给她建新文档
    # —— 内部状态 ——
    "current_session_id": "",
    "auto_memory_marker": "0",
}

# API Key 在接口里永远只回传掩码，不回传明文
API_KEY_MASK = "••••••••"

PERSONA_NOTE = (
    "修改人设只会更新 settings 表里的这一条记录，"
    "不会清空、重建或影响任何聊天记录。"
)


def parse_words(raw: str | list | None) -> list[str]:
    """把 settings 里存的 JSON 数组字符串解析成干净的词表（去空、去重、保持顺序）。"""
    import json

    if isinstance(raw, list):
        items = raw
    else:
        try:
            data = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError):
            return []
        items = data if isinstance(data, list) else []

    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        word = str(item).strip()
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out


def dump_words(words: list[str] | None) -> str:
    """词表 -> 存库用的 JSON 字符串。"""
    import json

    return json.dumps(parse_words(words), ensure_ascii=False)
