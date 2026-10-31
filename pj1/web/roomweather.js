/* 书桌视角的窗外动画（v7 定稿）。
 *
 * 窗外（天空 / 远云 / 树和坡 / 雨雪 / 玻璃上的雨滴）画在主画面 SVG 下面的两块 canvas 上，主画面在窗洞那里留空；
 * 闪电时最上面一层 div 跟着亮一下（屋里也闪）。配色和参数（出厂值）见 web/room3d-weather.js。
 * 长度 / 速度参数的单位是「窗洞高 360px 时的像素」，按实际窗洞大小缩放（k）。窗口隐藏、离开书桌视角时停掉动画。
 */
(() => {
  "use strict";
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rand = (a, b) => a + Math.random() * (b - a);
  const hexA = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${clamp(a, 0, 1).toFixed(3)})`; };
  const mixHex = (a, b, t) => { const x = parseInt(a.slice(1), 16), y = parseInt(b.slice(1), 16), m = (s) => Math.round(((x >> s) & 255) + (((y >> s) & 255) - ((x >> s) & 255)) * t); return `#${((m(16) << 16) | (m(8) << 8) | m(0)).toString(16).padStart(6, "0")}`; };
  const LS = {
    get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* 无痕模式 */ } },
  };
  const SKY_Y = 898;

  /* ───────────────── 动画 ─────────────────
   * 只有两块 canvas，都只铺在窗洞的外接矩形上（不用 CSS filter，GPU 不用每帧做模糊）：
   *   · 远景层（低分辨率，默认前景的一半）：天空底图（渐变 + 地平线的光 + 太阳 / 月亮，模糊烘焙进去，钟点 / 天气变了才重画）
   *     + 星星 + 远云（每朵是烘焙好模糊的小图，只平移）+ 闪电 + 树和坡（SVG 转成图、模糊烘焙进去）+ 雾气 + 远处的雨雪。
   *     分辨率低本身就让远处的雨雪发虚，相当于景深。
   *   · 近景层：近处的雨雪、溅水、窗台积雪、闪电时一亮；玻璃上的雨滴先画在一块低分辨率的小画布上（每秒刷新 glass.fps 次），
   *     每帧贴一次。
   * 渲染精度（设置 → 她的房间 → 画面）：高 = 屏幕像素比（最多 1.5）、中 = 1、低 = 0.75。 */
  const QUALITY = { high: () => Math.min(1.5, window.devicePixelRatio || 1), mid: () => Math.min(1, window.devicePixelRatio || 1), low: () => 0.75 };
  // 每档的动画帧率上限：实测 GPU 的开销主要跟「每秒合成几次画面」走，分辨率影响不大
  const QUALITY_FPS = { high: 30, mid: 24, low: 15 };
  const FX = {
    box: null, r: null, info: null, params: null, els: null, raf: 0, last: 0, running: false, want: false, quality: "high",
    geo: null, clouds: [], stars: [], rain: { far: [], near: [] }, snow: { far: [], near: [] }, splash: [], drops: [], runners: [], trails: [],
    flash: null, pile: 0, pileSaved: 0, t: 0,
    bake: { sky: null, skyKey: "", clouds: null, cloudKey: "", outdoor: null, outdoorKey: "", glass: null, glassAt: 0 },
  };
  /** 把主画面的 viewBox 坐标换成容器里的像素（主画面是 preserveAspectRatio="xMidYMid slice"）。 */
  function mapper(box, vb) {
    const W = box.clientWidth, H = box.clientHeight, s = Math.max(W / vb[2], H / vb[3]);
    const ox = (W - vb[2] * s) / 2 - vb[0] * s, oy = (H - vb[3] * s) / 2 - vb[1] * s;
    return { W, H, s, map: (p) => [p[0] * s + ox, p[1] * s + oy] };
  }

  function mount(box, r, info, params) {
    unmount();
    if (!box || !r || !r.layered || !r.hole) return;
    FX.box = box; FX.r = r; FX.info = info; FX.params = params;
    const wrap = document.createElement("div");
    wrap.className = "rm-wx";
    const mk = (cls) => { const c = document.createElement("canvas"); c.className = `rm-wx__layer ${cls}`; wrap.appendChild(c); return c; };
    const back = mk("rm-wx__back"), front = mk("rm-wx__front");
    const flash = document.createElement("div");
    flash.className = "rm-wx-flash";
    box.insertBefore(wrap, box.firstChild);
    box.appendChild(flash);
    FX.els = { wrap, back, front, flash };
    FX.bake = { sky: null, skyKey: "", clouds: null, cloudKey: "", outdoor: null, outdoorKey: "", glass: null, glassAt: 0 };
    FX.pile = loadPile();
    FX.ro = new ResizeObserver(() => { layout(); seed(true); });       // 第一次挂上去时容器可能还没进页面（大小是 0），进了页面这里会再算一遍
    FX.ro.observe(box);
    layout();
    seed(FX.clouds.length > 0);                                  // 重画（换钟点、调参数）时粒子接着飘，不从头来
    if (FX.want) start();
  }
  function unmount() {
    stop();
    if (FX.ro) FX.ro.disconnect();
    if (FX.els) { FX.els.wrap.remove(); FX.els.flash.remove(); }
    savePile(true);
    Object.assign(FX, { box: null, r: null, els: null, geo: null, ro: null });
  }
  /** 渲染精度：high / mid / low（来自设置）。 */
  function setQuality(q) { const v = QUALITY[q] ? q : "high"; if (v === FX.quality) return; FX.quality = v; if (FX.els) { layout(); seed(true); } }

  /** 窗洞在容器里的位置；两块 canvas 都只铺在窗洞外接矩形（外扩一点）。 */
  function layout() {
    if (!FX.box || !FX.r) return;
    const M = mapper(FX.box, FX.r.viewBox), hole = FX.r.hole.map(M.map);
    const xs = hole.map((p) => p[0]), ys = hole.map((p) => p[1]), pad = 12;
    const x0 = Math.max(0, Math.floor(Math.min(...xs) - pad)), y0 = Math.max(0, Math.floor(Math.min(...ys) - pad));
    const x1 = Math.min(M.W, Math.ceil(Math.max(...xs) + pad)), y1 = Math.min(M.H, Math.ceil(Math.max(...ys) + pad));
    const w = Math.max(1, x1 - x0), h = Math.max(1, y1 - y0);
    const front = QUALITY[FX.quality](), back = front * clamp(FX.params ? FX.params.view.backRes : 0.5, 0.2, 1);
    [["back", back], ["front", front]].forEach(([k, sc]) => {
      const c = FX.els[k];
      c.style.left = `${x0}px`; c.style.top = `${y0}px`; c.style.width = `${w}px`; c.style.height = `${h}px`;
      const cw = Math.max(1, Math.round(w * sc)), ch = Math.max(1, Math.round(h * sc));
      if (c.width !== cw || c.height !== ch) { c.width = cw; c.height = ch; }
    });
    const loc = hole.map((p) => [p[0] - x0, p[1] - y0]);              // 窗洞四角（canvas 里的坐标）：0 左下、1 右下、2 右上、3 左上
    const hh = Math.max(Math.hypot(loc[3][0] - loc[0][0], loc[3][1] - loc[0][1]), Math.hypot(loc[2][0] - loc[1][0], loc[2][1] - loc[1][1]));
    const cam = FX.r.cam;
    const sky = (x, z) => { const p = M.map(cam.project([x, SKY_Y, z])); return [p[0] - x0, p[1] - y0]; };
    FX.geo = { x0, y0, w, h, W: M.W, H: M.H, front, back, hole: loc, k: hh / 360, sky, s: M.s, ledgeY: (x) => { const a = loc[0], b = loc[1], t = clamp((x - a[0]) / ((b[0] - a[0]) || 1), 0, 1); return a[1] + (b[1] - a[1]) * t; },
      left: Math.min(loc[0][0], loc[3][0]), right: Math.max(loc[1][0], loc[2][0]), top: Math.min(loc[2][1], loc[3][1]), bottom: Math.max(loc[0][1], loc[1][1]) };
    FX.bake.skyKey = FX.bake.cloudKey = FX.bake.outdoorKey = "";       // 大小变了：烘焙的图都重做
  }

  /* ───────── 粒子 ───────── */
  function seed(keep) {
    const g = FX.geo, p = FX.params, info = FX.info;
    if (!g || !p || !info || g.w < 4 || g.h < 4) return;
    // 远云：位置按「天空板」上的 x / z 随机，大小随机；颜色按配色表
    const want = Math.round(p.farCloud.count * clamp(0.35 + info.clouds, 0.2, 1.6));
    if (!keep || FX.clouds.length !== want) {
      FX.clouds = [];
      for (let i = 0; i < want; i++) FX.clouds.push({ x: rand(-320, 320), z: rand(150, 330), w: rand(60, 130) });
    }
    FX.bake.cloudKey = FX.bake.skyKey = "";                     // 颜色、模糊可能变了：下一帧重新烘焙
    if (!keep || !FX.stars.length) { FX.stars = []; for (let i = 0; i < 60; i++) FX.stars.push({ x: rand(-300, 300), z: rand(110, 420), r: rand(0.6, 1.9), ph: rand(0, 6.28), hz: rand(0.3, 1.2) }); }
    fillRain(); fillSnow();
  }
  const area = () => (FX.geo.w * FX.geo.h) / (FX.geo.k * FX.geo.k * 520 * 360);   // 和参考窗洞（520×360）比的面积
  function fillRain() {
    const p = FX.params.rain, total = Math.round(150 * p.density * FX.info.rain * area());
    const nFar = Math.round(total * p.farShare), nNear = total - nFar;
    const resize = (arr, n, far) => { while (arr.length > n) arr.pop(); while (arr.length < n) arr.push(newDrop(far, true)); };
    resize(FX.rain.far, nFar, true); resize(FX.rain.near, nNear, false);
  }
  function newDrop(far, anywhere) {
    const g = FX.geo, p = FX.params.rain, sc = far ? p.farScale : 1;
    const ang = ((p.angle + rand(-p.angleJitter, p.angleJitter)) * Math.PI) / 180;
    const len = rand(p.lenMin, Math.max(p.lenMin, p.lenMax)) * sc * g.k, sp = rand(p.speedMin, Math.max(p.speedMin, p.speedMax)) * sc * g.k;
    const span = g.right - g.left + g.h * Math.abs(Math.tan(ang)) + 40;
    return { x: g.left - 20 - (Math.tan(ang) > 0 ? g.h * Math.tan(ang) : 0) + Math.random() * span, y: anywhere ? rand(g.top - 40, g.bottom) : g.top - rand(10, 80), len, sp, ang, a: rand(p.alphaMin, Math.max(p.alphaMin, p.alphaMax)) * (far ? 0.7 : 1) };
  }
  function fillSnow() {
    const p = FX.params.snow, total = Math.round(110 * p.density * FX.info.snow * area());
    const nFar = Math.round(total * p.farShare), nNear = total - nFar;
    const resize = (arr, n, far) => { while (arr.length > n) arr.pop(); while (arr.length < n) arr.push(newFlake(far, true)); };
    resize(FX.snow.far, nFar, true); resize(FX.snow.near, nNear, false);
  }
  function newFlake(far, anywhere) {
    const g = FX.geo, p = FX.params.snow, sc = far ? 0.55 : 1;
    const r = rand(p.sizeMin, Math.max(p.sizeMin, p.sizeMax)) * sc * g.k, t = (r / (g.k * sc) - p.sizeMin) / Math.max(0.01, p.sizeMax - p.sizeMin);
    return { x: rand(g.left - 30, g.right + 30), y: anywhere ? rand(g.top - 20, g.bottom) : g.top - rand(5, 40), r, vy: (p.speedMin + (p.speedMax - p.speedMin) * clamp(t * 0.7 + Math.random() * 0.3, 0, 1)) * sc * g.k, ph: rand(0, 6.28), hz: p.swayHz * rand(0.6, 1.4), amp: p.sway * rand(0.5, 1.2) * sc * g.k, a: p.alpha * (far ? 0.7 : rand(0.75, 1)) };
  }

  /* ───────── 窗台积雪（按真实时间累积；存在本机，刷新不丢） ───────── */
  function loadPile() {
    const v = LS.get("phal.room.snowpile");
    if (!v || typeof v.a !== "number") return 0;
    const mins = Math.max(0, (Date.now() - (v.ts || 0)) / 60000), p = FX.params && FX.params.snow;
    const snowing = FX.info && FX.info.snow;
    return clamp(v.a + (snowing ? 1 : -1) * mins * (p ? (snowing ? p.accumPerMin : p.meltPerMin) : 0.02), 0, 1);
  }
  function savePile(force) {
    const now = Date.now();
    if (!force && now - FX.pileSaved < 10000) return;
    FX.pileSaved = now;
    LS.set("phal.room.snowpile", { a: FX.pile, ts: now });
  }

  /* ───────── 主循环 ───────── */
  function start() {
    FX.want = true;
    if (FX.running || !FX.els) return;
    FX.running = true; FX.last = performance.now();
    const loop = (now) => {
      if (!FX.running) return;
      FX.raf = requestAnimationFrame(loop);
      // 没有雨雪、玻璃上没水、没在闪电：只有云在慢慢飘，降到 12 帧省电
      const busy = FX.info && (FX.info.rain || FX.info.snow || FX.drops.length || FX.runners.length || FX.trails.length || FX.splash.length || FX.flash);
      const fps = Math.min(FX.params ? FX.params.view.fps : 30, QUALITY_FPS[FX.quality] || 30, busy ? 999 : 12), dtMs = now - FX.last;
      if (dtMs < 1000 / fps - 2) return;
      FX.last = now;
      step(Math.min(0.1, dtMs / 1000));
      draw(now);
    };
    FX.raf = requestAnimationFrame(loop);
  }
  function stop() { FX.running = false; cancelAnimationFrame(FX.raf); }
  function setActive(on) { FX.want = !!on; if (on) start(); else { stop(); savePile(true); } }

  function step(dt) {
    const g = FX.geo, info = FX.info, P = FX.params;
    if (!g || !info || !P) return;
    FX.t += dt;
    // 远云：沿天空板往右（西）飘，出了画面从另一边回来
    FX.clouds.forEach((c) => { c.x += P.farCloud.speed * dt * 0.3; if (c.x > 380) c.x -= 760; });
    // 雨：往下（带倾斜）落；近处的落到窗台上，有一定概率溅起水花
    const rainStep = (arr, far) => arr.forEach((d, i) => {
      d.y += d.sp * dt * Math.cos(d.ang); d.x += d.sp * dt * Math.sin(d.ang);
      const floorY = far ? g.bottom : g.ledgeY(d.x);
      if (d.y - d.len * 0.3 > floorY) {
        if (!far && Math.random() < 0.35) addSplash(d.x, g.ledgeY(d.x));
        arr[i] = newDrop(far, false);
      }
    });
    // 雪：边落边左右飘
    const snowStep = (arr, far) => arr.forEach((f, i) => { f.y += f.vy * dt; f.ph += dt * f.hz * 6.283; if (f.y - f.r > (far ? g.bottom : g.ledgeY(f.x) + f.r)) arr[i] = newFlake(far, false); });
    rainStep(FX.rain.far, true); rainStep(FX.rain.near, false); snowStep(FX.snow.far, true); snowStep(FX.snow.near, false);
    // 溅水：除了雨滴落下来溅的，再按频率在窗台上随机溅一些
    if (info.rain > 0) {
      const n = P.splash.rate * info.rain * dt * (g.right - g.left) / (520 * g.k);
      for (let i = 0; i < Math.floor(n) + (Math.random() < n % 1 ? 1 : 0); i++) { const x = rand(g.left, g.right); addSplash(x, g.ledgeY(x)); }
    }
    FX.splash = FX.splash.filter((s) => { s.life -= dt; s.x += s.vx * dt; s.y += s.vy * dt; s.vy += 900 * g.k * dt; return s.life > 0; });
    // 窗台积雪：下雪时慢慢变厚，不下就慢慢化掉（0~1，乘 accumMax）
    { FX.pile = clamp(FX.pile + (info.snow ? P.snow.accumPerMin * info.snow : -P.snow.meltPerMin) * dt / 60, 0, 1); savePile(); }
    // 玻璃上的雨滴：雨越大越多；不下雨了慢慢干掉
    const target = Math.round(P.glass.drops * clamp(info.rain, 0, 1.5) * area());
    if (FX.drops.length < target && Math.random() < dt * 8) FX.drops.push(newBead());
    if (FX.drops.length > target && Math.random() < dt * (info.rain ? 2 : 0.6)) FX.drops.splice(Math.floor(Math.random() * FX.drops.length), 1);
    if (info.rain > 0 && Math.random() < P.glass.runRate * dt * info.rain && FX.drops.length) {
      const big = FX.drops.reduce((b, d) => (d.r > b.r ? d : b), FX.drops[Math.floor(Math.random() * FX.drops.length)]);
      FX.drops.splice(FX.drops.indexOf(big), 1);
      FX.runners.push({ x: big.x, y: big.y, r: Math.max(big.r, 2.2 * g.k), vy: P.glass.runSpeed * g.k * rand(0.6, 1.4), wob: rand(0, 6.28), path: [[big.x, big.y]] });
    }
    FX.runners = FX.runners.filter((ru) => {
      ru.y += ru.vy * dt * rand(0.4, 1.6); ru.wob += dt * 3; ru.x += Math.sin(ru.wob) * 4 * g.k * dt;
      ru.path.push([ru.x, ru.y]);
      FX.drops = FX.drops.filter((d) => { if (Math.abs(d.x - ru.x) < ru.r + d.r && Math.abs(d.y - ru.y) < ru.r + d.r) { ru.r = Math.min(ru.r + d.r * 0.15, 4.5 * g.k); return false; } return true; });
      if (ru.y > g.ledgeY(ru.x) - ru.r) { FX.trails.push({ path: ru.path, life: 1, w: ru.r * 0.8 }); return false; }
      return true;
    });
    FX.trails = FX.trails.filter((tr) => { tr.life -= dt / 7; return tr.life > 0; });
    // 闪电
    const rate = info.thunder ? P.lightning.thunderPerMin : info.rain ? P.lightning.rainPerMin : 0;
    if (!FX.flash && rate > 0 && Math.random() < (rate / 60) * dt) strike();
    if (FX.flash) { FX.flash.t += dt; if (FX.flash.t > 0.9) FX.flash = null; }
  }
  function addSplash(x, y) {
    const g = FX.geo, s = FX.params.splash.size * g.k, n = 2 + Math.floor(Math.random() * 3);
    for (let i = 0; i < n; i++) FX.splash.push({ x, y: y - 1, vx: rand(-60, 60) * s, vy: -rand(70, 170) * s, r: rand(0.5, 1.3) * s, life: rand(0.18, 0.38) });
  }
  function newBead() {
    const g = FX.geo, p = FX.params.glass;
    const x = rand(g.left + 4, g.right - 4), y = rand(g.top + 4, g.ledgeY(x) - 4);
    return { x, y, r: rand(0.7, 2.6) * p.dropSize * g.k, sx: rand(0.75, 1.1) };
  }
  /** 打一道闪电：亮度包络（闪两下），有一定概率能看到闪电本身。 */
  function strike() {
    const g = FX.geo, P = FX.params.lightning, pts = [];
    if (!g) return;
    if (Math.random() < P.bolt) {
      let x = rand(g.left + g.w * 0.15, g.right - g.w * 0.15), y = g.top - 10;
      const yEnd = g.top + (g.bottom - g.top) * rand(0.45, 0.7);
      pts.push([x, y]);
      while (y < yEnd) { y += rand(8, 22) * g.k; x += rand(-14, 14) * g.k; pts.push([x, y]); }
    }
    FX.flash = { t: 0, pts };
  }
  function flashLevel() {
    const f = FX.flash;
    if (!f) return 0;
    const t = f.t;
    return t < 0.05 ? t / 0.05 : t < 0.12 ? 1 - (t - 0.05) / 0.07 * 0.75 : t < 0.2 ? 0.25 + (t - 0.12) / 0.08 * 0.6 : Math.max(0, 0.85 * (1 - (t - 0.2) / 0.7));
  }

  /* ───────── 烘焙（钟点 / 天气 / 参数 / 大小变了才重做） ───────── */
  const blurPx = (x) => Math.max(0, x);
  function offscreen(w, h) { const c = document.createElement("canvas"); c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h)); const x = c.getContext("2d"); return [c, x]; }
  /** 天空底图：渐变 + 地平线的光 + 太阳 / 月亮，按远景层的分辨率画好、模糊烘焙进去。 */
  function bakeSky() {
    const g = FX.geo, info = FX.info, P = FX.params, sc = g.back, key = [info.top, info.bot, info.glow, info.glowA, JSON.stringify(info.sun), JSON.stringify(info.moon), g.w, g.h, sc, P.view.dof, P.view.skyBlur].join("|");
    if (FX.bake.skyKey === key) return FX.bake.sky;
    const [cv, c] = offscreen(g.w * sc, g.h * sc);
    c.scale(sc, sc);
    const b = blurPx((P.view.dof + P.view.skyBlur) * g.k) * sc;
    const top = g.sky(0, 330), bot = g.sky(0, 40), grd = c.createLinearGradient(0, top[1], 0, bot[1]);
    grd.addColorStop(0, info.top); grd.addColorStop(1, info.bot);
    c.fillStyle = grd; c.fillRect(0, 0, g.w, g.h);
    if (info.glowA > 0.01) {                                      // 地平线的光（清晨 / 黄昏最明显）
      const h0 = g.sky(0, 140), h1 = g.sky(0, 20), gg = c.createLinearGradient(0, h0[1], 0, h1[1]);
      gg.addColorStop(0, hexA(info.glow, 0)); gg.addColorStop(1, hexA(info.glow, info.glowA));
      c.fillStyle = gg; c.fillRect(0, h0[1], g.w, g.h - h0[1]);
    }
    if (b > 0.3) c.filter = `blur(${(b / sc).toFixed(2)}px)`;     // 只有太阳 / 月亮有边，模糊只作用在它们身上（渐变本来就是糊的）
    const ssc = skyScale();
    if (info.sun) {
      const p = g.sky(-200 + info.sun.u * 380, 90 + Math.sin(Math.PI * info.sun.u) * 175), rg = c.createRadialGradient(p[0], p[1], 0, p[0], p[1], 46 * ssc);
      rg.addColorStop(0, hexA(info.sun.color, 1)); rg.addColorStop(0.32, hexA(info.sun.color, 0.95)); rg.addColorStop(0.36, hexA(info.sun.color, 0.35)); rg.addColorStop(1, hexA(info.sun.color, 0));
      c.fillStyle = rg; c.beginPath(); c.arc(p[0], p[1], 46 * ssc, 0, 6.283); c.fill();
    }
    if (info.moon) {
      const p = g.sky(-200 + info.moon.u * 380, 110 + Math.sin(Math.PI * info.moon.u) * 150), q = g.sky(-200 + info.moon.u * 380 + 6, 113 + Math.sin(Math.PI * info.moon.u) * 150);
      c.fillStyle = "rgba(247,241,216,.16)"; c.beginPath(); c.arc(p[0], p[1], 22 * ssc, 0, 6.283); c.fill();
      c.fillStyle = "#f7f1d8"; c.beginPath(); c.arc(p[0], p[1], 13 * ssc, 0, 6.283); c.fill();
      c.fillStyle = info.top; c.beginPath(); c.arc(q[0], q[1], 11 * ssc, 0, 6.283); c.fill();
    }
    FX.bake.sky = cv; FX.bake.skyKey = key;
    return cv;
  }
  const skyScale = () => { const g = FX.geo, a = g.sky(0, 150), b = g.sky(1, 150); return Math.hypot(b[0] - a[0], b[1] - a[1]); };
  /** 远云的小图：几团叠起来的圆，模糊烘焙进去（4 种样子，轮流用）。 */
  function bakeClouds() {
    const g = FX.geo, info = FX.info, P = FX.params, sc = skyScale() * P.farCloud.scale, res = g.back, key = [info.cloud, sc.toFixed(3), res, P.view.dof, P.farCloud.blur].join("|");
    if (FX.bake.cloudKey === key) return FX.bake.clouds;
    const b = blurPx((P.view.dof + P.farCloud.blur) * g.k) * res, list = [];
    for (let i = 0; i < 4; i++) {
      const w = Math.max(8, 100 * sc * 1.6 * res), h = w * 0.55, m = Math.ceil(b * 2 + 2), [cv, c] = offscreen(w + m * 2, h + m * 2), puffs = 5 + i;
      if (b > 0.3) c.filter = `blur(${b.toFixed(2)}px)`;
      c.fillStyle = info.cloud;
      c.translate(m, m);
      for (let j = 0; j < puffs; j++) { const t = j / (puffs - 1), r = h * (0.22 + 0.16 * Math.sin(Math.PI * t)) * (0.85 + ((i * 7 + j * 3) % 5) * 0.06); c.beginPath(); c.arc(w * (0.18 + 0.64 * t), h * (0.62 - 0.12 * Math.sin(Math.PI * t)), r, 0, 6.283); c.fill(); }
      c.fillRect(w * 0.18, h * 0.6, w * 0.64, h * 0.22);
      list.push({ cv, w: (w + m * 2) / res, h: (h + m * 2) / res });
    }
    FX.bake.clouds = list; FX.bake.cloudKey = key;
    return list;
  }
  /** 树和坡：room3d 单独画的 SVG → 图，按远景层的分辨率裁出窗洞那一块、模糊烘焙进去（异步，图好之前先不画）。 */
  function bakeOutdoor() {
    const g = FX.geo, P = FX.params, res = g.back, svg = FX.r && FX.r.outdoorSvg, key = [svg ? svg.length : 0, FX.r && FX.r.viewBox.join(","), g.x0, g.y0, g.w, g.h, g.W, g.H, res, P.view.dof, P.view.outdoorBlur].join("|");
    if (FX.bake.outdoorKey === key) return FX.bake.outdoor;
    FX.bake.outdoorKey = key; FX.bake.outdoor = null;
    if (!svg) return null;
    const src = window.PhalRoom3D.toOutdoorSvg(FX.r).replace("<svg ", `<svg width="${g.W}" height="${g.H}" preserveAspectRatio="xMidYMid slice" `);
    const img = new Image();
    img.onload = () => {
      if (FX.bake.outdoorKey !== key) return;                    // 等图的时候又变了
      const [cv, c] = offscreen(g.w * res, g.h * res), b = blurPx((P.view.dof * 0.8 + P.view.outdoorBlur) * g.k) * res;
      if (b > 0.3) c.filter = `blur(${b.toFixed(2)}px)`;
      c.drawImage(img, g.x0, g.y0, g.w, g.h, 0, 0, g.w * res, g.h * res);
      FX.bake.outdoor = cv;
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(src);
    return null;
  }
  /** 玻璃上的雨滴：低分辨率小画布，每秒刷新 glass.fps 次。 */
  function bakeGlass(now, info) {
    const g = FX.geo, P = FX.params.glass, res = g.front * 0.5;
    let b = FX.bake.glass;
    if (!b || b.w !== Math.round(g.w * res) || b.h !== Math.round(g.h * res)) { const [cv, c] = offscreen(g.w * res, g.h * res); b = FX.bake.glass = { cv, c, w: cv.width, h: cv.height, at: 0, empty: true }; }
    const empty = !FX.drops.length && !FX.runners.length && !FX.trails.length;
    if (empty && b.empty) return null;
    if (now - b.at < 1000 / Math.max(1, P.fps)) return b.empty ? null : b.cv;
    b.at = now;
    const c = b.c;
    c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, b.w, b.h); c.setTransform(res, 0, 0, res, 0, 0);
    b.empty = empty;
    if (!empty) drawGlass(c, info);
    return b.empty ? null : b.cv;
  }

  /* ───────── 画 ───────── */
  function ctxOf(cv, sc) { const x = cv.getContext("2d"); x.setTransform(1, 0, 0, 1, 0, 0); x.clearRect(0, 0, cv.width, cv.height); x.setTransform(sc, 0, 0, sc, 0, 0); return x; }
  function draw(now) {
    const g = FX.geo, info = FX.info, P = FX.params;
    if (!g || !info || !P || !FX.els) return;
    const fl = flashLevel() * P.lightning.strength;
    // 远景层（低分辨率）
    const c = ctxOf(FX.els.back, g.back);
    c.drawImage(bakeSky(), 0, 0, g.w, g.h);
    if (info.stars) {
      c.fillStyle = "#ffffff";
      const ssc = skyScale();
      FX.stars.forEach((s) => { const p = g.sky(s.x, s.z), tw = 1 - P.sky.starTwinkle * (0.5 + 0.5 * Math.sin(FX.t * s.hz * 6.283 + s.ph)); c.globalAlpha = clamp(0.75 * info.stars * info.d * tw, 0, 1); c.fillRect(p[0] - s.r * ssc * 0.5, p[1] - s.r * ssc * 0.5, s.r * ssc, s.r * ssc); });
      c.globalAlpha = 1;
    }
    if (FX.clouds.length) {
      const list = bakeClouds();
      c.globalAlpha = clamp(info.cloudA * P.farCloud.opacity, 0, 1);
      FX.clouds.forEach((cl, i) => { const p = g.sky(cl.x, cl.z), sp = list[i % list.length], w = sp.w * (cl.w / 100), h = sp.h * (cl.w / 100); if (p[0] + w / 2 < 0 || p[0] - w / 2 > g.w) return; c.drawImage(sp.cv, p[0] - w / 2, p[1] - h / 2, w, h); });
      c.globalAlpha = 1;
    }
    if (FX.flash && FX.flash.pts.length && FX.flash.t < 0.35) {     // 闪电（在树后面）：外面一圈光晕 + 白芯
      const a = 1 - FX.flash.t / 0.35, path = () => { c.beginPath(); FX.flash.pts.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]))); };
      c.lineJoin = "round"; c.lineCap = "round";
      c.strokeStyle = `rgba(190,210,255,${(0.55 * a).toFixed(3)})`; c.lineWidth = 9 * g.k; path(); c.stroke();
      c.strokeStyle = `rgba(255,255,255,${a.toFixed(3)})`; c.lineWidth = 3.4 * g.k; path(); c.stroke();
    }
    if (fl > 0.01) { c.fillStyle = `rgba(225,232,255,${(fl * 0.75).toFixed(3)})`; c.fillRect(0, 0, g.w, g.h); }
    const od = bakeOutdoor();
    if (od) c.drawImage(od, 0, 0, g.w, g.h);
    if (info.hazeA > 0.01) { c.fillStyle = hexA(info.haze, info.hazeA); c.fillRect(0, 0, g.w, g.h); }
    const rainCol = info.d > 0.5 ? "#9fb3da" : mixHex("#f4f8ff", info.top, 0.15);
    drawRain(c, FX.rain.far, rainCol, P.rain.width * 0.7); drawSnow(c, FX.snow.far, info);
    // 近景层
    const n = ctxOf(FX.els.front, g.front);
    drawRain(n, FX.rain.near, rainCol, P.rain.width); drawSnow(n, FX.snow.near, info);
    if (FX.splash.length) {
      n.fillStyle = hexA(rainCol, P.splash.alpha);
      n.beginPath();
      FX.splash.forEach((s) => { n.moveTo(s.x + s.r, s.y); n.arc(s.x, s.y, s.r, 0, 6.283); });
      n.fill();
    }
    drawPile(n, info);
    if (fl > 0.01) { n.fillStyle = `rgba(235,240,255,${(fl * 0.28).toFixed(3)})`; n.fillRect(0, 0, g.w, g.h); }
    const gl = bakeGlass(now, info);
    if (gl) n.drawImage(gl, 0, 0, g.w, g.h);
    // 屋里跟着闪：只在闪的时候显示这一层（平时 display:none，不参与合成）
    const fo = fl * P.lightning.indoor;
    FX.els.flash.style.display = fo > 0.005 ? "block" : "none";
    if (fo > 0.005) FX.els.flash.style.opacity = fo.toFixed(3);
  }
  function drawRain(c, arr, col, width) {
    if (!arr.length) return;
    c.lineCap = "round";
    // 按透明度分 4 档，一档一条 path（快）
    for (let b = 0; b < 4; b++) {
      c.beginPath(); let any = false, aSum = 0, n = 0;
      arr.forEach((d) => { if (Math.min(3, Math.floor(d.a * 4)) !== b) return; any = true; aSum += d.a; n++; c.moveTo(d.x, d.y); c.lineTo(d.x - Math.sin(d.ang) * d.len, d.y - Math.cos(d.ang) * d.len); });
      if (!any) continue;
      c.strokeStyle = hexA(col, aSum / n); c.lineWidth = width * FX.geo.k; c.stroke();
    }
  }
  /* 小贴图（画一次，之后每帧只 drawImage）：雪花是一个软边的圆点，水珠是亮底 + 下沿暗边 + 高光点 */
  const SPRITES = {};
  function sprite(key, size, paint) {
    if (SPRITES[key]) return SPRITES[key];
    const cv = document.createElement("canvas"); cv.width = cv.height = size;
    paint(cv.getContext("2d"), size);
    return (SPRITES[key] = cv);
  }
  const flakeSprite = (night) => sprite(night ? "flake-n" : "flake-d", 32, (c, n) => {
    const g = c.createRadialGradient(n / 2, n / 2, 0, n / 2, n / 2, n / 2), col = night ? "223,230,247" : "255,255,255";
    g.addColorStop(0, `rgba(${col},1)`); g.addColorStop(0.6, `rgba(${col},1)`); g.addColorStop(1, `rgba(${col},0)`);
    c.fillStyle = g; c.fillRect(0, 0, n, n);
  });
  const beadSprite = (night) => sprite(night ? "bead-n" : "bead-d", 48, (c, n) => {
    const x = n / 2, y = n / 2, r = n * 0.42;
    c.fillStyle = night ? "rgba(170,190,230,.24)" : "rgba(255,255,255,.34)";
    c.beginPath(); c.arc(x, y, r, 0, 6.283); c.fill();
    c.strokeStyle = night ? "rgba(20,28,50,.45)" : "rgba(55,75,110,.42)"; c.lineWidth = r * 0.32;
    c.beginPath(); c.arc(x, y, r * 0.9, 0.15 * Math.PI, 0.85 * Math.PI); c.stroke();
    c.fillStyle = night ? "rgba(220,230,255,.6)" : "rgba(255,255,255,.85)";
    c.beginPath(); c.arc(x - r * 0.35, y - r * 0.4, r * 0.28, 0, 6.283); c.fill();
  });
  function drawSnow(c, arr, info) {
    if (!arr.length) return;
    const sp = flakeSprite(info.d > 0.5);
    arr.forEach((f) => { const x = f.x + Math.sin(f.ph) * f.amp, d = f.r * 2.3; c.globalAlpha = f.a; c.drawImage(sp, x - d / 2, f.y - d / 2, d, d); });
    c.globalAlpha = 1;
  }
  function drawPile(c, info) {
    const g = FX.geo, a = FX.pile;
    if (a < 0.01) return;
    const dep = a * FX.params.snow.accumMax * g.k, n = 40, x0 = g.hole[0][0], x1 = g.hole[1][0], col = info.d > 0.5 ? "#c9d3ea" : "#fbfcff";
    c.beginPath(); c.moveTo(x0, g.ledgeY(x0) + 1);
    for (let i = 0; i <= n; i++) { const x = x0 + ((x1 - x0) * i) / n, bump = 0.78 + 0.14 * Math.sin(i * 1.7) + 0.08 * Math.sin(i * 4.3 + 1), edge = Math.min(1, Math.min(i, n - i) / 3); c.lineTo(x, g.ledgeY(x) - dep * bump * (0.55 + 0.45 * edge)); }
    c.lineTo(x1, g.ledgeY(x1) + 1); c.closePath();
    c.fillStyle = col; c.fill();
    c.strokeStyle = info.d > 0.5 ? "rgba(120,140,190,.5)" : "rgba(150,170,210,.45)"; c.lineWidth = 1 * g.k; c.stroke();
  }
  function drawGlass(c, info) {
    const g = FX.geo, P = FX.params.glass;
    // 只画在窗洞里
    c.save(); c.beginPath(); g.hole.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]))); c.closePath(); c.clip();
    if (FX.trails.length) {
      c.lineCap = "round"; c.lineJoin = "round";
      FX.trails.forEach((tr) => { c.strokeStyle = `rgba(225,235,250,${(0.22 * P.trail * tr.life).toFixed(3)})`; c.lineWidth = tr.w * 1.6; c.beginPath(); tr.path.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]))); c.stroke(); });
    }
    FX.runners.forEach((ru) => { c.strokeStyle = `rgba(225,235,250,${(0.3 * P.trail).toFixed(3)})`; c.lineWidth = ru.r * 1.3; c.beginPath(); ru.path.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]))); c.stroke(); bead(c, ru.x, ru.y, ru.r * 1.1, 1.25, info); });
    FX.drops.forEach((d) => bead(c, d.x, d.y, d.r, d.sx, info));
    c.restore();
  }
  /** 一颗水珠：淡淡的亮底 + 下沿一道暗边 + 左上角一个高光点（像透镜）；贴图按 r、竖向比例 sy 缩放。 */
  function bead(c, x, y, r, sy, info) {
    const w = r / 0.42;
    c.drawImage(beadSprite(info.d > 0.5), x - w / 2, y - (w * sy) / 2, w, w * sy);
  }

  window.PhalWeatherFX = { mount, unmount, setActive, setQuality };
})();
