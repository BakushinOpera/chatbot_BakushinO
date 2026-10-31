# 学习笔记

> 每完成一个功能写一条（200 字以内），修 bug 时涉及概念的写一条简短的。格式见 `CLAUDE.md` 第 6 节。最新的在最下面。

## 1. 意图驱动的行为（Intention / BDI）· 2026-10-31

* **一句话**：把「她说要做什么」存成一条带强度、时间范围的记录，之后做随机决定时先查它，按概率照做或打破，而不是说的和做的各走各的。
* **在这个项目里**：`pj1/app/intents.py`。这次把「读会儿书 / 休息一下」也变成意图（kind `read`）：回复后那次提取里抽出来，马上按「随口 60% / 答应 85% / 坚定 95%」判定，照做就调 `life.start_reading_now()`。读书从此和吃饭、做事走同一套规则：没做到会记原因、会主动提。
* **常见的坑**：同一件事被掷两次骰子（每个意图只判定一次，结果存在 `decision` 列）；她嘴上已经拒绝了，提取又抽出「答应」（用 `refused_read_this_turn` 兜住）。
* **搜什么**：BDI agent architecture；commitment tracking dialogue agent

## 2. 上下文变量（ContextVar）做请求级追踪 · 2026-10-31

* **一句话**：一个「跟着当前这条执行流走」的全局变量：同一时间多个请求 / 任务各看到各自的值，新起的异步任务会继承创建它时的值。
* **在这个项目里**：`pj1/app/usage.py` 的运行开销统计。聊天一轮开始时 `start_round()` 设一个轮次 id，回复后起的记忆提取任务自动继承它，于是「回复 + 提取」算成一轮，调用方一行都不用改。
* **常见的坑**：在任务**起完之后**才设值，子任务看不到；普通全局变量在并发时会串（两轮对话互相覆盖）。
* **搜什么**：python contextvars asyncio；request-scoped context propagation

## 3. 上下文预算和按优先级裁剪（Context window budgeting）· 2026-10-31

* **一句话**：给送进模型的内容定一个总上限，超了就先扔「可有可无」的，保住「不能少」的。
* **在这个项目里**：`pj1/app/context.py` 的 `fit_budget`。人设和总纲不动；状态、打算、聊天记录其次；世界条目 → 小本子 → 记忆最先裁。检索来的东西本来就是「可能相关」，最适合先让位。
* **常见的坑**：只按条数限、不按字数（一条超长记忆就撑爆）；裁聊天记录时把开头裁成一句孤零零的 AI 回复，模型会以为是它先开口。
* **搜什么**：LLM context window management；prompt budget truncation strategy

## 4. 约束放在代码里，而不是提示词里（Deterministic post-processing）· 2026-10-31

* **一句话**：模型不一定听话，能用几行代码保证的格式规则（去感叹号、限引用次数）就交给代码，提示词只管语气和内容。
* **在这个项目里**：T5 把十几处提示词里的「不用感叹号」删了，改成在出口统一 `guards.strip_exclaim`（主动消息、批注、日总结、写作正文……）；写作也不再为一个感叹号多调一次模型重写。
* **常见的坑**：有些规则代码只能「事后兜底」（截断、夹回价格），提示词里还是要写，否则会多重试或截坏句子。
* **搜什么**：LLM output post-processing guardrails；constrained generation vs validation

## 5. 加权随机的可解释性（Weighted random + decision logging）· 2026-10-31

* **一句话**：加权随机 = 每个选项一个权重，被抽中的概率 = 它的权重 ÷ 总权重；把这一步的输入（各权重、乘过哪些系数）和结果记下来，事后就能回答「她为什么去打游戏」。
* **在这个项目里**：`pj1/app/whylog.py`。状态、吃饭、事件、买东西四处在 `random.choices` 之后各记一行：抽中的那个占多少、主要系数（情绪、精力、下雨、余额……）、排前几的其它候选。
* **常见的坑**：只记结果不记当时的权重（权重随心情变，事后算不回来）；每次「没触发」也记，日志会被刷屏（买东西只记掷中的）。
* **搜什么**：weighted random selection explainability；game AI decision logging / debug visualization

## 6. 数据驱动 + 配置覆盖（Data-driven design / layered config）· 2026-10-31

* **一句话**：把「是谁、在哪、用什么钱」这类内容从代码里搬到配置；代码里留一份默认值，用户的配置文件只写想改的项，读的时候「默认 + 覆盖」深合并。
* **在这个项目里**：`pj1/app/character.py`。Phal 的东西是默认值，`data/characters/<id>/character.json` 覆盖；`states.json` / `events.json` 是挂载点，加状态、事件类型不用改代码。测试里换成假角色「金」（金币、炼金术、王都）整套跑一遍。
* **常见的坑**：第一次运行就把整份默认值写进用户文件——以后改默认值，用户那份不会跟着变（这次一开始就踩了，改成只写参考副本）；配置在导入时读，改了要重启。
* **搜什么**：data-driven game design；layered configuration deep merge

## 7. 配置优先级与首次初始化（Config precedence / seeding）· 2026-10-31

* **一句话**：同一个设置可能来自好几层（环境变量 > 用户配置文件 > 数据库里存的 > 角色默认 > 代码默认），要先定清楚谁压谁；「第一次建库时写进去的初始值」也是一层，最容易被忽略。
* **在这个项目里**：T8 把调好的数值放进角色配置当默认值，结果发现建库时 `config.DEFAULT_SETTINGS` 已经把老默认值写进了 settings 表，角色默认被压住了；改成建库时用 `db.seed_settings()`（角色配置优先）。桌面外壳的数据 / 素材目录同理：环境变量 > config.json > 打包进来的 > 用户目录。
* **常见的坑**：测试也吃到了新默认值（测试应该测代码逻辑，用 `CHAT_CODE_DEFAULTS=1` 隔开）。
* **搜什么**：configuration precedence order；database seeding default settings
