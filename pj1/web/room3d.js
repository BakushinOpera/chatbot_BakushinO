/* 房间（新版）：greyBoxTest 的灰模 → 带风格的 SVG（房间视角 = 等距，书桌视角 = 透视）。
 *
 * 一份场景（room3d-scene.js，灰模的副本）+ 两个相机（room3d-persp.js）：
 *   · 房间视角 "room"：真等距，从东南上方看向西北，拆掉东墙 / 南墙（窗墙）/ 天花板；
 *   · 书桌视角 "desk"：透视，相机在北墙外面看向南偏东，隐藏北墙；整幅画 2000×1167，页面按「铺满」缩放。
 * 家具外观来自三套风格（A 软萌圆润 / B 贴纸白边 / C 细线精致，定义在 iso.js 的 STYLES 里，配色在这里补了新家具的部分）；
 * 书、小物件的外观参数和旧版一样（封面 / 书脊的画法直接用 iso.js 里的函数）。
 *
 * 这个文件只管「画」：不碰网络、不碰 DOM，浏览器和 Node 都能用。
 * 世界坐标（cm）= 灰模坐标：x 向西、y 向南、z 向上；方位角（bearing）= 从北往东量的度数（0 北 / 90 东 / 180 南 / 270 西）。
 *
 * 返回的 render 结果：
 *   { order: [{ o: 形体, svg, b }], viewBox, width, height, project(x,y,z), pxPerCm(x,y), insertIndex(box, after?), faceOf(dx,dy,x,y),
 *     view, hide, st, scene, hotspots }
 *   order 是画家算法排好的顺序（带白边 / 阴影的垫层）；页面据此挂点击、把角色插进画面里。
 */
(function (root) {
  "use strict";

  const P = root.RoomPersp || require("./room3d-persp.js");
  const S3 = root.RoomScene3D || require("./room3d-scene.js");
  const Iso = root.PhalIso || require("./iso.js");
  const X = root.PhalRoom3DX || require("./room3d-extras.js");
  const W = root.PhalWeatherLook || require("./room3d-weather.js");
  const { hull, tone, mix, spineSvg, spineFlatSvg, coverSvg, bookDefaults } = Iso;
  const L = S3.LAYOUT, R = S3.ROOM;

  const n2 = (v) => Math.round(v * 100) / 100;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const SHELL = new Set(["floor", "ceiling", "wallnorth", "walleast", "wallwest", "wall", "outdoor"]);
  const HIDE = { room: ["ceiling", "walleast", "wall", "outdoor"], desk: ["wallnorth"] };      // 和灰模的 hide 一致

  /* ───────────────── 配色：旧版的 pal + 新家具 ───────────────── */
  const EXTRA = {
    A: {
      ceiling: "#eef3fa", sky: "#e1eefb", tree: "#bcd6cb", hill: "#f4d6e1", cherry: "#f7dde6", poster: "#6d82ac", panel: "#7e92b8", mullion: "#f4f7fc",
      sofaSeat: "#aebfe0", sofaBack: "#9db1d8", sideboard: "#9bb2d3", sideTop: "#b9c9e3", lampStem: "#8ea7cb", lampShade: "#f2e4bd",
      monitor: "#7388b0", monitorStand: "#8ea3c6", mug: "#f2d5de", redMachine: "#e8a0ab", diffuser: "#d4e7de", plush: "#ead9cd",
      pile: ["#c8d4e8", "#e9c9d2", "#b6c8e4", "#e3d9c4", "#c9d8cf", "#d9cfe6", "#e8dcc0"], sketch: "#f1e6cc", noteClosed: "#f2bccb", nbCover: "#e9a8ba", page: "#fbf8f1",
      hutch: "#aebcd8", hutchBoard: "#cdd7ea", pot: "#e8d9c4", sprout: "#8fb7a8", chairBase: "#8196bb", shadow: "#2b4068",
    },
    B: {
      ceiling: "#f1f4fa", sky: "#e6f0fa", tree: "#c4dccf", hill: "#f6d9e3", cherry: "#f9e0e8", poster: "#7d8fb3", panel: "#8c9dc0", mullion: "#fbfcfe",
      sofaSeat: "#c4cfe8", sofaBack: "#b5c2e0", sideboard: "#aebcd8", sideTop: "#cdd7ea", lampStem: "#97a9ca", lampShade: "#f5e8c8",
      monitor: "#8394b8", monitorStand: "#9aaacb", mug: "#f5d7df", redMachine: "#eba7b1", diffuser: "#d9ebe2", plush: "#efdfd4",
      pile: ["#d3dcee", "#f0d3da", "#c3d1e8", "#ebe2d0", "#d2dfd6", "#e0d8ec", "#efe4cb"], noteClosed: "#f5c7d3", nbCover: "#eeb0c1", hutch: "#aebcd8", hutchBoard: "#cdd7ea",
      chairBase: "#8c9dc0",
    },
  };
  /** 配色：B 用 iso.js 里的 B + 这里补的新家具；A（水彩）在它上面整套换掉；窗外的树 / 坡跟着天色（amb，配色表见 room3d-weather.js）变。 */
  const palOf = (styleId, amb, weather) => {
    const sid = styleId === "A" ? "A" : "B", st = Iso.styleOf("B");
    const pal = X.palFor(sid, Object.assign({}, st.pal, EXTRA.A, EXTRA.B));
    return amb ? W.tintOutdoor(pal, W.resolve(amb, weather), sid) : pal;
  };

  /** 灰模里每个形体的主色（按所在的组 + 名字）。书、小物件自己带 col。 */
  function partColor(m, pal) {
    const n = m.name;
    switch (m.g) {
      case "floor": return pal.floor;
      case "ceiling": return pal.ceiling;
      case "wallnorth": return pal.wallN;
      case "walleast": case "wallwest": return pal.wallW;
      case "wall": return /^(sill|mullion)/.test(n) ? pal.mullion : n === "poster" ? pal.poster : n === "wall-panel" ? pal.panel : n === "wall-under" ? tone(pal.wallN, -0.04, 0) : pal.wallN;
      case "outdoor": return n === "sky" ? pal.sky : /^tree/.test(n) ? pal.tree : n === "tulip-hill" ? pal.hill : pal.cherry;
      case "sofa": return n === "seat" ? pal.sofaSeat : pal.sofaBack;
      case "corner": return n === "sideboard" ? pal.sideboard : n === "lamp-stem" ? pal.lampStem : pal.lampShade;
      case "desk": return /^top/.test(n) ? pal.deskTop : pal.deskLeg;
      case "props":
        if (n === "monitor") return pal.monitor;
        if (n === "monitor-stand") return pal.monitorStand;
        if (n === "mug" || n === "mug-handle") return pal.mug;
        if (n === "red-machine") return pal.redMachine;
        if (n === "diffuser") return pal.diffuser;
        if (n === "plush") return pal.plush;
        if (n === "west-book") return pal.pile[0];
        if (/^stack-/.test(n)) return pal.pile[1 + ((+n.slice(6) - 1) % (pal.pile.length - 1))];
        if (n === "sketchbook") return pal.sketch;
        if (n === "notebook-closed") return pal.noteClosed;
        if (n === "notebook-cover") return pal.nbCover;
        return pal.page;
      case "hutch":
        if (/^book-/.test(n)) return pal.pile[+n.slice(5) % pal.pile.length];
        return n === "pot" ? pal.pot : n === "sprout" ? pal.sprout : /^hutch-board/.test(n) ? pal.hutchBoard : pal.hutch;
      case "girlchair": return n === "seat" ? pal.chairSeat : n === "back" ? pal.chairBack : pal.chairBase;
      case "bookcase": return n === "back" ? pal.shelfBack : /^board/.test(n) ? pal.shelfBoard : n === "top" ? pal.shelfTop : n === "base" ? tone(pal.shelf, -0.08, 6) : pal.shelf;
      case "wardrobe": return n === "feet" ? tone(pal.wardrobe, -0.1, 6) : n === "cornice" ? pal.cornice : /^door/.test(n) ? pal.wardDoor : pal.wardrobe;
      case "bed": return n === "headboard" ? pal.bedHead : n === "frame" ? pal.bedFrame : n === "mattress" ? pal.mattress : /^pillow/.test(n) ? pal.pillow : pal.blanket;
      case "nightstand": return n === "feet" ? tone(pal.nsBody, -0.1, 6) : n === "top" ? pal.nsTop : /^drawer/.test(n) ? pal.drawer : pal.nsBody;
      case "foodtable": return n === "top" ? pal.deskTop : n === "shelf" ? tone(pal.deskTop, -0.06, 6) : pal.deskLeg;          // 和书桌同一套木色
      default: return "#cfd6e4";
    }
  }

  /** 每个形体的圆角 / 描边细节（rad：圆角半径 cm（再乘视角系数）；sharp：不圆角；thin：描边变细）。 */
  function flagsOf(m, st) {
    const g = m.g, n = m.name, f = {};
    if (SHELL.has(g) && g !== "floor") Object.assign(f, { sharp: true, noSticker: true, noRim: true, noHatch: true });
    if (g === "floor") Object.assign(f, { rad: 6, noSticker: true, noRim: true, noHatch: true });
    if (g === "wall" && /^(sill|mullion)/.test(n)) f.thin = 0.6;
    if (g === "outdoor") f.thin = 0.5;
    if (g === "bed") Object.assign(f, n === "headboard" ? { rad: 11 } : n === "frame" ? { rad: 7 } : n === "mattress" ? { rad: 8 } : /^pillow/.test(n) ? { pill: true } : { rad: 5 });
    if (g === "nightstand") f.rad = n === "feet" ? 3 : n === "top" ? 5 : /^drawer/.test(n) ? 3 : 6;
    if (g === "wardrobe") f.rad = n === "feet" ? 3 : n === "cornice" ? 5 : /^door/.test(n) ? 4 : 7;
    if (g === "desk") f.rad = /^top/.test(n) ? 6 : 2;
    if (g === "foodtable") f.rad = n === "top" ? 5 : 2;
    if (g === "girlchair") f.rad = n === "seat" ? 5 : n === "back" ? 4 : 2;
    if (g === "bookcase") f.rad = /^(side|board|top)/.test(n) ? 2 : 3;
    if (g === "sofa") f.rad = 8;
    if (g === "corner") f.rad = n === "sideboard" ? 5 : 3;
    if (g === "hutch") { f.rad = 1.5; f.thin = /^book/.test(n) ? 0.5 : 0.8; f.noSticker = true; }
    if (g === "props") {
      f.rad = 2.5;
      if (/^(west-book|stack|sketchbook|notebook-closed)/.test(n)) f.rad = 1.5;
      if (/^notebook-(cover|page)/.test(n)) { f.sharp = true; f.thin = 0.7; }
      if (n === "red-machine") f.rad = 4;
    }
    if (g === "bookcase" && n === "back") Object.assign(f, { sharp: true, noRim: true, noHatch: true });
    return f;
  }

  /* ───────────────── 几何 / 路径小工具 ───────────────── */
  function roundedPath(pts, r) {
    const n = pts.length;
    let d = "";
    for (let i = 0; i < n; i++) {
      const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n];
      const v1 = [p0[0] - p1[0], p0[1] - p1[1]], v2 = [p2[0] - p1[0], p2[1] - p1[1]];
      const l1 = Math.hypot(v1[0], v1[1]) || 1, l2 = Math.hypot(v2[0], v2[1]) || 1, rr = Math.min(r, l1 / 2, l2 / 2);
      const a = [p1[0] + (v1[0] / l1) * rr, p1[1] + (v1[1] / l1) * rr], b = [p1[0] + (v2[0] / l2) * rr, p1[1] + (v2[1] / l2) * rr];
      d += `${i ? "L" : "M"}${n2(a[0])} ${n2(a[1])}Q${n2(p1[0])} ${n2(p1[1])} ${n2(b[0])} ${n2(b[1])}`;
    }
    return d + "Z";
  }
  const polyPath = (pts) => `M${pts.map((p) => `${n2(p[0])} ${n2(p[1])}`).join("L")}Z`;
  const poly = (pts, fill, extra = "") => `<polygon points="${pts.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}" fill="${fill}"${extra}/>`;
  const minEdge = (pts) => Math.min(...pts.map((p, i) => Math.hypot(p[0] - pts[(i + 1) % pts.length][0], p[1] - pts[(i + 1) % pts.length][1])));
  const centroid = (vs) => vs.reduce((s, v) => [s[0] + v[0] / vs.length, s[1] + v[1] / vs.length, s[2] + v[2] / vs.length], [0, 0, 0]);
  let UID = 0;

  const toneCache = new Map();
  function shade(base, dl, cool) {
    const key = `${base}|${dl.toFixed(3)}|${cool}`;
    let c = toneCache.get(key);
    if (!c) { c = Math.abs(dl) < 0.002 ? base : tone(base, dl, cool); toneCache.set(key, c); }
    return c;
  }
  /** 一个面的颜色：k = 法线和光的夹角余弦。k≈0.8（朝上）→ 亮，k≈0.35 → 原色，k≤0（背光）→ 暗并往冷色偏。 */
  function faceColor(base, k, st) {
    const T = st.tone;
    if (k >= 0.35) return shade(base, ((Math.min(k, 0.8) - 0.35) / 0.45) * T.top, 0);
    const t = 1 - Math.max(k, 0) / 0.35;
    return shade(base, t * T.right, T.cool);
  }

  /* ───────────────── 一个形体的面（只算一次） ───────────────── */
  function prep(m) {
    if (m._f) return m._f;
    const cen = centroid(m.verts);
    m._f = m.faces.map((idx) => {
      const vs = idx.map((i) => m.verts[i]), fc = centroid(vs);
      let nrm = P.unit(P.cross(P.sub(vs[1], vs[0]), P.sub(vs[2], vs[0])));
      if (P.dot(nrm, P.sub(fc, cen)) < 0) nrm = nrm.map((s) => -s);
      return { vs, fc, nrm };
    });
    return m._f;
  }

  /* ───────────────── 面上的装饰（花纹、缝线、书脊） ─────────────────
   * 每个装饰写在某个面的局部坐标里（u 向右、v 向下，单位 cm）：d.face = "top" | "S" | "E" | "N" | "W"（朝哪面），或者 d.frame = { o, u, v, W, H, n }（书的书脊 / 封面用）。
   * 等距里是准确的仿射变换；透视里用面上三个点近似（书脊这么小，看不出差别）。 */
  const NORMAL = { top: [0, 0, 1], S: [0, 1, 0], N: [0, -1, 0], E: [-1, 0, 0], W: [1, 0, 0] };
  function frameOfFace(f) {
    const vs = f.vs, n = f.nrm, dot = P.dot;
    let u, v;
    if (Math.abs(n[2]) > 0.8) {                                           // 朝上：u 取最靠近「朝东」(-x) 的边方向，v 取最靠近「朝南」(+y) 的垂直方向
      let best = null;
      vs.forEach((a, i) => { const b = vs[(i + 1) % vs.length], e = [b[0] - a[0], b[1] - a[1], 0], l = Math.hypot(e[0], e[1]); if (l < 1e-6) return; const d = [e[0] / l, e[1] / l, 0], s = Math.abs(-d[0]); if (!best || s > best.s) best = { d, s, sign: -d[0] >= 0 ? 1 : -1 }; });
      if (!best) return null;
      u = best.d.map((q) => q * best.sign); v = [-u[1], u[0], 0];
      if (v[1] < 0) v = v.map((q) => -q);
    } else {                                                              // 竖直的面：站在外面看，u = 右手边，v = 往下
      u = [-n[1], n[0], 0]; const l = Math.hypot(u[0], u[1]); if (l < 1e-6) return null; u = u.map((q) => q / l); v = [0, 0, -1];
    }
    const pu = vs.map((p) => dot(p, u)), pv = vs.map((p) => dot(p, v)), u0 = Math.min(...pu), u1 = Math.max(...pu), v0 = Math.min(...pv), v1 = Math.max(...pv);
    // 原点 = 局部 (u0, v0) 那个角：沿 u、v 的分量定了，垂直于面的分量取面上一点
    const c = f.fc, du = u0 - dot(c, u), dv = v0 - dot(c, v), o = [c[0] + du * u[0] + dv * v[0], c[1] + du * u[1] + dv * v[1], c[2] + du * u[2] + dv * v[2]];
    // 斜面（纸页）：把 z 也带上（用面上三点解）
    if (Math.abs(n[2]) > 0.8 && Math.abs(n[2]) < 0.99999) { const dz = (dot(n, c) - dot(n, o)) / n[2]; o[2] += dz; }
    return { o, u, v, W: u1 - u0, H: v1 - v0, n };
  }
  function matrixOf(cam, fr) {
    const o = fr.o, p0 = cam.project(o), pu = cam.project([o[0] + fr.u[0], o[1] + fr.u[1], o[2] + fr.u[2]]), pv = cam.project([o[0] + fr.v[0], o[1] + fr.v[1], o[2] + fr.v[2]]);
    return `matrix(${n2(pu[0] - p0[0])} ${n2(pu[1] - p0[1])} ${n2(pv[0] - p0[0])} ${n2(pv[1] - p0[1])} ${n2(p0[0])} ${n2(p0[1])})`;
  }
  function decoInner(d, W, H, st) {
    const col = d.color || "#fff", op = d.op == null ? 1 : d.op;
    switch (d.type) {
      case "stripes": {
        const gap = d.gap || 8, sw = d.w || gap / 2, vert = d.dir === "v";
        let s = "";
        for (let t = d.off == null ? gap / 2 : d.off; t < (vert ? W : H); t += gap) s += vert ? `<rect x="${n2(t - sw / 2)}" y="0" width="${n2(sw)}" height="${n2(H)}"/>` : `<rect x="0" y="${n2(t - sw / 2)}" width="${n2(W)}" height="${n2(sw)}"/>`;
        return `<g fill="${col}" fill-opacity="${op}">${s}</g>`;
      }
      case "gingham": {
        const gap = d.gap || 8;
        let s = "";
        for (let a = gap / 2; a < W; a += gap) s += `<rect x="${n2(a - gap / 4)}" y="0" width="${n2(gap / 2)}" height="${n2(H)}"/>`;
        for (let a = gap / 2; a < H; a += gap) s += `<rect x="0" y="${n2(a - gap / 4)}" width="${n2(W)}" height="${n2(gap / 2)}"/>`;
        return `<g fill="${col}" fill-opacity="${op * 0.5}">${s}</g>`;
      }
      case "dots": {
        const gap = d.gap || 8, r = d.r || 1.2;
        let s = "";
        for (let a = gap / 2, row = 0; a < H; a += gap, row++) for (let b = gap / 2 + (row % 2 ? gap / 2 : 0); b < W; b += gap) s += `<circle cx="${n2(b)}" cy="${n2(a)}" r="${r}"/>`;
        return `<g fill="${col}" fill-opacity="${op}">${s}</g>`;
      }
      case "stitch": {
        const i = d.inset || 3;
        return `<rect x="${i}" y="${i}" width="${n2(Math.max(1, W - 2 * i))}" height="${n2(Math.max(1, H - 2 * i))}" rx="${d.rx || 3}" fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.7}" stroke-dasharray="${d.dash || "2.6 2.2"}" stroke-linecap="round"/>`;
      }
      case "grain": {
        let seed = d.seed || 7, s = "";
        const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
        for (let k = 0; k < (d.n || 6); k++) { const y = (H * (k + 0.5)) / (d.n || 6) + (rnd() - 0.5) * 3, x0 = rnd() * W * 0.3, x1 = W - rnd() * W * 0.3; s += `<path d="M${n2(x0)} ${n2(y)}Q${n2((x0 + x1) / 2)} ${n2(y + (rnd() - 0.5) * 3)} ${n2(x1)} ${n2(y)}"/>`; }
        return `<g fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.6}" stroke-linecap="round">${s}</g>`;
      }
      case "scallop": {
        const gap = d.gap || 9, r = gap / 2.2, y = d.y == null ? H : d.y;
        let s = "";
        for (let a = gap / 2; a < W; a += gap) s += `<path d="M${n2(a - r)} ${n2(y - r)}a${n2(r)} ${n2(r)} 0 0 0 ${n2(2 * r)} 0"/>`;
        return `<g fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.9}" stroke-linecap="round">${s}</g>`;
      }
      case "rect": return `<rect x="${n2(d.x)}" y="${n2(d.y)}" width="${n2(d.w)}" height="${n2(d.h)}" rx="${d.rx || 0}" fill="${col}" fill-opacity="${op}"${d.stroke ? ` stroke="${d.stroke}" stroke-width="${d.sw || 0.6}"` : ""}/>`;
      case "circle": return `<circle cx="${n2(d.x)}" cy="${n2(d.y)}" r="${d.r}" fill="${col}" fill-opacity="${op}"${d.stroke ? ` stroke="${d.stroke}" stroke-width="${d.sw || 0.6}"` : ""}/>`;
      case "star": {
        const r = d.r || 2, pts = [];
        for (let k = 0; k < 10; k++) { const a = (k * Math.PI) / 5 - Math.PI / 2, rr = k % 2 ? r * 0.45 : r; pts.push(`${n2(d.x + rr * Math.cos(a))},${n2(d.y + rr * Math.sin(a))}`); }
        return `<polygon points="${pts.join(" ")}" fill="${col}" fill-opacity="${op}"/>`;
      }
      case "raw": return d.svg;
      default: return "";
    }
  }
  /** 透视相机下的大面（桌面、地板）：花纹逐点投影，不能用一个仿射矩阵（会被拉成一团）。只支持 stripes / dots / grain，其它的不画。 */
  function exactDeco(d, fr, cam) {
    const at = (u, v) => [fr.o[0] + u * fr.u[0] + v * fr.v[0], fr.o[1] + u * fr.u[1] + v * fr.v[1], fr.o[2] + u * fr.u[2] + v * fr.v[2]];
    const col = d.color || "#fff", op = d.op == null ? 1 : d.op, W = fr.W, H = fr.H, f = P.CAM.f;
    if (d.type === "stripes") {
      const gap = d.gap || 8, sw = d.w || gap / 2, vert = d.dir === "v";
      let out = "";
      for (let t = d.off == null ? gap / 2 : d.off; t < (vert ? W : H); t += gap) {
        const q = vert ? [at(t - sw / 2, 0), at(t + sw / 2, 0), at(t + sw / 2, H), at(t - sw / 2, H)] : [at(0, t - sw / 2), at(W, t - sw / 2), at(W, t + sw / 2), at(0, t + sw / 2)];
        const c = cam.clip(q);
        if (c.length >= 3) out += poly(c.map((p) => cam.project(p)), col, ` fill-opacity="${op}"`);
      }
      return out;
    }
    if (d.type === "dots") {
      const gap = d.gap || 8, r = d.r || 1.2;
      let out = "";
      for (let a = gap / 2, row = 0; a < H; a += gap, row++) for (let b = gap / 2 + (row % 2 ? gap / 2 : 0); b < W; b += gap) {
        const c = at(b, a), dep = cam.depth(c);
        if (dep < 30) continue;
        const p = cam.project(c), pu = cam.project(at(b + r, a)), pu2 = cam.project(at(b - r, a)), pv = cam.project(at(b, a + r)), pv2 = cam.project(at(b, a - r));
        out += `<ellipse cx="${n2(p[0])}" cy="${n2(p[1])}" rx="${n2(Math.max(0.3, Math.hypot(pu[0] - pu2[0], pu[1] - pu2[1]) / 2))}" ry="${n2(Math.max(0.3, Math.hypot(pv[0] - pv2[0], pv[1] - pv2[1]) / 2))}"/>`;
      }
      void f;
      return `<g fill="${col}" fill-opacity="${op}">${out}</g>`;
    }
    if (d.type === "grain") {
      let seed = d.seed || 7, out = "";
      const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
      for (let k = 0; k < (d.n || 6); k++) {
        const y = (H * (k + 0.5)) / (d.n || 6) + (rnd() - 0.5) * 3, x0 = rnd() * W * 0.3, x1 = W - rnd() * W * 0.3, wob = (rnd() - 0.5) * 3, pts = [];
        for (let i = 0; i <= 8; i++) { const t = i / 8, c = at(x0 + (x1 - x0) * t, y + Math.sin(t * Math.PI) * wob); if (cam.depth(c) >= 30) pts.push(cam.project(c)); }
        if (pts.length >= 2) out += `M${pts.map((p) => `${n2(p[0])} ${n2(p[1])}`).join("L")}`;
      }
      return `<g fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.6}" stroke-linecap="round"><path d="${out}"/></g>`;
    }
    return "";
  }
  function decoSvg(m, vis, ctx) {
    if (!m.deco || !m.deco.length) return "";
    const { cam, st } = ctx;
    let out = "";
    for (const d of m.deco) {
      if (d.min && st.detail < d.min) continue;
      const want = d.frame ? d.frame.n : NORMAL[d.face || "top"];
      let best = null;
      for (const v of vis) { const s = P.dot(v.f.nrm, want); if (s > 0.6 && (!best || s > best.s)) best = { v, s }; }
      if (!best) continue;
      const fr = d.frame || (best.v.fr || (best.v.fr = frameOfFace(best.v.f)));
      if (!fr) continue;
      const id = `r3d${++UID}`, xs = best.v.pts.map((p) => p[0]), ys = best.v.pts.map((p) => p[1]);
      const big = cam.kind === "persp" && Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) > 500;      // 透视里的大面：逐点投影
      const inner = big ? exactDeco(d, fr, cam) : `<g transform="${matrixOf(cam, fr)}">${decoInner(d, fr.W, fr.H, st)}</g>`;
      if (!inner) continue;
      out += `<clipPath id="${id}"><polygon points="${best.v.pts.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}"/></clipPath><g clip-path="url(#${id})">${inner}</g>`;
    }
    return out;
  }

  /** 各个形体上的装饰（按风格的 detail 等级）。 */
  function decorate(m, st, pal) {
    const n = m.name, d = [], det = st.detail, ink = st.ink;
    const knob = (face, x, y) => ({ type: "circle", face, x, y, r: 1.8, color: pal.knob, stroke: ink, sw: 0.5 });
    switch (m.g) {
      case "floor": if (n === "floor") d.push({ type: "stripes", face: "top", dir: "v", gap: 50, color: pal.floorLine, op: 0.6, w: 1, off: 8 }, { type: "stripes", face: "top", dir: "h", gap: 50, color: pal.floorLine, op: 0.6, w: 1, off: 8 }); break;
      case "bed":
        if (n === "blanket") {
          const pat = det >= 3 ? "stitch" : det === 2 ? "dots" : "stripes";
          if (pat === "stripes") d.push({ type: "stripes", face: "top", dir: "v", gap: 18, color: pal.blanketB, op: 0.95, w: 8 });
          else if (pat === "dots") d.push({ type: "dots", face: "top", gap: 14, r: 2.2, color: pal.blanketB, op: 0.95 }, { type: "star", face: "top", x: 40, y: 34, r: 4.4, color: "#fff", op: 0.9, min: 2 }, { type: "star", face: "top", x: 86, y: 76, r: 3.4, color: "#fff", op: 0.9, min: 2 });
          else d.push({ type: "stripes", face: "top", dir: "v", gap: 22, color: pal.blanketB, op: 0.8, w: 9 }, { type: "stitch", face: "top", inset: 4, color: "#fff", op: 0.85, sw: 0.9 });
          d.push({ type: "scallop", face: "S", y: 6, gap: 11, color: tone(pal.blanket, -0.14, 6), op: 0.8, min: 1 });
        } else if (n === "mattress") d.push({ type: "scallop", face: "S", y: 20, gap: 12, color: tone(pal.mattress, -0.12, 6), op: 0.7, min: 3 });
        else if (/^pillow/.test(n) && det >= 3) d.push({ type: "stitch", face: "top", inset: 3.5, color: "#9db3d4", op: 0.7 });
        else if (n === "frame" && det >= 3) d.push({ type: "stripes", face: "S", dir: "v", gap: 24, color: tone(pal.bedFrame, -0.1, 6), op: 0.4, w: 1 });
        break;
      case "desk": if (/^top/.test(n)) d.push({ type: "grain", face: "top", color: tone(pal.deskTop, det >= 3 ? -0.13 : -0.1, 6), op: det >= 3 ? 0.7 : 0.55, n: 7, seed: n === "top-lower" ? 11 : 5 }); break;
      case "foodtable": if (n === "top") d.push({ type: "grain", face: "top", color: tone(pal.deskTop, -0.1, 6), op: 0.5, n: 4, seed: 17 }); break;
      case "sofa": if (n === "seat" && det >= 2) d.push({ type: "stitch", face: "top", inset: 5, color: "#fff", op: 0.6, rx: 5 }); break;
      case "wardrobe":
        if (/^door/.test(n)) d.push({ type: "stitch", face: "S", inset: 6, color: tone(pal.wardDoor, -0.12, 6), op: 0.9, sw: 0.9, dash: "100 0", rx: 3, min: 2 }, knob("S", n === "door-l" ? 54 : 6, 90));
        break;
      case "nightstand": if (/^drawer/.test(n)) d.push(knob("E", 18, 7.5)); break;
      case "girlchair": if (n === "seat" && det >= 2) d.push({ type: "stitch", face: "top", inset: 4, color: "#fff", op: 0.7 }); break;
      case "props":
        if (/^notebook-page/.test(n)) d.push({ type: "stripes", face: "top", dir: "h", gap: 2.6, off: 3, w: 0.18, color: pal.paperLine, op: 0.9 });
        if (n === "notebook-closed" && det >= 2) d.push({ type: "stitch", face: "top", inset: 2, color: "#fff", op: 0.7, rx: 1.5 });
        break;
      default: break;
    }
    return d;
  }

  /* ───────────────── 画一个形体（圆角轮廓 + 面 + 描边；两种笔触：toon 贴纸 / wash 水彩） ───────────────── */
  /** 贴图（杂物区的奶茶 / 零食）：立着的按竖直高度定大小、底边贴桌面；平放的按桌面上的宽度定大小、中心对准位置。 */
  function drawSprite(m, ctx) {
    const { cam, st } = ctx, sp = m.sprite, base = [sp.x, sp.y, sp.z];
    if (cam.kind === "persp" && cam.depth(base) <= P.NEAR) return null;
    const p0 = cam.project(base), up = cam.project([sp.x, sp.y, sp.z + 10]), ppc = Math.hypot(up[0] - p0[0], up[1] - p0[1]) / 10;
    let w, h, x, y;
    if (sp.mode === "stand") { h = sp.hcm * ppc * 1.04; w = (h * sp.w) / sp.h; x = p0[0] - w / 2; y = p0[1] - h * 0.985; }
    else { w = sp.wcm * ppc; h = (w * sp.h) / sp.w; x = p0[0] - w / 2; y = p0[1] - h * 0.56; }
    const pts = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
    const look = st.mode === "wash" ? ' style="filter:sepia(.18) saturate(.9)"' : "";
    return { svg: `<image href="${sp.href}" x="${n2(x)}" y="${n2(y)}" width="${n2(w)}" height="${n2(h)}" preserveAspectRatio="none"${look}/>`, halo: "", pts, sb: [x, y, x + w, y + h] };
  }

  function drawMesh(m, ctx) {
    if (m.sprite) return drawSprite(m, ctx);
    const { cam, st, lineK, radK } = ctx, mode = st.mode || "toon", faces = prep(m), vis = [];
    const amp = st.jitter ? st.jitter * lineK : 0, J = (p) => (amp ? X.jit(p, amp) : p);          // 水彩的线条略抖
    for (const f of faces) {
      if (P.dot(f.nrm, cam.toCam(f.fc)) <= 0) continue;
      const cl = cam.clip(f.vs);
      if (cl.length < 3) continue;
      vis.push({ f, pts: cl.map((v) => J(cam.project(v))), k: P.dot(f.nrm, cam.light) });
    }
    if (!vis.length) return null;
    const all = [];
    vis.forEach((v) => v.pts.forEach((p) => all.push(p)));
    const sil = hull(all);
    if (sil.length < 3) return null;
    const base = m.col, me = minEdge(sil), shell = SHELL.has(m.g);
    const rad = m.sharp ? 0 : m.pill ? me * 0.49 : Math.min((m.rad == null ? st.radMax * 0.7 : m.rad) * radK, me * 0.42);
    const path = rad > 0.2 ? roundedPath(sil, rad) : polyPath(sil), id = `r3c${++UID}`;
    const fill = (k) => faceColor(base, k, st);
    let svg = `<clipPath id="${id}"><path d="${path}"/></clipPath><g clip-path="url(#${id})">`;
    vis.forEach((v) => { svg += poly(v.pts, fill(v.k)); });
    if (mode === "wash") {                                                              // 水彩：大面上浓淡不均；颜料在边缘积了一圈深色
      vis.forEach((v) => {
        const xs = v.pts.map((p) => p[0]), ys = v.pts.map((p) => p[1]);
        if (Math.max(...xs) - Math.min(...xs) < 160 || Math.max(...ys) - Math.min(...ys) < 90 || Math.max(...xs) - Math.min(...xs) > 6000) return;
        const cid = `${id}b${++UID}`;
        svg += `<clipPath id="${cid}"><polygon points="${v.pts.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}"/></clipPath><g clip-path="url(#${cid})">${X.washBlobs(v.pts, fill(v.k))}</g>`;
      });
      if (!shell) {
        let pool = "";
        vis.forEach((v) => { pool += polyPath(v.pts); });
        svg += `<path d="${pool}" fill="none" stroke="${tone(base, -0.2, 0)}" stroke-opacity=".5" stroke-width="${n2(3.6 * lineK)}" stroke-linejoin="round"/>`;
      }
    }
    const hatchOn = st.hatch && !m.noHatch && me > 6 * radK / 3;
    if (hatchOn) {                                                                     // 背光的面画斜线排线（风格里 hatch 打开时）
      vis.forEach((v) => {
        if (v.k > 0.08) return;
        const xs = v.pts.map((p) => p[0]), ys = v.pts.map((p) => p[1]), x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys), h = y1 - y0;
        if (h < 5 || x1 - x0 < 5 || x1 - x0 > 4000) return;
        const dense = 5, gap = dense * lineK, cid = `${id}h${++UID}`;
        let hs = "";
        for (let t = x0 - h; t < x1; t += gap) hs += `M${n2(t)} ${n2(y1)}L${n2(t + h)} ${n2(y0)}`;
        svg += `<clipPath id="${cid}"><polygon points="${v.pts.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}"/></clipPath><g clip-path="url(#${cid})"><path d="${hs}" fill="none" stroke="${tone(base, -0.2, st.tone.cool)}" stroke-opacity=".45" stroke-width="${n2(0.7 * lineK)}"/></g>`;
      });
    }
    svg += decoSvg(m, vis, ctx);
    if (m.sky) svg += X.skySvg(m.sky, cam, ctx);                                      // 窗外那块天空板：渐变 + 太阳 / 月亮 / 星星 / 云 / 雨 / 雪
    if (st.rim && !m.noRim) {                                                       // 顶面上沿的一条高光
      const top = vis.find((v) => v.f.nrm[2] > 0.9 && v.pts.length >= 3);
      if (top) {
        const pts = top.pts, n = pts.length, ix = (f) => pts.reduce((b, p, i) => (f(p, pts[b]) ? i : b), 0);
        const iL = ix((p, q) => p[0] < q[0]), iT = ix((p, q) => p[1] < q[1]), iR = ix((p, q) => p[0] > q[0]);
        const walk = (dir) => { const out = [iL]; let i = iL; for (let s = 0; s < n && i !== iR; s++) { i = (i + dir + n) % n; out.push(i); } return out; };
        const a = walk(1), b = walk(-1), seq = (a.includes(iT) ? a : b).map((i) => pts[i]), k = 1.4 * lineK;
        if (seq.length >= 2) svg += `<path d="M${seq.map((p) => `${n2(p[0] + k * 0.6)} ${n2(p[1] + k)}`).join("L")}" fill="none" stroke="#fff" stroke-opacity="${st.rim}" stroke-width="${n2(1.2 * lineK)}" stroke-linecap="round" stroke-linejoin="round"/>`;
      }
    }
    svg += `</g>`;
    const thin = m.thin || 1, iw = st.innerW * lineK * thin, ow = st.inkW * lineK * thin;
    let inner = "";
    vis.forEach((v) => { if (m.smooth && Math.abs(v.f.nrm[2]) < 0.9) return; inner += polyPath(v.pts); });
    const line = mode === "wash" ? mix(base, "#5e4636", 0.62) : st.ink;               // 水彩的线是同色系的深色，不是黑
    const washA = mode === "wash" ? ' stroke-opacity=".85"' : "", washB = mode === "wash" ? ' stroke-opacity=".6"' : "";
    svg += `<g fill="none" stroke="${line}" stroke-linecap="round" stroke-linejoin="round"${washA}>${inner ? `<path d="${inner}" stroke-width="${n2(iw)}"${washB}/>` : ""}<path d="${path}" stroke-width="${n2(ow)}"/></g>`;
    if (mode === "wash" && !shell && me > 8) {                                        // 水彩：轮廓再描一遍，错开一点（手绘的感觉）
      const sil2 = sil.map((p) => X.jit(p, amp * 1.4, 3));
      svg += `<path d="${rad > 0.2 ? roundedPath(sil2, rad) : polyPath(sil2)}" fill="none" stroke="${line}" stroke-opacity=".3" stroke-width="${n2(ow * 0.7)}" stroke-linejoin="round"/>`;
    }
    const halo = mode === "toon" && st.sticker && !m.noSticker ? `<path d="${path}" fill="#fff" stroke="#fff" stroke-width="${n2(st.sticker * lineK)}" stroke-linejoin="round"/>` : "";
    const xs = sil.map((p) => p[0]), ys = sil.map((p) => p[1]);
    return { svg, halo, pts: sil, sb: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] };
  }

  /** 贴纸白边按「一件家具」垫一圈（桌上的东西各自成一件）。 */
  function haloKey(m) {
    if (m.g === "props") {
      const n = m.name;
      if (/^monitor/.test(n)) return "props:monitor";
      if (/^mug/.test(n)) return "props:mug";
      if (/^(west-book|stack|sketchbook|notebook-closed)/.test(n)) return "props:pile";
      if (/^notebook/.test(n)) return "props:notebook";
      if (n === "red-machine" || n === "diffuser") return "props:machine";
      return "props:" + n;
    }
    if (m.g === "things" || m.g === "clutter") return m.g + ":" + m.itemId;
    return m.g;
  }

  /* ───────────────── 书：站在书架上 / 躺在地上 / 站在篮子里 ───────────────── */
  const unit2 = (a) => { const l = Math.hypot(a[0], a[1]) || 1; return [a[0] / l, a[1] / l]; };
  /** 一本书（长方体）：中心 (cx,cy)，spine 朝 f（单位向量），厚度 w、书高 h、封面宽（深）cw；站着（z0 是底面）或躺着（z0 是底面，厚度竖直）。 */
  function bookMesh(b, pose, cx, cy, z0, f, g) {
    const l = bookDefaults(b.look), r = [f[1], -f[0]];
    let m, frames;
    if (pose === "stand") {
      m = P.rbox(g, `book-${b.id}`, 0.6, cx, cy, l.w / 2, l.cw / 2, f, z0, z0 + l.h);
      const hx = l.w / 2, hy = l.cw / 2, z1 = z0 + l.h, o = (a, c) => [cx + a[0], cy + a[1], z1];
      frames = {
        spine: { o: [cx + f[0] * hy + r[0] * hx, cy + f[1] * hy + r[1] * hx, z1], u: [-f[1], f[0], 0], v: [0, 0, -1], W: l.w, H: l.h, n: [f[0], f[1], 0] },
        cover: { o: [cx - r[0] * hx + f[0] * hy, cy - r[1] * hx + f[1] * hy, z1], u: [-f[0], -f[1], 0], v: [0, 0, -1], W: l.cw, H: l.h, n: [-r[0], -r[1], 0] },
        top: { o: [cx + r[0] * hx - f[0] * hy, cy + r[1] * hx - f[1] * hy, z1], u: [-r[0], -r[1], 0], v: [f[0], f[1], 0], W: l.w, H: l.cw, n: [0, 0, 1] },
      };
      void o;
    } else {
      m = P.rbox(g, `book-${b.id}`, 0.6, cx, cy, l.h / 2, l.cw / 2, f, z0, z0 + l.w);
      const hx = l.h / 2, hy = l.cw / 2, z1 = z0 + l.w;
      frames = {
        spine: { o: [cx + f[0] * hy + r[0] * hx, cy + f[1] * hy + r[1] * hx, z1], u: [-f[1], f[0], 0], v: [0, 0, -1], W: l.h, H: l.w, n: [f[0], f[1], 0] },
        top: { o: [cx + r[0] * hx - f[0] * hy, cy + r[1] * hx - f[1] * hy, z1], u: [-r[0], -r[1], 0], v: [f[0], f[1], 0], W: l.h, H: l.cw, n: [0, 0, 1] },
      };
    }
    Object.assign(m, { col: l.main, book: b, pose, sharp: true, noRim: true, noHatch: true, thin: 0.38, noSticker: g !== "pile" });
    const title = b.title;
    m.deco = pose === "stand"
      ? [{ type: "raw", frame: frames.spine, svg: spineSvg(l, title) }, { type: "raw", frame: frames.cover, svg: coverSvg(l, title, b.author) },
         { type: "raw", frame: frames.top, svg: `<rect x="0" y="0" width="${n2(l.w)}" height="${n2(l.cw)}" fill="${l.accent}" fill-opacity=".5"/><rect x="${n2(l.w * 0.16)}" y="${n2(l.cw * 0.03)}" width="${n2(l.w * 0.8)}" height="${n2(l.cw * 0.94)}" fill="#f7f3e8"/>` }]
      : [{ type: "raw", frame: frames.spine, svg: spineFlatSvg(l, title) },
         { type: "raw", frame: frames.top, svg: `<g transform="translate(0 ${n2(l.cw)}) rotate(-90)">${coverSvg(l, title, b.author)}</g>` }];
    return m;
  }

  /** 书架上的书：4 层（中间两层先放，再上层、最后最下层），每层从南往北排（站在书架前面看，从左往右），书脊朝西（朝外）。 */
  function layoutShelfBooks(books, O) {
    const B = L.BOOKCASE, rowH = (B.h - B.base - B.top - (B.rows - 1) * B.bt) / B.rows;
    const cellZ = (i) => B.base + i * (rowH + B.bt);
    const yA = B.y0 + B.side, yB = B.y1 - B.side, front = B.x1 - 1.5, backX = B.x0 + B.back;
    const rows = [2, 1, 3, 0];
    let ri = 0, y = yA + 1;
    for (const b of books) {
      const l = bookDefaults(b.look);
      while (ri < rows.length && (y + l.w > yB - 0.5 || l.h > rowH - 0.5)) { ri++; y = yA + 1; }
      if (ri >= rows.length) break;
      const depth = Math.min(l.cw, front - backX - 1), cx = front - depth / 2;
      O.push(bookMesh(Object.assign({}, b, { look: Object.assign({}, b.look, { cw: depth }) }), "stand", cx, y + l.w / 2, cellZ(rows[ri]), [1, 0], "books"));
      y += l.w + 0.25;
    }
  }
  /** 地上的书：书桌（贴北墙那条）下面，几摞横着叠，略显凌乱（每本有个小偏移和小角度）。 */
  const FLOOR_PILES = [[-190, 58], [-150, 60], [-110, 52], [-62, 60], [-24, 56], [-165, 78]];
  function layoutFloorBooks(books, O) {
    let seed = 20261024;
    const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    const per = Math.max(1, Math.ceil(books.length / Math.min(FLOOR_PILES.length, Math.ceil(books.length / 5))));
    let pi = 0, z = 0, count = 0;
    books.forEach((b) => {
      if (count >= per) { pi = Math.min(pi + 1, FLOOR_PILES.length - 1); z = 0; count = 0; }
      const l = bookDefaults(b.look), p = FLOOR_PILES[pi], jx = (rnd() - 0.5) * 6, jy = (rnd() - 0.5) * 5, yaw = ((rnd() - 0.5) * 34 * Math.PI) / 180;
      O.push(bookMesh(b, "lie", p[0] + jx, p[1] + jy, z, [Math.sin(yaw), Math.cos(yaw)], "pile"));
      z += l.w;
      count++;
    });
  }
  /** 收纳篮（落地灯和床脚之间的地上，房间视角里看得到）+ 里面露出几本书。 */
  const BASKET = { x0: 100, x1: 144, y0: 236, y1: 274, h: 28 };
  function layoutBasket(books, pal, O) {
    const { x0, x1, y0, y1, h } = BASKET;
    const mk = (name, ax0, ax1, ay0, ay1, z0, z1, col, extra) => { const m = P.frustum("basket", name, 0.6, ax0, ax1, ay0, ay1, z0, z1); return Object.assign(m, { col, rad: 6 }, extra); };
    const body = mk("basket", x0, x1, y0, y1, 0, h, pal.basket, { deco: [{ type: "stripes", face: "S", dir: "h", gap: 5, color: pal.basketB, op: 0.7, w: 1.4 }, { type: "stripes", face: "E", dir: "h", gap: 5, color: pal.basketB, op: 0.7, w: 1.4 }, { type: "stripes", face: "top", dir: "v", gap: 5, color: pal.basketB, op: 0.5, w: 1.2 }] });
    const rim = mk("rim", x0 - 1.5, x1 + 1.5, y0 - 1.5, y1 + 1.5, h - 1, h + 3, pal.basketB, { rad: 4, noRim: true });
    O.push(body, rim);
    books.slice(0, 5).forEach((b, i) => O.push(bookMesh(b, "stand", x1 - 8 - i * 7.5, (y0 + y1) / 2 + 2, h + 2, [0, 1], "basket")));
  }

  /* ───────────────── 家具上的小物件（按区域名摆在固定的位置） ───────────────── */
  const SLOTS = (() => {
    const B = L.BOOKCASE, SB = L.SIDEBOARD, SF = L.SOFA, BD = L.BED, NS = L.NIGHTSTAND, W = L.WARDROBE, D = L.DESK;
    const mid = (a, b) => (a + b) / 2;
    return {
      "窗边": [[65, mid(SB.y0, SB.y1), SB.h], [95, mid(SB.y0, SB.y1), SB.h], [125, mid(SB.y0, SB.y1), SB.h]],
      "沙发": [[-190, SF.y1 - 6, SF.back], [-120, SF.y1 - 6, SF.back], [-50, SF.y1 - 6, SF.back]],               // 沙发靠背的顶上（座面留给她坐 / 躺）
      "书桌": [[-200, 52, D], [-176, 50, D], [-152, 66, D], [-190, 142, D], [-175, 166, D], [-196, 166, D]],
      "床": [[BD.x0 + 34, 275, 52], [BD.x0 + 34, 325, 52], [BD.x0 + 84, 300, 52], [BD.x0 + 110, 262, 52]],
      "床头柜": [[mid(NS.x0, NS.x1), mid(NS.y0, NS.y1), NS.top]],
      "衣柜": [[W.x0 + 22, mid(W.y0, W.y1), W.top], [W.x0 + 62, mid(W.y0, W.y1), W.top], [W.x0 + 102, mid(W.y0, W.y1), W.top]],
      "书架": [0, 1, 2, 3].map((i) => [mid(B.x0, B.x1), B.y0 + 18 + i * 24, B.h]),
    };
  })();
  /** 家具的组 → 区域名（点击、悬停、标签都按它）。 */
  const AREA_OF = { bed: "床", nightstand: "床头柜", desk: "书桌", props: "书桌", hutch: "书桌", girlchair: "书桌", wardrobe: "衣柜", bookcase: "书架", sofa: "沙发", corner: "窗边", clutter: "杂物区", foodtable: "食物桌" };

  const PROP_DEFAULT = { plant: { shape: "plant", color: "#8fb7a8", accent: "#6f9a8d", pot: "#e8d9c4", size: 1 }, bear: { shape: "plush", color: "#cdb7a8", accent: "#a88f80", size: 1 },
    book: { shape: "stack", color: "#c8d4e8", accent: "#8fa5c8", size: 1 }, dress: { shape: "cloth", color: "#d8c5d2", accent: "#b89fb0", size: 1 }, game: { shape: "device", color: "#c8d4e8", accent: "#8fa5c8", size: 1 },
    lamp: { shape: "lamp", color: "#f0e2b8", accent: "#c9b078", size: 1 }, gift: { shape: "box", color: "#d6c3df", accent: "#b59cc4", size: 1 } };
  function propMeshes(item, sx, sy, sz) {
    const look = Object.assign({}, PROP_DEFAULT[item.icon] || PROP_DEFAULT.gift, item.look || {}), k = clamp(+look.size || 1, 0.6, 1.5), g = "things";
    const col = look.color, acc = look.accent, O = [], id = `it-${item.id}`;
    const add = (m, c, extra) => { O.push(Object.assign(m, { col: c, item, itemId: item.id, noRim: true, noHatch: true, rad: 3, thin: 0.8 }, extra || {})); };
    const box = (name, x, y, z, w, d, h, c, extra) => add(P.frustum(g, `${id}-${name}`, 0.5, x, x + w, y, y + d, z, z + h), c, extra);
    const cyl = (name, cx, cy, z, r, h, c) => add(P.cyl(g, `${id}-${name}`, 0.5, cx, cy, r, r, z, z + h, 10), c);
    const ball = (name, cx, cy, cz, r, c) => add(P.ball(g, `${id}-${name}`, 0.5, cx, cy, cz, r, 8), c);
    switch (look.shape) {
      case "plant": cyl("pot", sx, sy, sz, 5 * k, 7 * k, look.pot || "#e8d9c4"); ball("leaf-1", sx - 2.5 * k, sy + 0.5, sz + 12 * k, 5.2 * k, col); ball("leaf-2", sx + 3 * k, sy - 1, sz + 14.5 * k, 4.6 * k, acc); ball("leaf-3", sx, sy + 1, sz + 18 * k, 3.8 * k, col); break;
      case "plush": ball("body", sx, sy, sz + 5 * k, 6.2 * k, col); ball("head", sx, sy, sz + 12.5 * k, 5.2 * k, col); ball("ear-l", sx - 3.6 * k, sy + 3.4 * k, sz + 17 * k, 2 * k, acc); ball("ear-r", sx + 3.6 * k, sy - 3.4 * k, sz + 17 * k, 2 * k, acc); break;
      case "stack": box("b1", sx - 6 * k, sy - 5 * k, sz, 12 * k, 10 * k, 3 * k, col); box("b2", sx - 5 * k, sy - 4.5 * k, sz + 3 * k, 10 * k, 9 * k, 2.6 * k, acc); box("b3", sx - 6.5 * k, sy - 5 * k, sz + 5.6 * k, 12.5 * k, 10 * k, 2.8 * k, col); break;
      case "cloth": box("c1", sx - 7 * k, sy - 5 * k, sz, 14 * k, 10 * k, 3 * k, col, { deco: [{ type: "stripes", face: "top", dir: "v", gap: 3.2, color: acc, op: 0.5, w: 1.1 }] }); box("c2", sx - 6 * k, sy - 4.6 * k, sz + 3 * k, 12 * k, 9 * k, 3 * k, acc); break;
      case "device": box("d", sx - 6 * k, sy - 4 * k, sz, 12 * k, 8 * k, 3.2 * k, col, { deco: [{ type: "rect", face: "top", x: 2 * k, y: 1.6 * k, w: 8 * k, h: 4.8 * k, rx: 1, color: acc }, { type: "circle", face: "top", x: 3.2 * k, y: 6.9 * k, r: 0.7, color: acc }] }); break;
      case "lamp": cyl("base", sx, sy, sz, 3.6 * k, 2 * k, acc); cyl("stem", sx, sy, sz + 2 * k, 0.9 * k, 10 * k, acc); add(P.cyl(g, `${id}-shade`, 0.5, sx, sy, 4.8 * k, 4.8 * k, sz + 12 * k, sz + 17.5 * k, 10, 0.85), col); break;
      default: box("box", sx - 5 * k, sy - 5 * k, sz, 10 * k, 10 * k, 8 * k, col, { deco: [{ type: "rect", face: "S", x: 4.4 * k, y: 0, w: 1.2 * k, h: 8 * k, color: acc }, { type: "rect", face: "top", x: 0, y: 4.4 * k, w: 10 * k, h: 1.2 * k, color: acc }] }); box("lid", sx - 5.6 * k, sy - 5.6 * k, sz + 8 * k, 11.2 * k, 11.2 * k, 2.4 * k, acc); break;
    }
    return O;
  }

  /* ───────────────── 场景：灰模 + 书 / 篮子 / 小物件 ───────────────── */
  function buildScene(opts, st, pal, info) {
    const O = S3.buildScene({ state: S3.STATES[opts.state] ? opts.state : S3.DEFAULT_STATE });      // 不认识的状态当默认的（A：笔记本合上）
    O.forEach((m) => { m.col = partColor(m, pal); Object.assign(m, flagsOf(m, st)); m.deco = decorate(m, st, pal); if (info && m.g === "outdoor" && m.name === "sky") m.sky = info; });
    const bk = opts.books || {}, extra = [];
    if (opts.clutter && opts.clutter.length) X.clutterMeshes(opts.clutter, pal, opts.sprites).forEach((m) => { m.col = st.mode === "wash" ? mix(m.col, "#fbf3e4", 0.18) : m.col; extra.push(m); });
    if (bk.shelf && bk.shelf.length) layoutShelfBooks(bk.shelf, extra);
    if (bk.floor && bk.floor.length) layoutFloorBooks(bk.floor, extra);
    if (opts.basket) layoutBasket(bk.storage || [], pal, extra);
    if (opts.items && opts.items.length) {
      const used = {};
      opts.items.forEach((it) => {
        const list = SLOTS[it.location], i = used[it.location] || 0;
        if (!list || i >= list.length) return;
        used[it.location] = i + 1;
        propMeshes(it, ...list[i]).forEach((p) => extra.push(p));
      });
    }
    return O.concat(extra);
  }

  /* ───────────────── 相机 / 视角 ───────────────── */
  function makeView(kind, opts) {
    if (kind === "desk") {
      const cam = P.deskCamera({ yaw: opts.yaw == null ? S3.DESK_YAW : opts.yaw, x: opts.camX == null ? S3.DESK_X : opts.camX });
      return { kind, cam, lineK: 1.25, radK: 3, hide: HIDE.desk, fixed: P.VIEW };
    }
    const sc = opts.scale || 1.6;
    return { kind: "room", cam: P.isoCamera({ westX: R.xw, northY: R.yn, scale: sc }), lineK: sc / 1.6, radK: sc / 1.6, hide: HIDE.room, sc };
  }

  /** 地面软影子：家具的投影（光从画面左上，影子偏右下），整组一个半透明块。 */
  const SHADOW_GROUPS = ["bed", "nightstand", "desk", "bookcase", "sofa", "corner", "wardrobe", "pile", "basket", "girlchair", "foodtable"];
  function shadowEntry(scene, ctx, pal) {
    const { cam } = ctx, l = cam.light, sl = Math.hypot(l[0], l[1]) || 1, dx = (-l[0] / sl) * 7, dy = (-l[1] / sl) * 7, polys = [];
    scene.forEach((o) => {
      if (!SHADOW_GROUPS.includes(o.g)) return;
      if ((o.g === "pile" || o.g === "basket") && o.bb[4] > 1) return;
      if (o.g === "girlchair" && o.name !== "base") return;
      if (o.g === "desk" && !/^top/.test(o.name)) return;
      const fp = o.fp || [[o.bb[0], o.bb[2]], [o.bb[1], o.bb[2]], [o.bb[1], o.bb[3]], [o.bb[0], o.bb[3]]], ring = o.smooth ? o.verts.slice(0, o.verts.length / 2).map((v) => [v[0], v[1]]) : fp;
      const q = cam.clip(ring.map((c) => [c[0] + dx, c[1] + dy, 0]));
      if (q.length < 3) return;
      polys.push(`<polygon points="${q.map((c) => { const p = cam.project(c); return `${n2(p[0])},${n2(p[1])}`; }).join(" ")}"/>`);
    });
    if (!polys.length) return null;
    return { o: { name: "shadow", g: "shadow", shadow: true }, b: null, svg: `<g opacity="${ctx.st.shadow == null ? 0.16 : ctx.st.shadow}"><g fill="${pal.shadow || ctx.st.ink}" stroke="${pal.shadow || ctx.st.ink}" stroke-width="${n2(1.5 * ctx.lineK)}" stroke-linejoin="round">${polys.join("")}</g></g>` };
  }

  /* ───────────────── 画整个房间 ───────────────── */
  function render(opts, styleId, kind, scale) {
    opts = opts || {};
    const st = X.styleOf(styleId), info = W.resolve(opts.ambient, opts.weather), view = makeView(kind === "desk" ? "desk" : "room", Object.assign({ scale }, opts.camera || {}));
    const pal = info ? W.tintOutdoor(palOf(styleId), info, styleId === "A" ? "A" : "B") : palOf(styleId);
    const ctx = { cam: view.cam, st, lineK: view.lineK, radK: view.radK };
    // layered：书桌视角的窗外拆出去单独画（天空 / 远云 / 雨雪是 web/roomweather.js 的动画层，树和坡是单独一张 SVG），主画面在窗洞那里留空
    const layered = !!opts.layered && view.kind === "desk";
    const hide = (opts.hide || view.hide).slice(), scene = buildScene(opts, st, pal, layered ? null : info);
    if (layered) hide.push("outdoor");
    const shown = scene.filter((m) => !hide.includes(m.g) && (view.kind !== "desk" || m.verts.some((v) => view.cam.depth(v) > P.NEAR)));
    const sorted = P.order(shown, view.cam), order = [], halos = {}, hot = {}, shapes = [];
    let pts = [];
    sorted.forEach((m) => {
      const r = drawMesh(m, ctx);
      if (!r) return;
      if (r.halo && !SHELL.has(m.g)) {
        const key = haloKey(m);
        let h = halos[key];
        if (!h) { h = halos[key] = { entry: { o: { name: `halo-${key}`, g: m.g, halo: true }, svg: "", b: null }, parts: [] }; order.push(h.entry); }
        h.parts.push(r.halo);
      }
      order.push({ o: m, svg: r.svg, b: m.bb, sb: r.sb });
      pts = pts.concat(r.pts);
      if (info && view.kind === "room") shapes.push(polyPath(r.pts));
      if (!SHELL.has(m.g)) (hot[m.g] = hot[m.g] || []).push(...r.pts);
    });
    Object.values(halos).forEach((h) => { h.entry.svg = h.parts.join(""); });
    const sh = shadowEntry(scene.filter((m) => !hide.includes(m.g)), ctx, pal);
    if (sh) { const i = order.findIndex((e) => !e.o.halo && !SHELL.has(e.o.g)); order.splice(i < 0 ? order.length : i, 0, sh); }
    // 斜射的阳光落在地上 / 桌面上的亮斑：插在地板（和影子）/ 桌面后面，家具、桌上的东西画在它上面
    const panes = W.panesOf(scene.filter((m) => m.g === "wall" && /^mullion/.test(m.name)).map((m) => [m.bb[0], m.bb[1]]));
    if (info && info.sunA > 0.02) {
      const patch = (name, svg) => ({ o: { name, g: "light", shadow: true }, b: null, svg });
      const fp = W.sunPatchSvg(info, view.cam, panes, view.lineK, "floor");
      if (fp) { const i = order.findIndex((e) => !e.o.halo && !e.o.shadow && !SHELL.has(e.o.g)); order.splice(i < 0 ? order.length : i, 0, patch("sun-floor", fp)); }
      const dp = W.sunPatchSvg(info, view.cam, panes, view.lineK, "desk");
      if (dp) { let j = -1; order.forEach((e, k) => { if (e.o.g === "desk" && /^top/.test(e.o.name)) j = k; }); if (j >= 0) order.splice(j + 1, 0, patch("sun-desk", dp)); }
    }

    let vb;
    if (view.fixed) vb = [view.fixed.x, view.fixed.y, view.fixed.w, view.fixed.h];
    else {
      const pad = 14 * view.lineK, xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]), x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
      vb = [n2(x0), n2(y0), n2(Math.max(...xs) + pad - x0), n2(Math.max(...ys) + pad - y0)];
    }
    const cam = view.cam, before = P.makeBefore(cam);
    const hotspots = {};
    Object.keys(hot).forEach((g) => { hotspots[g] = hull(hot[g]); });
    // 窗：窗洞在画面里的四个角、被窗框分成的几格（光照、动画层都用）
    const hole = view.kind === "desk" ? W.holeOf(cam) : null;
    let outdoorSvg = "";
    if (layered) P.order(scene.filter((m) => m.g === "outdoor" && m.name !== "sky"), cam).forEach((m) => { const d = drawMesh(m, ctx); if (d) outdoorSvg += `<g data-n="${m.name}">${d.svg}</g>`; });
    const api = {
      view: view.kind, order, viewBox: vb, width: Math.ceil(vb[2]), height: Math.ceil(vb[3]), st, pal, scene, hotspots, cam, hide, bg: view.kind === "desk" ? pal.wallN : null, ambient: info,
      layered, hole, panes, outdoorSvg,
      overlay: W.lightSvg(info, cam, vb, st.mode, view.lineK, { shapes, hole, panes }),     // 室内光照：光色（雨天暗冷、夜里暗蓝）+ 斜射的阳光 + 灯 / 显示器的微光
      project: (x, y, z) => cam.project([x, y, z || 0]),
      /** 站在地上 (x,y) 的人，每 cm 身高在画面里是多少像素。 */
      pxPerCm: (x, y) => { const a = cam.project([x, y, 0]), b = cam.project([x, y, 100]); return Math.abs(a[1] - b[1]) / 100; },
      /** 面朝世界方向 (dx,dy) 的人在 (x,y) 处该用哪张图：side（mirror：往画面右边）/ front / back。 */
      faceOf: (dx, dy, x, y) => {
        if (cam.kind === "persp") {                                       // 透视：看她的朝向和「相机→她」那条视线的夹角（离相机越近、越靠边，屏幕上的位移越不能直接比）
          const rx = x - cam.pos[0], ry = y - cam.pos[1], c = (dx * rx + dy * ry) / ((Math.hypot(dx, dy) || 1) * (Math.hypot(rx, ry) || 1));
          if (c >= 0.574) return { face: "back", mirror: false };             // 背对相机（夹角 ≤ 55°）
          if (c <= -0.574) return { face: "front", mirror: false };
          return { face: "side", mirror: dx * cam.R[0] + dy * cam.R[1] > 0 };      // 朝画面右边走 → 侧面图（朝左）镜像
        }
        const l = Math.hypot(dx, dy) || 1, a = cam.project([x, y, 0]), b = cam.project([x + (dx / l) * 10, y + (dy / l) * 10, 0]), sx = b[0] - a[0], sy = b[1] - a[1];
        if (Math.abs(sx) > Math.abs(sy) * 0.9) return { face: "side", mirror: sx > 0 };
        return { face: sy > 0 ? "front" : "back", mirror: false };
      },
      /** 角色（一个包围盒 {bb:[x0,x1,y0,y1,z0,z1]}）该插在 order 的第几项之前。after：必须画在它们后面的形体名（"组/名字"）；
       *  ahead：必须画在她前面（盖住她）的形体名，比如坐在椅子上时房间视角里的椅背（挡住一部分头发）。 */
      insertIndex: (box, after, ahead) => {
        const cs = [];
        for (const x of [box.bb[0], box.bb[1]]) for (const y of [box.bb[2], box.bb[3]]) for (const z of [box.bb[4], box.bb[5]]) cs.push(cam.project([x, y, z]));
        let sb = [Math.min(...cs.map((p) => p[0])), Math.min(...cs.map((p) => p[1])), Math.max(...cs.map((p) => p[0])), Math.max(...cs.map((p) => p[1]))];
        if (box.sb) sb = [Math.min(sb[0], box.sb[0]), Math.min(sb[1], box.sb[1]), Math.max(sb[2], box.sb[2]), Math.max(sb[3], box.sb[3])];   // 图比身体宽（头发散开）：按图在画面上实际占的范围判断有没有挡到
        // must：1 = 它必须画在她后面，-1 = 它必须画在她前面，0 = 不用管（画面上不重叠，或者前后关系说不准）
        const must = order.map((e) => {
          const m = e.o;
          if (e.b == null || SHELL.has(m.g) || !e.sb) return 0;
          if (ahead && ahead.includes(`${m.g}/${m.name}`)) return 1;                // ahead 优先：这个视角里它盖住她
          if (after && after.includes(`${m.g}/${m.name}`)) return -1;
          if (e.sb[2] <= sb[0] || sb[2] <= e.sb[0] || e.sb[3] <= sb[1] || sb[3] <= e.sb[1]) return 0;
          const cb = before(box, m), bc = before(m, box);
          if (cb !== bc) return cb ? 1 : -1;
          // 说不准（转过角度的小家具，比如椅子：外包盒很大，和她的盒子交叠）：小东西就按离相机的远近定，免得她站在椅子后面却画在椅子前面（穿模）
          const mb = m.bb;
          if (mb && Math.max(mb[1] - mb[0], mb[3] - mb[2]) < 80) {
            const c = cam.pos, d = (b) => Math.hypot((b[0] + b[1]) / 2 - c[0], (b[2] + b[3]) / 2 - c[1]);
            const dm = d(mb), dc = d(box.bb);
            if (Math.abs(dm - dc) > 8) return dc > dm ? 1 : -1;            // 她更远 → 它画在她前面
          }
          return 0;
        });
        let w = must.filter((v) => v < 0).length, best = 0, bw = w;           // w：把她放在 idx 之前时，有多少条关系被破坏
        for (let i = 0; i < must.length; i++) {
          if (must[i] > 0) w++; else if (must[i] < 0) w--;
          if (w <= bw) { bw = w; best = i + 1; }                              // 同样好就取靠后的（画在更前面）
        }
        return best;
      },
      screenBox: (m) => { const e = order.find((x) => x.o === m); return e && e.sb; },
    };
    return api;
  }

  const svgBody = (r) => `<g stroke-linejoin="round" stroke-linecap="round">${r.order.map((e) => `<g data-n="${e.o.name}" data-g="${e.o.g || ""}"${e.o.halo || e.o.shadow ? ' pointer-events="none"' : ""}>${e.svg}</g>`).join("")}</g>`;
  function toSvg(r, extra) {
    const vb = r.viewBox, hole = r.layered && r.hole ? `M${r.hole.map((p) => `${n2(p[0])} ${n2(p[1])}`).join("L")}Z` : "";
    const bg = !r.bg ? "" : hole                                                         // 分层画窗外时，底色在窗洞那里留空（透出下面的动画层）
      ? `<path d="M${vb[0] - 2} ${vb[1] - 2}h${vb[2] + 4}v${vb[3] + 4}h${-(vb[2] + 4)}Z${hole}" fill-rule="evenodd" fill="${r.bg}"/>`
      : `<rect x="${vb[0] - 2}" y="${vb[1] - 2}" width="${vb[2] + 4}" height="${vb[3] + 4}" fill="${r.bg}"/>`;
    const paper = r.st.mode === "wash" ? X.paperDefs("r3paper") + `<rect x="${r.viewBox[0] - 2}" y="${r.viewBox[1] - 2}" width="${r.viewBox[2] + 4}" height="${r.viewBox[3] + 4}" fill="url(#r3paper)" opacity=".55" pointer-events="none"/>` : "";
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${r.viewBox.join(" ")}" width="${r.width}" height="${r.height}">${bg}${svgBody(r)}${paper}${r.overlay || ""}${extra || ""}</svg>`;
  }

  /** 分层时窗外的树和坡（单独一张 SVG，和主画面同一个 viewBox，叠在天空动画层上面、雨雪层下面）。 */
  const toOutdoorSvg = (r) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${r.viewBox.join(" ")}"><g stroke-linejoin="round" stroke-linecap="round">${r.outdoorSvg || ""}</g></svg>`;

  const API = { render, toSvg, toOutdoorSvg, svgBody, W, buildScene, palOf, partColor, X, SLOTS, AREA_OF, SHELL, HIDE, BASKET, FLOOR_PILES, LAYOUT: L, ROOM: R, STATES: S3.STATES, GROUPS: S3.GROUPS, CHAIR_F: L.CHAIR_F, DESK_YAW: S3.DESK_YAW, DESK_X: S3.DESK_X, roundedPath, faceColor };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.PhalRoom3D = API;
})(typeof window !== "undefined" ? window : globalThis);
