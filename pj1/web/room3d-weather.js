/* 窗外天气（v7）：配色表 + 参数 + 室内光照。纯函数，浏览器和 node 都能用（tests/room3d_test.js 直接调）。
 *
 * · 配色表：时段（清晨 / 白天 / 黄昏 / 夜晚）× 天气（晴 / 阴 / 雨 / 雪），每格是一组颜色和透明度（天空渐变、地平线的光、云、雾气、
 *   树和坡的色偏、室内光）。实际用的是按钟点混合出来的：日出前后偏清晨、日落前后偏黄昏，两头渐变过去（每小时一档）。
 * · 天气种类（服务器给的 7 种）先归到四类里，再加修饰：多云 = 晴 + 云多、阳光弱；雾 = 阴 + 浓雾；雷雨 = 雨 + 更暗 + 闪电。
 * · 参数（DEFAULTS）：每层（天空 / 远云 / 近景天气 / 玻璃）的速度、透明度、模糊，雨、雪、溅水、积雪、闪电、室内光的强度……
 * · 室内光照（lightSvg）：按配色表给整间房盖一层光色（雨天暗、偏冷；夜里暗蓝），夜里 / 阴雨天落地灯和显示器有微光；
 *   晴天有太阳从南窗斜射进来：窗洞（按窗框分成几格）沿着阳光方向投到地上的亮斑 + 一道道淡淡的光柱。
 */
(function (root) {
  "use strict";
  const S3 = root.RoomScene3D || require("./room3d-scene.js");
  const Iso = root.PhalIso || require("./iso.js");
  const mix = Iso.mix;
  const n2 = (v) => Math.round(v * 100) / 100;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const L = S3.LAYOUT, WIN = S3.WIN, ROOM = S3.ROOM;

  const PERIODS = ["dawn", "day", "dusk", "night"];
  const PERIOD_LABEL = { dawn: "清晨", day: "白天", dusk: "黄昏", night: "夜晚" };
  const CATS = ["clear", "overcast", "rain", "snow"];
  const CAT_LABEL = { clear: "晴", overcast: "阴", rain: "雨", snow: "雪" };
  // 服务器给的天气种类 → 四类之一 + 修饰（clouds 云量、sun 阳光、rain / snow 强度、fog 雾、thunder 闪电、dark 再压暗）
  const KINDS = {
    clear: { cat: "clear", clouds: 0.25, sun: 1 },
    cloudy: { cat: "clear", clouds: 0.7, sun: 0.55 },
    overcast: { cat: "overcast", clouds: 1, sun: 0 },
    fog: { cat: "overcast", clouds: 0.45, sun: 0.08, fog: 1 },
    rain: { cat: "rain", clouds: 1, sun: 0, rain: 1 },
    thunder: { cat: "rain", clouds: 1, sun: 0, rain: 1.45, thunder: 1, dark: 0.22 },
    snow: { cat: "snow", clouds: 0.85, sun: 0, snow: 1 },
  };
  const KIND_LABEL = { clear: "晴", cloudy: "多云", overcast: "阴", fog: "雾", rain: "雨", thunder: "雷雨", snow: "雪" };

  // 配色表的一格里有哪些东西（normalize 按这个补齐、检查参数）
  const CELL_COLORS = [["top", "天顶"], ["bot", "地平线"], ["glow", "地平线的光"], ["cloud", "云"], ["haze", "雾气"], ["outdoor", "树和坡的色偏"], ["indoor", "室内光色"]];
  const CELL_NUMS = [["glowA", "地平线的光（强度）"], ["cloudA", "云（不透明度）"], ["hazeA", "雾气（浓度）"], ["outdoorK", "树和坡的色偏（程度）"], ["indoorA", "室内光色（程度）"], ["sunA", "斜射的阳光"]];
  const cell = (top, bot, glow, glowA, cloud, cloudA, haze, hazeA, outdoor, outdoorK, indoor, indoorA, sunA) => ({ top, bot, glow, glowA, cloud, cloudA, haze, hazeA, outdoor, outdoorK, indoor, indoorA, sunA });
  const PALETTE = {
    clear: {
      dawn: cell("#7f9ed4", "#ffd3b0", "#ffb48c", 0.55, "#ffe0cf", 0.8, "#f6dccf", 0.08, "#7a6f9a", 0.22, "#ffd9b0", 0.07, 0.55),
      day: cell("#6db4ec", "#dcf0fb", "#f6fbff", 0.25, "#ffffff", 0.88, "#eef6fb", 0.04, "#ffffff", 0, "#fff3d6", 0.02, 1),
      dusk: cell("#6a74b4", "#ffb48e", "#ff9466", 0.6, "#ffc6a6", 0.82, "#f3c4b3", 0.1, "#7b5a7d", 0.3, "#ffbd8f", 0.09, 0.65),
      night: cell("#0e1633", "#283869", "#3a4f86", 0.25, "#2c3558", 0.7, "#28325a", 0.06, "#1d2b58", 0.72, "#16225a", 0.22, 0),
    },
    overcast: {
      dawn: cell("#8b95ab", "#d8cbc6", "#e6c6b6", 0.25, "#c3c4cc", 0.92, "#d6d6db", 0.18, "#7d7f96", 0.28, "#b9b6c4", 0.08, 0),
      day: cell("#a7b2c1", "#e0e5eb", "#eef1f4", 0.15, "#d3d8df", 0.92, "#e2e6ea", 0.16, "#8f98a6", 0.32, "#a9b2c2", 0.07, 0),
      dusk: cell("#7c8098", "#c9aaa3", "#d8a08b", 0.3, "#a9a3ad", 0.92, "#bfb3b4", 0.18, "#6f667f", 0.36, "#b49c9c", 0.1, 0),
      night: cell("#1b2034", "#363c55", "#454b66", 0.15, "#2f3448", 0.85, "#343a52", 0.14, "#1f2848", 0.75, "#1a2350", 0.25, 0),
    },
    rain: {
      dawn: cell("#6c7690", "#a9a9b6", "#b9a8a8", 0.18, "#8c90a0", 0.95, "#a3a8b5", 0.28, "#5b6480", 0.38, "#3f4c70", 0.14, 0),
      day: cell("#7d8aa0", "#bac5d2", "#ccd4de", 0.12, "#8e99ad", 0.95, "#b3bdc9", 0.26, "#66748c", 0.36, "#3d4a6e", 0.15, 0),
      dusk: cell("#5d6480", "#9a8f9a", "#a8888a", 0.2, "#7d7c8e", 0.95, "#9a96a3", 0.28, "#544e6c", 0.42, "#3a4068", 0.18, 0),
      night: cell("#121829", "#283046", "#33405c", 0.12, "#252c40", 0.9, "#2c344a", 0.2, "#16203f", 0.8, "#141c44", 0.3, 0),
    },
    snow: {
      dawn: cell("#8e9ab6", "#d9cdd0", "#e8c7bd", 0.28, "#c9cbd8", 0.88, "#dcdde6", 0.2, "#c9cde0", 0.3, "#c4cde6", 0.06, 0.1),
      day: cell("#9eabbf", "#d8dfe8", "#eef2f7", 0.18, "#c8d0dc", 0.9, "#e3e8ef", 0.2, "#dfe6f0", 0.38, "#c9d4ea", 0.06, 0.1),
      dusk: cell("#777d9f", "#c9b2b6", "#dba899", 0.3, "#aba6ba", 0.9, "#c4bccb", 0.2, "#a9a3c2", 0.36, "#b9b1d0", 0.08, 0.05),
      night: cell("#222a46", "#454f6d", "#56628a", 0.2, "#3b4462", 0.85, "#434c69", 0.18, "#26325c", 0.6, "#1d2756", 0.18, 0),
    },
  };

  /* 参数（定稿的出厂值）。长度 / 速度的单位都是「窗洞高 360px 时的像素」，按实际窗洞大小缩放。 */
  const DEFAULTS = {
    view: { dof: 1.2, skyBlur: 1.6, outdoorBlur: 0.9, backRes: 0.5, fps: 30 },
    sky: { starTwinkle: 0.35 },
    farCloud: { count: 6, speed: 5, opacity: 1, blur: 3, scale: 1 },
    rain: { density: 1, lenMin: 16, lenMax: 44, speedMin: 620, speedMax: 1050, alphaMin: 0.22, alphaMax: 0.62, angle: 11, angleJitter: 3, width: 1.1, farShare: 0.55, farScale: 0.5 },
    glass: { drops: 110, dropSize: 1.1, runRate: 0.35, runSpeed: 46, trail: 0.55, fps: 12 },
    splash: { rate: 22, size: 1, alpha: 0.7 },
    lightning: { rainPerMin: 0.35, thunderPerMin: 3, strength: 0.9, indoor: 0.45, bolt: 0.6 },
    snow: { density: 1.2, sizeMin: 1.3, sizeMax: 5.2, speedMin: 22, speedMax: 64, sway: 16, swayHz: 0.35, alpha: 0.9, farShare: 0.5, accumPerMin: 0.035, meltPerMin: 0.02, accumMax: 16 },
    light: { indoor: 1, rainDim: 1, sun: 1, sunMaxEl: 52, lamp: 1, shaftBlur: 6 },
  };

  const clone = (o) => JSON.parse(JSON.stringify(o));
  /** 出厂参数（含配色表的一份拷贝）。 */
  function defaults() { return Object.assign(clone(DEFAULTS), { palette: clone(PALETTE) }); }
  /** 预设里存的参数可能是旧版 / 只存了一部分：按出厂参数补齐，数字不合法的丢掉。 */
  function normalize(p) {
    const out = defaults();
    if (!p || typeof p !== "object") return out;
    Object.keys(DEFAULTS).forEach((g) => {
      const src = p[g];
      if (!src || typeof src !== "object") return;
      Object.keys(DEFAULTS[g]).forEach((k) => { const v = +src[k]; if (Number.isFinite(v)) out[g][k] = v; });
    });
    if (p.palette && typeof p.palette === "object") {
      CATS.forEach((c) => PERIODS.forEach((t) => {
        const s = p.palette[c] && p.palette[c][t];
        if (!s) return;
        CELL_COLORS.forEach(([k]) => { if (/^#[0-9a-f]{6}$/i.test(s[k] || "")) out.palette[c][t][k] = s[k].toLowerCase(); });
        CELL_NUMS.forEach(([k]) => { const v = +s[k]; if (Number.isFinite(v)) out.palette[c][t][k] = v; });
      }));
    }
    return out;
  }

  /** 这个钟点四个时段各占多少（加起来 = 1）：日出前后 1.4 小时偏清晨，日落前后偏黄昏，中间白天，其余夜晚。 */
  function periodWeights(t, rise, set) {
    const near = (c, span) => Math.max(0, 1 - Math.abs(t - c) / span);
    const dawn = near(rise, 1.4), dusk = near(set, 1.4);
    const isDay = t > rise && t < set ? 1 : 0;
    let day = isDay * (1 - Math.max(dawn, dusk)), night = (1 - isDay) * (1 - Math.max(dawn, dusk));
    const sum = dawn + dusk + day + night || 1;
    return { dawn: dawn / sum, day: day / sum, dusk: dusk / sum, night: night / sum };
  }
  /** 按权重把几格配色混在一起（颜色在 RGB 里线性混合）。 */
  function blendCells(cells, w) {
    const out = {};
    CELL_NUMS.forEach(([k]) => { out[k] = PERIODS.reduce((a, p) => a + (w[p] || 0) * cells[p][k], 0); });
    CELL_COLORS.forEach(([k]) => {
      let acc = null, tot = 0;
      PERIODS.forEach((p) => { const ww = w[p] || 0; if (ww <= 0) return; tot += ww; acc = acc == null ? cells[p][k] : mix(acc, cells[p][k], ww / tot); });
      out[k] = acc || cells.day[k];
    });
    return out;
  }

  /** 服务器给的 {kind, hour, sunrise, sunset} + 参数 → 这一小时窗外和屋里的样子（画静态部分、动画、光照都用它）。 */
  function resolve(amb, params) {
    if (!amb) return null;
    const p = params && params.palette ? params : normalize(params);
    const kind = KINDS[amb.kind] ? amb.kind : "clear", spec = KINDS[kind], cat = spec.cat;
    const hour = Math.floor(amb.hour == null ? 12 : amb.hour), t = hour + 0.5;
    const rise = amb.sunrise == null ? 6.6 : amb.sunrise, set = amb.sunset == null ? 17.8 : amb.sunset;
    const w = periodWeights(t, rise, set), c = blendCells(p.palette[cat], w);
    if (spec.dark) { c.top = mix(c.top, "#10131f", spec.dark); c.bot = mix(c.bot, "#1c2030", spec.dark * 0.8); c.cloud = mix(c.cloud, "#2a2e3c", spec.dark); c.indoorA = Math.min(0.6, c.indoorA + spec.dark * 0.25); }
    if (spec.fog) c.hazeA = Math.max(c.hazeA, 0.55 * spec.fog);
    // 暗度 d：0 白天 ~ 1 深夜（灯什么时候亮、窗外的星星）；和以前的 ambientInfo 一样按日出日落前后 1.5 小时渐变
    let d;
    if (t < rise - 0.6 || t > set + 0.6) d = 1;
    else if (t < rise + 0.9) d = 1 - (t - (rise - 0.6)) / 1.5;
    else if (t > set - 0.9) d = (t - (set - 0.9)) / 1.5;
    else d = 0;
    d = clamp(d, 0, 1);
    const span = (set - rise) || 12, u = (t - rise) / span, night = d > 0.55, nightLen = ((rise - set + 24) % 24) || 12, un = ((t - set + 24) % 24) / nightLen;
    const showSun = !night && u > 0.02 && u < 0.98 && spec.sun > 0.3, showMoon = night && un > 0 && un < 1 && spec.clouds < 0.8;
    const maxEl = (p.light.sunMaxEl * Math.PI) / 180, el = Math.sin(Math.PI * clamp(u, 0, 1)) * maxEl, az = 90 + 180 * clamp(u, 0, 1);
    const warm = (w.dawn + w.dusk) * (cat === "clear" ? 1 : 0.4);
    const lampOn = clamp(d + (cat === "rain" ? 0.35 : cat === "overcast" ? 0.12 : 0) * (1 - d), 0, 1);
    return {
      kind, label: KIND_LABEL[kind], cat, catLabel: CAT_LABEL[cat], hour, t, rise, set, w, d, warm, params: p,
      top: c.top, bot: c.bot, glow: c.glow, glowA: c.glowA, cloud: c.cloud, cloudA: c.cloudA, haze: c.haze, hazeA: c.hazeA,
      outdoor: c.outdoor, outdoorK: c.outdoorK, indoor: c.indoor, indoorA: c.indoorA * p.light.indoor * (cat === "rain" || cat === "overcast" ? p.light.rainDim : 1),
      sunA: c.sunA * spec.sun * p.light.sun * (el > 0.05 ? 1 : 0), lamp: lampOn * p.light.lamp,
      clouds: spec.clouds, rain: spec.rain || 0, snow: spec.snow || 0, fog: spec.fog || 0, thunder: spec.thunder || 0,
      clear: { clear: 1, cloudy: 0.7, overcast: 0.25, fog: 0.2, rain: 0.1, thunder: 0.05, snow: 0.25 }[kind],
      sunDir: { az, el },
      sun: showSun ? { u, color: warm > 0.35 ? "#ffc27d" : "#ffe9a8" } : null,
      moon: showMoon ? { u: un } : null,
      stars: night && spec.clouds < 0.8 ? (kind === "clear" ? 1 : 0.5) : 0,
    };
  }

  /** 树 / 坡 / 樱花树：按配色表的色偏混过去（夜里暗蓝、阴雨天发灰）。 */
  function tintOutdoor(pal, info, sid) {
    if (!info) return pal;
    const out = Object.assign({}, pal), soft = 0;
    ["tree", "hill", "cherry"].forEach((k) => { out[k] = mix(mix(pal[k], info.outdoor, clamp(info.outdoorK, 0, 1)), "#fffdf6", soft * (1 - info.d * 0.6)); });
    out.sky = info.bot;
    return out;
  }

  /* ───────────────── 室内光照（盖在整幅画上的一层 SVG） ───────────────── */
  /** 太阳光线的方向（从太阳射向屋里，单位向量）。方位角从北往东量；世界坐标 x 往西、y 往南。 */
  function sunRay(sd) {
    const b = (sd.az * Math.PI) / 180, el = sd.el, h = Math.cos(el);
    return [Math.sin(b) * h, Math.cos(b) * h, -Math.sin(el)];                  // = −(朝向太阳的方向)
  }
  /** 多边形按半平面裁剪（Sutherland–Hodgman）：保留 f(p) >= 0 的部分。 */
  function clipPoly(poly, f) {
    const out = [];
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i], b = poly[(i + 1) % poly.length], fa = f(a), fb = f(b);
      if (fa >= 0) out.push(a);
      if ((fa >= 0) !== (fb >= 0)) { const k = fa / (fa - fb); out.push(a.map((v, j) => v + (b[j] - v) * k)); }
    }
    return out;
  }
  /** 阳光透过每一格窗投到某个水平面（高 z）上的亮斑，裁到 rects（[x0,x1,y0,y1] 若干块）里：[{ win: 窗格四角, floor: 亮斑 }]。
   *  默认是地面（整间房）；桌面用 DESK_RECTS。 */
  const FLOOR_RECTS = [[ROOM.xe, ROOM.xw, ROOM.yn, ROOM.ys]];
  const DESK_RECTS = [[ROOM.xe, 15, ROOM.yn, 95], [ROOM.xe, -160, 95, 180]];        // L 形书桌的两块桌面（和 room3d-scene.js 一致）
  function sunPatches(info, panes, z = 0, rects = FLOOR_RECTS) {
    if (!info || info.sunA < 0.02) return [];
    const r = sunRay(info.sunDir);
    if (r[1] >= -0.05 || r[2] >= -0.05) return [];                              // 太阳在北边（光照不进南窗）或贴着地平线
    const yo = ROOM.ys, out = [];
    panes.forEach(([x0, x1]) => {
      const win = [[x0, yo, WIN.sill], [x1, yo, WIN.sill], [x1, yo, WIN.top], [x0, yo, WIN.top]];
      const hit = win.map((p) => { const k = (p[2] - z) / -r[2]; return [p[0] + r[0] * k, p[1] + r[1] * k, z]; });
      rects.forEach(([ax, bx, ay, by]) => {
        let f = clipPoly(hit, (p) => p[1] - ay);
        f = clipPoly(f, (p) => by - p[1]);
        f = clipPoly(f, (p) => p[0] - ax);
        f = clipPoly(f, (p) => bx - p[0]);
        if (f.length >= 3) out.push({ win, floor: f });
      });
    });
    return out;
  }
  /** 地上 / 桌面上的亮斑（一段 SVG）：插在画家顺序里地板 / 桌面的后面，家具和桌上的东西会把它挡住。 */
  function sunPatchSvg(info, cam, panes, lineK, plane) {
    const pats = plane === "desk" ? sunPatches(info, panes, L.DESK + 0.2, DESK_RECTS) : sunPatches(info, panes);
    if (!pats.length) return "";
    const u = ++UID, sa = clamp(info.sunA, 0, 2), col = info.warm > 0.3 ? "#ffcf8a" : "#fff1c8", blur = (info.params ? info.params.light.shaftBlur : 6) * lineK * 0.6;
    return `<defs><filter id="r3sp${u}" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="${n2(blur)}"/></filter></defs><g pointer-events="none" filter="url(#r3sp${u})">`
      + pats.map((pt) => `<path d="${polyD(pt.floor.map((p) => cam.project(p)))}" fill="${col}" fill-opacity="${n2(clamp(0.3 * sa, 0, 0.6))}"/>`).join("") + `</g>`;
  }
  /** 窗户被窗框竖挺分成的几格（x 范围）。mullions：竖挺的 [x0, x1]（从场景里取）。 */
  function panesOf(mullions) {
    const ms = (mullions || []).slice().sort((a, b) => a[0] - b[0]), out = [];
    let x = WIN.x0;
    ms.forEach(([a, b]) => { if (a > x + 2) out.push([x, a]); x = Math.max(x, b); });
    if (WIN.x1 > x + 2) out.push([x, WIN.x1]);
    return out;
  }
  /** 窗洞（墙外侧那一圈，y = 南墙外皮）在画面里的四个角。 */
  function holeOf(cam) {
    const y = ROOM.ys + 10;
    return [[WIN.x0, y, WIN.sill], [WIN.x1, y, WIN.sill], [WIN.x1, y, WIN.top], [WIN.x0, y, WIN.top]].map((p) => cam.project(p));
  }
  const polyD = (pts) => `M${pts.map((p) => `${n2(p[0])} ${n2(p[1])}`).join("L")}Z`;

  let UID = 0;
  /** 室内光照层：光色（按配色表）+ 灯 / 屏幕的微光 + 斜射的阳光。
   *  shapes：房间视角里房间画出来的轮廓（光色只盖在它们上面）；hole：书桌视角里窗洞的四个角（现在不用了：光色整幅都盖）。 */
  function lightSvg(info, cam, vb, mode, lineK, opts) {
    if (!info) return "";
    const o = opts || {}, u = ++UID, pj = (x, y, z) => cam.project([x, y, z]);
    let s = "";
    // 光色整幅都盖（书桌视角不再挖掉窗洞：挖掉的话，挡在窗前的椅子、台灯、她都不变暗，看起来像一个亮着的画框）
    const area = `<rect x="${vb[0] - 4}" y="${vb[1] - 4}" width="${vb[2] + 8}" height="${vb[3] + 8}"`;
    const clip = o.shapes && o.shapes.length && cam.kind !== "persp" ? `<clipPath id="r3wl${u}">${o.shapes.map((d) => `<path d="${d}"/>`).join("")}</clipPath>` : "";
    const clipAttr = clip ? ` clip-path="url(#r3wl${u})"` : "";
    // 1. 光色：雨天暗、偏冷；夜里暗蓝；清晨黄昏一点暖色
    const a = clamp(info.indoorA, 0, 0.75), col = mode === "wash" ? mix(info.indoor, "#2a2f62", 0.15) : info.indoor;
    if (a > 0.005) s += `${clip}<g pointer-events="none"${clipAttr}>${area} fill="${col}" fill-opacity="${n2(a)}"/></g>`;
    // 2. 斜射的阳光：从窗格到地上亮斑的一道道淡淡的光柱（普通叠加，不用 screen 混合：混合模式盖在动画层上面会让 GPU 每帧重算，实测很卡）（空气里的光，所以盖在所有东西上面；亮斑本身在 sunPatchSvg 里，按前后顺序插进画面）
    const pats = sunPatches(info, o.panes || [[WIN.x0, WIN.x1]]);
    if (pats.length && cam.kind === "persp") {
      const blur = (info.params ? info.params.light.shaftBlur : 6) * lineK, sa = clamp(info.sunA, 0, 2), warmC = info.warm > 0.3 ? "#ffcf8a" : "#fff1c8";
      s += `<defs><filter id="r3sb${u}" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="${n2(blur)}"/></filter></defs><g pointer-events="none" filter="url(#r3sb${u})"${clipAttr}>`;
      pats.forEach((pt, i) => {
        const fl = pt.floor.map((p) => cam.project(p)), wn = pt.win.map((p) => cam.project(p));
        const hull = convexHull(fl.concat(wn)), cW = avg(wn), cF = avg(fl), gid = `r3sg${u}_${i}`;
        s += `<linearGradient id="${gid}" gradientUnits="userSpaceOnUse" x1="${n2(cW[0])}" y1="${n2(cW[1])}" x2="${n2(cF[0])}" y2="${n2(cF[1])}"><stop offset="0" stop-color="${warmC}" stop-opacity="${n2(0.12 * sa)}"/><stop offset=".6" stop-color="${warmC}" stop-opacity="${n2(0.03 * sa)}"/><stop offset="1" stop-color="${warmC}" stop-opacity="0"/></linearGradient>`;
        s += `<path d="${polyD(hull)}" fill="url(#${gid})"/>`;
      });
      s += `</g>`;
    }
    // 3. 灯和屏幕的微光（夜里、阴雨天）
    const g = clamp(info.lamp, 0, 2);
    if (g > 0.03) {
      const glow = (id, x, y, z, rcm, color, alpha, ax) => {
        const c = pj(x, y, z), ex = pj(x + rcm, y, z), ey = pj(x, y, z + rcm), rx = Math.hypot(ex[0] - c[0], ex[1] - c[1]), ry = Math.hypot(ey[0] - c[0], ey[1] - c[1]), rr = ax ? Math.max(rx, ry) : (rx + ry) / 2;
        return `<radialGradient id="${id}"><stop offset="0" stop-color="${color}" stop-opacity="${n2(alpha)}"/><stop offset=".45" stop-color="${color}" stop-opacity="${n2(alpha * 0.4)}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient><circle cx="${n2(c[0])}" cy="${n2(c[1])}" r="${n2(rr)}" fill="url(#${id})"/>`;
      };
      const floorPool = (id, x, y, rcm, color, alpha) => {
        const c = pj(x, y, 0), p1 = pj(x + rcm, y, 0), p2 = pj(x, y + rcm, 0);
        return `<radialGradient id="${id}"><stop offset="0" stop-color="${color}" stop-opacity="${n2(alpha)}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient><circle r="1" fill="url(#${id})" transform="matrix(${n2(p1[0] - c[0])} ${n2(p1[1] - c[1])} ${n2(p2[0] - c[0])} ${n2(p2[1] - c[1])} ${n2(c[0])} ${n2(c[1])})"/>`;
      };
      const deskView = cam.kind === "persp", monZ = L.DESK + 24;
      s += `<g pointer-events="none">`
        + floorPool(`r3lp${u}`, 60, 305, 70, "#ffd58a", 0.3 * g)
        + glow(`r3lg${u}`, 60, 305, 141, 90, "#ffe2a8", 0.5 * g)
        + floorPool(`r3ms${u}`, -100, 58, deskView ? 36 : 40, "#bfe0ff", 0.26 * g)
        + glow(`r3mg${u}`, L.MON[0], L.MON[1], monZ, deskView ? 34 : 50, "#b5dcff", 0.42 * g)
        + `</g>`;
    }
    return s;
  }
  function avg(pts) { return [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length]; }
  function convexHull(pts) {
    const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    if (p.length < 3) return p;
    const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], up = [];
    for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
    for (let i = p.length - 1; i >= 0; i--) { const q = p[i]; while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }

  const API = { PERIODS, PERIOD_LABEL, CATS, CAT_LABEL, KINDS, KIND_LABEL, CELL_COLORS, CELL_NUMS, PALETTE, DEFAULTS, defaults, normalize, periodWeights, blendCells, resolve, tintOutdoor, sunRay, sunPatches, sunPatchSvg, panesOf, holeOf, lightSvg, clipPoly };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.PhalWeatherLook = API;
})(typeof window !== "undefined" ? window : globalThis);
