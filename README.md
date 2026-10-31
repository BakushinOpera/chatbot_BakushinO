# Phal

一个跑在自己电脑上的 AI 陪伴聊天应用：她有自己的生活（作息、心情、吃饭、零花钱、读书写作、房间），会记得你们聊过的事，会把聊天里说过的打算真的去做。
可以随意修改。

## 组成

| 目录 | 是什么 |
|---|---|
| `pj1` | 后端（Python FastAPI + SQLite）+ 网页（原生 HTML/CSS/JS） |
| `pj2` | 桌面外壳（Electron），把后端打包进来 |
| `assets` | 房间素材：角色图、家具、道具，`manifest.json` 是清单 |
| `docs/design.md` | 各个系统现在是怎么设计的、参数是什么意思 |

## 用免安装版（推荐）

1. 从这个仓库的 Releases 下载 `AI聊天-portable-<日期>.zip`。
2. 解压到任意文件夹（路径里最好没有特殊符号），双击 `AI 聊天.exe`。不用装 Python。
3. 第一次打开没配 key 是离线演示模式，能发消息但她不会真的回复。配 key 见下面。

数据默认存在 `%APPDATA%\AI 聊天\data`，和程序分开，换个文件夹解压新版本不会丢。房间素材已经打包在里面。
Windows 可能提示「未知发布者」（没有代码签名），选「仍要运行」。

## 从源码运行

需要 Python 3（开发时用的是 3.14），打包桌面版时还要 Node。

```bash
cd pj1
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python run.py
```

`run.py` 会自己找空闲端口，打开它打印的地址就是网页版。**别和桌面版同时开**（两个进程会写同一份数据）。

打包桌面版（在 `pj2`）：

```bash
npm install
npm run build:sidecar
npx electron-builder --win nsis
```

产物在 `pj2/dist/win-unpacked/`。

## 配置模型（DeepSeek key）

1. 到 DeepSeek 开放平台申请一个 API key。
2. 打开应用 → 右上角齿轮「设置」→「模型与记忆」：API Base 填 `https://api.deepseek.com/v1`，填 key 和模型名，自动保存。
3. 也可以复制 `pj1/.env.example` 为 `pj1/.env` 填 `CHAT_API_KEY`；界面里保存的值优先。

任何 OpenAI 兼容的接口都能用（OpenAI、Moonshot、本地 Ollama 等）。

## 哪些东西只在你自己电脑上

都在数据目录里，不会进仓库：聊天记录和记忆（`chat.db`）、世界设定（`world/`）、小本子（`characters/`）、导入的书、她写的东西、信、QQ 配置和记录、你的 `.env`。

* 源码运行时数据目录是 `pj1/data/`；桌面版默认在 `%APPDATA%\AI 聊天\data`（环境变量 `PHAL_DATA_DIR` 可改）。
* 备份就是拷走整个数据目录。

## 换一个角色

默认角色是 Phal。角色相关的东西（名字、钱、住的地方、你在哪、人设、调好的设置值）都在角色配置里，代码不用改：

* 角色配置：`<数据目录>\characters\<角色id>\character.json`，只写想改的项；完整的项和 Phal 的默认值见 `pj1/app/character.py`。
* 人设：同一个目录里的 `persona.md`（第一次启动时当默认人设）。
* 加状态、加事件类型：同一个目录里的 `states.json`、`events.json`。
* 可选内容：`fictional_world`（虚构世界：不提现实地名）默认都关着；`user_pronoun` 是提示词里对你的称呼（默认「他」）。
* 用哪个角色：环境变量 `CHAT_CHARACTER`（默认 `phal`）。

## QQ 接入（可选，没实测过）

主界面「QQ」按钮里配置，通过 NapCat（OneBot v11）连接。**有封号风险，务必用小号。** 只处理你指定的主人私聊和白名单群，QQ 里的内容不会进她的本地记忆。

## 快捷键

| 在哪 | 按键 | 作用 |
|---|---|---|
| 聊天 | Enter | 发送（她回复时也能接着发） |
| 阅读器 | ← / → | 翻页 |
| 阅读器 | Ctrl+F | 搜索（默认全书） |
| 文档编辑器 | Ctrl+S | 保存 |
| 房间 | Esc | 从书桌视角回到房间视角 / 关掉小本子 |
| 房间 | Ctrl+Shift+P | 姿势调试器（调她在每个状态里的位置、大小） |
| 控制台 | `PhalRoom.setAmbient({ hour: 22, kind: "rain" })` | 预览房间夜里 / 雨天的样子，`null` 恢复 |

## 遇到问题

发现 bug 或者觉得哪里可以更好，直接告诉作者就行（截图 + 大概在做什么时候出的问题）。后端日志在 `%APPDATA%\AI 聊天\logs\server.log`。
