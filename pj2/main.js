// AI 聊天 · Windows 桌面外壳（Electron）
// 职责：启动 / 接上本地 Python 后端（sidecar），等它就绪后用一个窗口打开；关窗口 = 退出整个应用。
// 数据放在哪：见 resolveDataDir()（环境变量 / config.json 指定；开发时默认是仓库里的 pj1/data，打包后默认在用户数据目录）。

const { app, BrowserWindow, dialog, shell, session } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

const APP_ID = "local.phal.desktop";
const START_TIMEOUT_MS = 60_000;

app.disableHardwareAcceleration(); // 这个界面用不着 GPU 加速；关掉能避开显卡驱动 / 全屏游戏占着显卡时 GPU 进程崩溃导致整个应用闪退
app.setAppUserModelId(APP_ID); // Windows 通知要有这个，主动消息才弹得出来

let win = null;
let child = null; // 我们自己起的后端；接上别人已经在跑的后端时为 null
let serverUrl = null;
let quitting = false;

// ───────────────────────── 路径 / 配置 ─────────────────────────
function userFile(name) {
  return path.join(app.getPath("userData"), name);
}

function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(userFile("config.json"), "utf8"));
  } catch (_) {
    return {};
  }
}

/** 数据目录：环境变量 > config.json 里的 dataDir > 开发时是仓库里的 pj1/data，打包后是用户数据目录下的 data。第一次定下来后记进 config.json。 */
function resolveDataDir() {
  const cfg = readConfig();
  const dflt = app.isPackaged ? path.join(app.getPath("userData"), "data") : path.join(__dirname, "..", "pj1", "data");
  let dir = process.env.PHAL_DATA_DIR || cfg.dataDir || dflt;
  fs.mkdirSync(dir, { recursive: true });
  if (!cfg.dataDir && !process.env.PHAL_DATA_DIR) { // 只有没手动指定时才记下来（环境变量只是临时覆盖）
    try {
      fs.mkdirSync(app.getPath("userData"), { recursive: true });
      fs.writeFileSync(userFile("config.json"), JSON.stringify({ ...cfg, dataDir: dir }, null, 2));
    } catch (_) {
      /* 写不进去也不影响启动 */
    }
  }
  return dir;
}

/** 素材目录：环境变量 > config.json 里的 assetsDir > 打包进来的 resources/assets（开发时是仓库里的 assets）> 用户数据目录下的 assets（里面没有 manifest.json 时，房间里就没有角色）。 */
function resolveAssetsDir() {
  const cfg = readConfig();
  const bundled = app.isPackaged ? path.join(process.resourcesPath, "assets") : path.join(__dirname, "..", "assets");
  return process.env.PHAL_ASSETS_DIR || cfg.assetsDir || (fs.existsSync(bundled) ? bundled : path.join(app.getPath("userData"), "assets"));
}

function sidecarExe() {
  const base = app.isPackaged ? path.join(process.resourcesPath, "sidecar") : path.join(__dirname, "build", "sidecar");
  return path.join(base, "phal-server", "phal-server.exe");
}

// ───────────────────────── 后端 ─────────────────────────
function health(url, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(`${url}/api/health`, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => resolve(res.statusCode === 200 && /"ok"\s*:\s*true/.test(body)));
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

/** 旧版（或另一个实例）已经在这份数据上跑着后端？有就直接接上，不再起第二个（两个进程同时写一个库会打架）。 */
async function findRunningServer(dataDir) {
  try {
    const st = JSON.parse(fs.readFileSync(path.join(dataDir, "server.json"), "utf8"));
    if (st.url && (await health(st.url))) return st.url;
  } catch (_) {
    /* 没有 / 过期了 */
  }
  return null;
}

async function startBackend(dataDir) {
  const running = await findRunningServer(dataDir);
  if (running) return running;

  const exe = sidecarExe();
  if (!fs.existsSync(exe)) throw new Error(`找不到后端程序：${exe}\n请先运行 npm run build:sidecar`);
  const port = await freePort();
  fs.mkdirSync(userFile("logs"), { recursive: true });
  const log = fs.openSync(userFile(path.join("logs", "server.log")), "a");
  child = spawn(exe, ["--port", String(port), "--no-port-scan"], {
    cwd: path.dirname(exe),
    env: { ...process.env, CHAT_DATA_DIR: dataDir, CHAT_ASSETS_DIR: resolveAssetsDir(), PYTHONUTF8: "1" },
    windowsHide: true,
    stdio: ["ignore", log, log],
  });
  child.on("exit", (code) => {
    child = null;
    if (!quitting) {
      // 页面上的「关闭」按钮会让后端退出：那就是用户要关掉整个应用
      quitting = true;
      app.quit();
    }
    if (code) console.error("后端退出，代码", code);
  });

  const url = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!child) throw new Error("后端启动后马上退出了。日志：" + userFile("logs\\server.log"));
    if (await health(url)) return url;
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error("后端 60 秒内没有就绪。日志：" + userFile("logs\\server.log"));
}

function stopBackend() {
  return new Promise((resolve) => {
    if (!child || !serverUrl) return resolve();
    const c = child;
    const timer = setTimeout(() => { try { c.kill(); } catch (_) { /* 已经退了 */ } resolve(); }, 3500);
    c.once("exit", () => { clearTimeout(timer); resolve(); });
    const req = http.request(`${serverUrl}/api/shutdown`, { method: "POST", timeout: 1500 });
    req.on("error", () => { try { c.kill(); } catch (_) { /* noop */ } });
    req.end();
  });
}

// ───────────────────────── 窗口 ─────────────────────────
const SPLASH = "data:text/html;charset=utf-8," + encodeURIComponent(
  `<body style="margin:0;height:100vh;display:grid;place-items:center;background:#f9f8f6;color:#8a847c;font:14px 'Microsoft YaHei',sans-serif">正在启动……</body>`,
);

function createWindow() {
  win = new BrowserWindow({
    width: 1320,
    height: 880,
    minWidth: 520,
    minHeight: 560,
    backgroundColor: "#f9f8f6",
    icon: path.join(__dirname, "assets", "whale-girl.ico"),
    title: "AI 聊天",
    autoHideMenuBar: true,
    webPreferences: {
      backgroundThrottling: false, // 她的状态每 30 秒由页面推进一次，窗口在后台也别被降频
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadURL(SPLASH);

  win.webContents.setWindowOpenHandler(({ url }) => {
    if (serverUrl && url.startsWith(serverUrl)) return { action: "allow" };
    if (/^https?:/i.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (serverUrl && !url.startsWith(serverUrl) && !url.startsWith("data:")) {
      e.preventDefault();
      if (/^https?:/i.test(url)) shell.openExternal(url);
    }
  });
  // F5 / Ctrl+R 刷新，F12 开发者工具
  win.webContents.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    if (input.key === "F5" || (input.control && input.key.toLowerCase() === "r")) { win.webContents.reloadIgnoringCache(); e.preventDefault(); }
    else if (input.key === "F12") { win.webContents.toggleDevTools(); e.preventDefault(); }
  });
  // 房间里的角色动画：窗口最小化 / 隐藏时暂停，回来继续。关掉后台降频后页面自己的 visibilitychange 不一定会触发，所以这里直接告诉页面
  const tellVisible = (visible) => {
    if (win && !win.isDestroyed()) win.webContents.executeJavaScript(`document.dispatchEvent(new CustomEvent("phal:window-visible",{detail:{visible:${visible}}}))`).catch(() => {});
  };
  win.on("minimize", () => tellVisible(false));
  win.on("hide", () => tellVisible(false));
  win.on("restore", () => tellVisible(true));
  win.on("show", () => tellVisible(true));
  win.webContents.on("did-finish-load", () => tellVisible(!win.isMinimized() && win.isVisible()));
  win.on("closed", () => { win = null; });
}

async function boot() {
  // 主动消息的系统通知：允许
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === "notifications" || permission === "fullscreen"));
  createWindow();
  try {
    const dataDir = resolveDataDir();
    serverUrl = await startBackend(dataDir);
    if (win) await win.loadURL(`${serverUrl}/?r=${Date.now()}`);
  } catch (err) {
    dialog.showErrorBox("AI 聊天 启动失败", String(err.message || err));
    quitting = true;
    app.quit();
  }
}

// ───────────────────────── 生命周期 ─────────────────────────
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
  });
  app.whenReady().then(boot);
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (e) => {
    if (quitting && !child) return;
    if (child) {
      e.preventDefault();
      quitting = true;
      stopBackend().finally(() => app.exit(0));
    }
  });
}
