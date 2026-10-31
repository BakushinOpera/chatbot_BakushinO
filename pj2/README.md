# AI 聊天 · Windows 桌面应用（pj2）

Electron 外壳 + `pj1` 的 Python 后端（打成独立的 `phal-server.exe`，不需要装 Python）。

## 数据放哪
- 按这个顺序找：环境变量 `PHAL_DATA_DIR` > `%APPDATA%\AI 聊天\config.json` 里的 `dataDir` > 开发时（`npm start`）是仓库里的 `pj1\data`，打包后是 `%APPDATA%\AI 聊天\data`。第一次定下来后记进 `config.json`，想换位置改这个文件。
- 不要同时开两份：桌面应用启动时如果发现这份数据上已经有后端在跑，会直接接上它，不会再起第二个；这种情况下关窗口不会关掉那个后端。

## 房间素材放哪
- 按这个顺序找：环境变量 `PHAL_ASSETS_DIR` > `config.json` 里的 `assetsDir` > 打包进来的 `resources\assets`（开发时是仓库里的 `assets`）> `%APPDATA%\AI 聊天\assets`。桌面应用通过环境变量 `CHAT_ASSETS_DIR` 把这个目录交给后端。
- 打包时会把仓库的 `assets` 一起带进去（不带 `originals` 原图），所以拿到免安装版就有角色和房间。
- 想用一份能随时改的素材：在 `config.json` 里写 `assetsDir`，改完刷新房间页面（F5）就能看到，不用重新打包。
- 目录里没有 `manifest.json` 时，房间页面照常打开，只是房间里没有角色。

## 怎么用
- 开发运行：`npm start`（需要先 `npm run build:sidecar` 生成后端）。
- 重新打后端：`npm run build:sidecar`（改了 pj1 的 Python / 网页代码之后都要重打）。
- 打包：`npm run build:sidecar && npx electron-builder --win nsis` → `dist\AI 聊天 Setup 1.0.0.exe`（安装版）和 `dist\win-unpacked\`（打包输出，别直接用）。
- 免安装版：打完包跑 `npm run deploy:portable` → 把 `dist\win-unpacked\` 复制到仓库根下的 `Phal\`（`-Target` 可改），试启动一次（临时数据目录 + 离线，确认后端报健康），再把桌面快捷方式「AI 聊天」指过去。正在开着的免安装版会先被关掉；包里没有后端（`build:sidecar` 失败）时不部署。
- 关窗口 = 退出整个应用（同时关掉自己起的后端）；页面左下角的「关闭」按钮同样会退出。
- 后端日志：`%APPDATA%\AI 聊天\logs\server.log`。

## 说明
- 打包配置里 `asar: false`：开着 asar 时，打出来的 exe 会因为完整性校验（STATUS_BREAKPOINT）直接闪退，关掉后稳定。
- 窗口关闭背景节流，所以最小化后她的状态仍然每 30 秒推进一次。
- 主动消息的系统通知走 Windows 通知（应用 ID `local.phal.desktop`）。
- 没有代码签名：安装时 SmartScreen 可能提示「未知发布者」，选「仍要运行」。

## 打包后务必试启动一次
刚打包出来的 exe 偶尔一启动就退出（见过退出码 -2147483645 和 3），字节完全一样的副本却能正常运行。所以免安装版是 `npm run deploy:portable` 复制出去的那份：脚本复制完会试启动，起不来就把 exe 换成新复制的一份再试（最多 3 次），都不行才报错。
