"""QQ 接入：通过 OneBot v11 正向 WebSocket 连本机的 NapCat（不用 AstrBot）。

边界（最重要）：
* 对本地应用的数据**只读**（localview.py 用 ``mode=ro`` 的只读连接，数据库层面就写不进去）；
* QQ 自己的数据全放 ``data/qq/qq.db``（store.py）：群聊 / 私聊记录、群友印象、频率计数、配置；
* 只处理「主人私聊」和「白名单群」，其它一律在落库之前丢掉；
* 桌面端的聊天页不显示任何 QQ 数据；QQ 页（web/qq.js）里只有连接状态、设置、群友印象和模拟群聊。

模块分工：
  store.py     QQ 自己的数据库
  settings.py  配置的定义、校验、读写（存在 qq.db 里，不进统一设置页）
  localview.py 只读地看本地应用的数据（人设 / 状态 / 心情 / 事件 / 记忆 / 天气 / 人物……）
  text.py      OneBot 消息段解析、去 Markdown、静默口令、名字清洗
  gate.py      群聊的回复概率（基础概率 × 状态 × 忙碌 × 心情 × 冷却）和硬性上限
  ctx.py       给模型的上下文（私聊 / 群聊 / 主动开口），信任分级在这里
  onebot.py    正向 WebSocket 客户端：连接、断线重连、调用动作、收事件
  engine.py    把上面串起来：收消息 → 批量窗口 → 闸门 → 生成 → 后处理 → 逐条发出
  api.py       /api/qq/*：状态、设置、群友印象、模拟群聊
"""
