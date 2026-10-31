/* 房间的 2.5D 等距渲染（卡通扁平：圆角、描边、低饱和蓝色调）。
 *
 * 【现状】房间页面现在用 web/room3d.js（greyBoxTest 的统一灰模，房间 + 书桌两个视角）。这个文件里还在用的是：三套风格的定义（STYLES / styleOf）、
 * 书 / 封面 / 书脊的画法（spineSvg / coverSvg / coverStandalone / head8 …）、tone / hull 等小工具；下面「房间：家具」那一大段（buildScene / renderScene，
 * 第 4 版灰模的旧房间）已经没人用了，先留着，选定新房间的风格以后再清掉。
 *
 * 房间的几何来自灰模原型 greyBoxTest/greybox.js（不在仓库里）（第 4 版灰模）：456×400×240cm，后面两面墙，
 * 后墙 y=0：衣柜 → 窗 → 书桌+书架；左墙 x=0：床头靠着它；床头柜、椅子；没有门。坐标 x 向右下、y 向左下、z 向上，单位 cm，真等距 30°。
 * 这个文件只管「画」：不碰网络、不碰 DOM，浏览器和 Node 都能用，所以风格小样和房间页面用的是同一份代码。
 *
 * 外观全部由「参数」决定，方便以后扩展 / 替换：
 *   · 风格预设 STYLES（描边、圆角、明暗、配色）；
 *   · 家具的外观参数 opts.furniture.bed / desk / ……（主色、花纹……）；
 *   · 书的外观参数 look（主色、花纹、书脊宽高、书名排版）——封面和书脊由同一组参数生成，保证一致；
 *   · 小物件的外观参数 look（形状、颜色、大小）。
 */
(function (root) {
  "use strict";

  const ROOM = { W: 456, D: 400, H: 240, T: 8, F: 8 };
  const n2 = (v) => Math.round(v * 100) / 100;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ───────────────── 颜色 ───────────────── */
  function hex2rgb(h) {
    const s = String(h).replace("#", "");
    const v = s.length === 3 ? s.split("").map((c) => c + c).join("") : s;
    return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
  }
  function rgb2hsl([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    if (mx === mn) return [0, 0, l];
    const d = mx - mn, s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
    const h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l];
  }
  function hsl2hex([h, s, l]) {
    h = ((h % 360) + 360) % 360;
    const c = (1 - Math.abs(2 * l - 1)) * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2;
    const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return "#" + [r, g, b].map((v) => Math.round(clamp(v + m, 0, 1) * 255).toString(16).padStart(2, "0")).join("");
  }
  /** 调亮 / 调暗（dl 是亮度增量），阴影（dl<0）往冷色（蓝）偏一点，更像卡通的色块阴影。 */
  function tone(hex, dl, cool) {
    const [h, s, l] = rgb2hsl(hex2rgb(hex));
    let hh = h;
    if (dl < 0 && cool) {
      const diff = ((222 - h + 540) % 360) - 180;
      hh = h + clamp(diff, -cool, cool);
    }
    return hsl2hex([hh, clamp(s * (dl < 0 ? 1.06 : 0.96), 0, 1), clamp(l + dl, 0, 1)]);
  }
  const mix = (a, b, t) => { const A = hex2rgb(a), B = hex2rgb(b); return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join(""); };

  /* ───────────────── 风格预设：小样里的 A / B / C ───────────────── */
  const BASE_PAL = {
    floor: "#c7d7ec", floorLine: "#b4c6e0", wallN: "#e3ebf6", wallW: "#d6e1f1", trim: "#b9cbe3", glass: "#d9ecfa", glassHi: "#ffffff",
    bedHead: "#7790b8", bedFrame: "#8aa3c8", mattress: "#f2f6fc", pillow: "#ffffff", blanket: "#93b1dd", blanketB: "#e0ebf9",
    nsBody: "#9bb2d3", nsTop: "#b9c9e3", drawer: "#c8d6ec", knob: "#f4eacf",
    deskTop: "#dae3f0", deskLeg: "#8da5ca", shelf: "#a4b9d9", shelfBoard: "#c2d1e8", shelfBack: "#8ba3c7", shelfTop: "#b3c5e0",
    chairSeat: "#9fb6d8", chairLeg: "#7f98be", chairBack: "#8fa8cd",
    wardrobe: "#a7bcda", wardDoor: "#bccbe2", cornice: "#8ea7cb", wood: "#c9b79d",
    frame: "#f4f7fc", curtain: "#b7c9e7", rod: "#8b9cba", basket: "#d8c8a6", basketB: "#bba98a",
    paper: "#f7f4ec", paperLine: "#d9d3c4",
  };
  const STYLES = {
    A: {
      id: "A", name: "A · 软萌圆润", desc: "最圆最软：大圆角、粗描边、三段明暗；枕头是胖胖的药丸形，毯子下摆有小波浪。",
      ink: "#2b4068", inkW: 2.6, innerW: 2.0, radMax: 9, tone: { top: 0.09, right: -0.13, cool: 8 }, sticker: 0, rim: 0.28, detail: 1,
      pal: {},
    },
    B: {
      id: "B", name: "B · 贴纸白边", desc: "像贴纸：每件家具外面再勾一圈白边；明暗更少更平，配色更灰蓝，用淡粉做点缀，小星星 / 圆点。",
      ink: "#324c78", inkW: 2.2, innerW: 1.5, radMax: 6, tone: { top: 0.07, right: -0.09, cool: 5 }, sticker: 6, rim: 0, detail: 2,
      pal: { floor: "#d2dcec", floorLine: "#c0cde2", wallN: "#eaeef6", wallW: "#dde5f2", bedFrame: "#94a5c5", bedHead: "#8294b8", blanket: "#aebcdf", blanketB: "#f3c6d4",
             deskTop: "#e1e7f1", deskLeg: "#97a9ca", shelf: "#aebcd8", shelfBoard: "#cdd7ea", curtain: "#f0d3dc", chairSeat: "#efcdd8", chairBack: "#e6bccb", chairLeg: "#8c9dc0" },
    },
    C: {
      id: "C", name: "C · 细线精致", desc: "更精致：细描边、小圆角，右侧阴影有斜线排线，桌面有木纹，毯子有缝线；点缀一点点低饱和的暖木色。",
      ink: "#3a4d70", inkW: 1.6, innerW: 1.2, radMax: 3.5, tone: { top: 0.08, right: -0.12, cool: 7 }, sticker: 0, rim: 0, detail: 3, hatch: 1,
      pal: { floor: "#c3d2e6", floorLine: "#aebfd8", bedFrame: "#8199bd", blanket: "#8ba8d4", blanketB: "#d2e0f3", deskTop: "#cfbca2", deskLeg: "#8398bb", shelf: "#9ab0d2", shelfBoard: "#b9c9e2",
             chairSeat: "#c9b79d", chairLeg: "#7a91b8", chairBack: "#b7a58b", nsTop: "#cdbba1", wood: "#c9b79d" },
    },
  };
  function styleOf(id) {
    const s = typeof id === "object" ? id : (STYLES[id] || STYLES.A);
    return Object.assign({}, s, { pal: Object.assign({}, BASE_PAL, s.pal || {}) });
  }

  /* ───────────────── 相机 ───────────────── */
  function camera(scale) {
    const el = Math.asin(1 / Math.sqrt(3)), s = Math.sin(el), c = Math.cos(el), r = Math.SQRT1_2;
    const P = (x, y, z) => [(x - y) * r * scale, ((x + y) * r * s - z * c) * scale];
    return { P, scale };
  }
  /** 局部坐标（u, v，单位 cm）→ 屏幕的 SVG matrix。画在某个面上的字、花纹都靠它。 */
  function faceMatrix(P, o, u, v) {
    const p0 = P(o[0], o[1], o[2]), pu = P(o[0] + u[0], o[1] + u[1], o[2] + u[2]), pv = P(o[0] + v[0], o[1] + v[1], o[2] + v[2]);
    return `matrix(${n2(pu[0] - p0[0])} ${n2(pu[1] - p0[1])} ${n2(pv[0] - p0[0])} ${n2(pv[1] - p0[1])} ${n2(p0[0])} ${n2(p0[1])})`;
  }

  /* ───────────────── 几何小工具 ───────────────── */
  const bounds = (o) => {
    if (o.kind === "ball") return { x0: o.cx - o.r, x1: o.cx + o.r, y0: o.cy - o.r, y1: o.cy + o.r, z0: o.cz - o.r, z1: o.cz + o.r };
    if (o.kind === "cyl") return { x0: o.cx - o.rx, x1: o.cx + o.rx, y0: o.cy - o.ry, y1: o.cy + o.ry, z0: o.z, z1: o.z + o.h };
    if (o.yaw) {
      const cs = prismCorners(o), xs = cs.map((c) => c[0]), ys = cs.map((c) => c[1]);
      return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys), z0: o.z, z1: o.z + o.h };
    }
    return { x0: o.x, x1: o.x + o.w, y0: o.y, y1: o.y + o.d, z0: o.z, z1: o.z + o.h };
  };
  function prismCorners(o) {
    const a = ((o.yaw || 0) * Math.PI) / 180, cs = Math.cos(a), sn = Math.sin(a), cx = o.x + o.w / 2, cy = o.y + o.d / 2;
    return [[-o.w / 2, -o.d / 2], [o.w / 2, -o.d / 2], [o.w / 2, o.d / 2], [-o.w / 2, o.d / 2]].map(([dx, dy]) => [cx + dx * cs - dy * sn, cy + dx * sn + dy * cs]);
  }

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
  const minEdge = (pts) => Math.min(...pts.map((p, i) => Math.hypot(p[0] - pts[(i + 1) % pts.length][0], p[1] - pts[(i + 1) % pts.length][1])));
  const poly = (pts, fill, extra = "") => `<polygon points="${pts.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}" fill="${fill}"${extra}/>`;

  function hull(pts) {
    const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const half = (list) => { const out = []; for (const q of list) { while (out.length >= 2 && cross(out[out.length - 2], out[out.length - 1], q) <= 0) out.pop(); out.push(q); } out.pop(); return out; };
    return half(p).concat(half(p.slice().reverse()));
  }

  /* ───────────────── 画家算法：先画后面的，再画前面的 ───────────────── */
  const EPS = 1e-6;
  const behind = (a, b) => a.x1 <= b.x0 + EPS || a.y1 <= b.y0 + EPS || a.z1 <= b.z0 + EPS;     // a 在 b 的后面 / 下面
  function screenBox(b, P) {
    const xs = [], ys = [];
    for (const x of [b.x0, b.x1]) for (const y of [b.y0, b.y1]) for (const z of [b.z0, b.z1]) { const p = P(x, y, z); xs.push(p[0]); ys.push(p[1]); }
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  function sortObjects(objs, P) {
    const bs = objs.map(bounds), sb = bs.map((b) => screenBox(b, P)), n = objs.length, out = objs.map(() => []), indeg = Array(n).fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (sb[i][2] <= sb[j][0] || sb[j][2] <= sb[i][0] || sb[i][3] <= sb[j][1] || sb[j][3] <= sb[i][1]) continue;
        const ij = behind(bs[i], bs[j]), ji = behind(bs[j], bs[i]);
        if (ij === ji) continue;
        const [a, b] = ij ? [i, j] : [j, i];
        out[a].push(b);
        indeg[b]++;
      }
    }
    const key = (i) => bs[i].x0 + bs[i].x1 + bs[i].y0 + bs[i].y1 + 0.01 * (bs[i].z0 + bs[i].z1);
    const order = [], left = new Set(objs.keys());
    while (left.size) {
      let pick = -1;
      for (const i of left) if (indeg[i] === 0 && (pick < 0 || key(i) < key(pick))) pick = i;
      if (pick < 0) for (const i of left) if (pick < 0 || key(i) < key(pick)) pick = i;
      left.delete(pick);
      order.push(pick);
      out[pick].forEach((b) => { indeg[b]--; });
    }
    return order.map((i) => objs[i]);
  }

  /* ───────────────── 画一个方块（圆角 + 描边） ───────────────── */
  let UID = 0;
  function faceInfo(o, which) {
    const b = bounds(o), w = b.x1 - b.x0, d = b.y1 - b.y0, h = b.z1 - b.z0;
    if (o.yaw && (which === "top" || which === "left")) {
      const a = (o.yaw * Math.PI) / 180, cs = Math.cos(a), sn = Math.sin(a), cx = o.x + o.w / 2, cy = o.y + o.d / 2;
      const rot = (dx, dy) => [cx + dx * cs - dy * sn, cy + dx * sn + dy * cs];
      const ux = [cs, sn, 0], z1 = o.z + o.h;
      if (which === "top") { const c0 = rot(-o.w / 2, -o.d / 2); return { origin: [c0[0], c0[1], z1], u: ux, v: [-sn, cs, 0], W: o.w, H: o.d }; }
      const c0 = rot(-o.w / 2, o.d / 2);
      return { origin: [c0[0], c0[1], z1], u: ux, v: [0, 0, -1], W: o.w, H: o.h };
    }
    if (which === "top") return { origin: [b.x0, b.y0, b.z1], u: [1, 0, 0], v: [0, 1, 0], W: w, H: d };
    if (which === "left") return { origin: [b.x0, b.y1, b.z1], u: [1, 0, 0], v: [0, 0, -1], W: w, H: h };
    return { origin: [b.x1, b.y1, b.z1], u: [0, -1, 0], v: [0, 0, -1], W: d, H: h };            // right：u 沿 -y，往屏幕右上走
  }
  function facePoly(o, which, P) {
    const f = faceInfo(o, which), at = (u, v) => P(f.origin[0] + f.u[0] * u + f.v[0] * v, f.origin[1] + f.u[1] * u + f.v[1] * v, f.origin[2] + f.u[2] * u + f.v[2] * v);
    return [at(0, 0), at(f.W, 0), at(f.W, f.H), at(0, f.H)];
  }

  /** 方块上的装饰：写在某个面的局部坐标里（u 向右、v 向下，单位 cm），自动裁在这个面里。 */
  function decoSvg(o, P, st) {
    if (!o.deco || !o.deco.length) return "";
    let out = "";
    o.deco.forEach((d) => {
      if (d.min && st.detail < d.min) return;
      const f = faceInfo(o, d.face || "top"), m = faceMatrix(P, f.origin, f.u, f.v), id = `fc${++UID}`;
      const fp = facePoly(o, d.face || "top", P);
      let inner = "";
      const W = f.W, H = f.H, col = d.color || "#fff", op = d.op == null ? 1 : d.op;
      if (d.type === "stripes") {
        const gap = d.gap || 8, sw = d.w || gap / 2, vert = d.dir === "v";
        for (let t = d.off || gap / 2; t < (vert ? W : H); t += gap) inner += vert ? `<rect x="${n2(t - sw / 2)}" y="0" width="${n2(sw)}" height="${n2(H)}"/>` : `<rect x="0" y="${n2(t - sw / 2)}" width="${n2(W)}" height="${n2(sw)}"/>`;
        inner = `<g fill="${col}" fill-opacity="${op}">${inner}</g>`;
      } else if (d.type === "gingham") {
        const gap = d.gap || 8;
        for (let a = gap / 2; a < W; a += gap) inner += `<rect x="${n2(a - gap / 4)}" y="0" width="${n2(gap / 2)}" height="${n2(H)}"/>`;
        for (let a = gap / 2; a < H; a += gap) inner += `<rect x="0" y="${n2(a - gap / 4)}" width="${n2(W)}" height="${n2(gap / 2)}"/>`;
        inner = `<g fill="${col}" fill-opacity="${op * 0.5}">${inner}</g>`;
      } else if (d.type === "dots") {
        const gap = d.gap || 8, r = d.r || 1.2;
        for (let a = gap / 2, row = 0; a < H; a += gap, row++) for (let b = gap / 2 + (row % 2 ? gap / 2 : 0); b < W; b += gap) inner += `<circle cx="${n2(b)}" cy="${n2(a)}" r="${r}"/>`;
        inner = `<g fill="${col}" fill-opacity="${op}">${inner}</g>`;
      } else if (d.type === "stitch") {
        const i = d.inset || 3;
        inner = `<rect x="${i}" y="${i}" width="${n2(W - 2 * i)}" height="${n2(H - 2 * i)}" rx="${d.rx || 3}" fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.7}" stroke-dasharray="${d.dash || "2.6 2.2"}" stroke-linecap="round"/>`;
      } else if (d.type === "grain") {
        let seed = d.seed || 7;
        const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
        for (let k = 0; k < (d.n || 6); k++) { const y = (H * (k + 0.5)) / (d.n || 6) + (rnd() - 0.5) * 3, x0 = rnd() * W * 0.3, x1 = W - rnd() * W * 0.3; inner += `<path d="M${n2(x0)} ${n2(y)}Q${n2((x0 + x1) / 2)} ${n2(y + (rnd() - 0.5) * 3)} ${n2(x1)} ${n2(y)}"/>`; }
        inner = `<g fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.6}" stroke-linecap="round">${inner}</g>`;
      } else if (d.type === "scallop") {
        const gap = d.gap || 9, r = gap / 2.2, y = d.y == null ? H : d.y;
        for (let a = gap / 2; a < W; a += gap) inner += `<path d="M${n2(a - r)} ${n2(y - r)}a${n2(r)} ${n2(r)} 0 0 0 ${n2(2 * r)} 0"/>`;
        inner = `<g fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.9}" stroke-linecap="round">${inner}</g>`;
      } else if (d.type === "hatch") {
        const gap = d.gap || 5;
        for (let a = -H; a < W; a += gap) inner += `<path d="M${n2(a)} ${n2(H)}L${n2(a + H)} 0"/>`;
        inner = `<g fill="none" stroke="${col}" stroke-opacity="${op}" stroke-width="${d.sw || 0.5}">${inner}</g>`;
      } else if (d.type === "rect") {
        inner = `<rect x="${n2(d.x)}" y="${n2(d.y)}" width="${n2(d.w)}" height="${n2(d.h)}" rx="${d.rx || 0}" fill="${col}" fill-opacity="${op}"${d.stroke ? ` stroke="${d.stroke}" stroke-width="${d.sw || 0.6}"` : ""}/>`;
      } else if (d.type === "circle") {
        inner = `<circle cx="${n2(d.x)}" cy="${n2(d.y)}" r="${d.r}" fill="${col}" fill-opacity="${op}"${d.stroke ? ` stroke="${d.stroke}" stroke-width="${d.sw || 0.6}"` : ""}/>`;
      } else if (d.type === "star") {
        const r = d.r || 2, pts = [];
        for (let k = 0; k < 10; k++) { const a = (k * Math.PI) / 5 - Math.PI / 2, rr = k % 2 ? r * 0.45 : r; pts.push(`${n2(d.x + rr * Math.cos(a))},${n2(d.y + rr * Math.sin(a))}`); }
        inner = `<polygon points="${pts.join(" ")}" fill="${col}" fill-opacity="${op}"/>`;
      } else if (d.type === "raw") {
        inner = d.svg;
      }
      out += `<clipPath id="${id}"><polygon points="${fp.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}"/></clipPath><g clip-path="url(#${id})"><g transform="${m}">${inner}</g></g>`;
    });
    return out;
  }

  function drawBox(o, P, st, sc) {
    const b = bounds(o), { x0, x1, y0, y1, z0, z1 } = b;
    const base = o.col;
    const cTop = tone(base, st.tone.top, 0), cLeft = base, cRight = tone(base, st.tone.right, st.tone.cool);
    const sil = [P(x0, y1, z0), P(x1, y1, z0), P(x1, y0, z0), P(x1, y0, z1), P(x0, y0, z1), P(x0, y1, z1)];
    const V = P(x1, y1, z1), E1 = P(x0, y1, z1), E2 = P(x1, y0, z1), E3 = P(x1, y1, z0);
    const left = [P(x0, y1, z0), P(x1, y1, z0), P(x1, y1, z1), P(x0, y1, z1)];
    const right = [P(x1, y0, z0), P(x1, y1, z0), P(x1, y1, z1), P(x1, y0, z1)];
    const top = [P(x0, y0, z1), P(x1, y0, z1), P(x1, y1, z1), P(x0, y1, z1)];
    const me = minEdge(sil);
    const rad = o.pill ? me * 0.49 : o.sharp ? 0 : Math.min((o.rad == null ? st.radMax : o.rad) * (sc / 1.6), me * 0.42);
    const id = `bx${++UID}`, path = rad > 0.2 ? roundedPath(sil, rad) : `M${sil.map((p) => `${n2(p[0])} ${n2(p[1])}`).join("L")}Z`;
    const halo = st.sticker && !o.noSticker ? `<path d="${path}" fill="#fff" stroke="#fff" stroke-width="${st.sticker * (sc / 1.6)}" stroke-linejoin="round"/>` : "";
    let svg = `<clipPath id="${id}"><path d="${path}"/></clipPath><g clip-path="url(#${id})">`
      + poly(left, cLeft) + poly(right, cRight) + poly(top, cTop);
    if (st.hatch && !o.noHatch && me > 6) {                                    // 右面斜线排线（C）
      let h = "";
      const rp = right, bx0 = Math.min(...rp.map((p) => p[0])), bx1 = Math.max(...rp.map((p) => p[0])), by0 = Math.min(...rp.map((p) => p[1])), by1 = Math.max(...rp.map((p) => p[1]));
      for (let t = bx0 - (by1 - by0); t < bx1; t += 5 * (sc / 1.6)) h += `M${n2(t)} ${n2(by1)}L${n2(t + (by1 - by0))} ${n2(by0)}`;
      svg += `<g clip-path="url(#${id}r)" stroke="${tone(base, -0.2, st.tone.cool)}" stroke-opacity=".45" stroke-width=".7" fill="none"><path d="${h}"/></g>`
        + `<clipPath id="${id}r"><polygon points="${rp.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}"/></clipPath>`;
    }
    svg += decoSvg(o, P, st);
    if (st.rim && !o.noRim) {                                                   // 顶面左上沿的一条高光
      const a = P(x0, y1, z1), c = P(x0, y0, z1), e = P(x1, y0, z1), k = 1.4 * (sc / 1.6);
      svg += `<path d="M${n2(a[0] + k)} ${n2(a[1] - k * 0.5)}L${n2(c[0] + k)} ${n2(c[1] + k)}L${n2(e[0] - k)} ${n2(e[1] + k)}" fill="none" stroke="#fff" stroke-opacity="${st.rim}" stroke-width="${1.2 * (sc / 1.6)}" stroke-linecap="round" stroke-linejoin="round"/>`;
    }
    svg += `</g>`;
    const thin = o.book ? 0.38 : 1, iw = st.innerW * (sc / 1.6) * thin, ow = st.inkW * (sc / 1.6) * thin;
    svg += `<g fill="none" stroke="${st.ink}" stroke-linecap="round" stroke-linejoin="round"><path d="M${n2(E1[0])} ${n2(E1[1])}L${n2(V[0])} ${n2(V[1])}L${n2(E2[0])} ${n2(E2[1])}M${n2(V[0])} ${n2(V[1])}L${n2(E3[0])} ${n2(E3[1])}" stroke-width="${n2(iw)}"/>`
      + `<path d="${path}" stroke-width="${n2(ow)}"/></g>`;
    return { svg, pts: sil, halo };
  }

  /** 旋转过的方块（歪着放的书）：底面四个角转一个角度，再往上拉。 */
  function drawPrism(o, P, st, sc) {
    const cs = prismCorners(o), z0 = o.z, z1 = o.z + o.h, base = o.col;
    const cTop = tone(base, st.tone.top, 0), cLeft = base, cRight = tone(base, st.tone.right, st.tone.cool);
    let svg = "", pts = [];
    const sides = [];
    for (let i = 0; i < 4; i++) {
      const a = cs[i], b = cs[(i + 1) % 4], nx = b[1] - a[1], ny = -(b[0] - a[0]);              // 外法线（底面顺时针时朝外）
      if (nx + ny > 0) sides.push({ a, b, k: ny >= nx ? cLeft : cRight, nx, ny });
    }
    const ow = st.inkW * (sc / 1.6) * (o.book ? 0.4 : 0.85);
    const halo = st.sticker && !o.noSticker ? `<path d="${roundedPath(hull(cs.map((c) => P(c[0], c[1], z0)).concat(cs.map((c) => P(c[0], c[1], z1)))), 3)}" fill="#fff" stroke="#fff" stroke-width="${st.sticker * (sc / 1.6) * 0.7}" stroke-linejoin="round"/>` : "";
    sides.forEach((s) => { const q = [P(s.a[0], s.a[1], z0), P(s.b[0], s.b[1], z0), P(s.b[0], s.b[1], z1), P(s.a[0], s.a[1], z1)]; svg += poly(q, s.k, ` stroke="${st.ink}" stroke-width="${n2(ow)}" stroke-linejoin="round"`); pts = pts.concat(q); });
    const t = cs.map((c) => P(c[0], c[1], z1));
    svg += poly(t, cTop, ` stroke="${st.ink}" stroke-width="${n2(ow)}" stroke-linejoin="round"`);
    pts = pts.concat(t);
    svg += decoSvg(o, P, st);
    return { svg, pts, halo };
  }

  function drawCyl(o, P, st, sc) {
    const N = 40, top = [], bot = [], z1 = o.z + o.h, base = o.col;
    for (let i = 0; i < N; i++) { const a = (i / N) * Math.PI * 2, x = o.cx + o.rx * Math.cos(a), y = o.cy + o.ry * Math.sin(a); top.push(P(x, y, z1)); bot.push(P(x, y, o.z)); }
    const sil = hull(top.concat(bot)), mid = P(o.cx, o.cy, o.z)[0];
    const cLeft = base, cRight = tone(base, st.tone.right, st.tone.cool), cTop = tone(base, st.tone.top, 0);
    const id = `cy${++UID}`, ow = st.inkW * (sc / 1.6);
    const halo = st.sticker && !o.noSticker ? poly(sil, "#fff", ` stroke="#fff" stroke-width="${st.sticker * (sc / 1.6)}" stroke-linejoin="round"`) : "";
    let svg = `<clipPath id="${id}"><polygon points="${sil.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}"/></clipPath><g clip-path="url(#${id})">${poly(sil, cLeft)}`
      + `<rect x="${n2(mid)}" y="${n2(Math.min(...sil.map((p) => p[1])) - 2)}" width="${n2(Math.max(...sil.map((p) => p[0])) - mid + 2)}" height="${n2(Math.max(...sil.map((p) => p[1])) - Math.min(...sil.map((p) => p[1])) + 4)}" fill="${cRight}"/></g>`
      + `<polygon points="${sil.map((p) => `${n2(p[0])},${n2(p[1])}`).join(" ")}" fill="none" stroke="${st.ink}" stroke-width="${n2(ow)}" stroke-linejoin="round"/>`
      + poly(top, cTop, ` stroke="${st.ink}" stroke-width="${n2(ow * 0.8)}" stroke-linejoin="round"`);
    return { svg, pts: sil, halo };
  }

  /** 一个椭球（毛绒玩具的头 / 身子）：画成椭圆，右下一小块阴影。 */
  function drawBall(o, P, st, sc) {
    const c = P(o.cx, o.cy, o.cz), rx = o.r * 0.8165 * sc, ry = rx;
    const ow = st.inkW * (sc / 1.6) * 0.9, base = o.col, id = `bl${++UID}`;
    const halo = st.sticker && !o.noSticker ? `<ellipse cx="${n2(c[0])}" cy="${n2(c[1])}" rx="${n2(rx)}" ry="${n2(ry)}" fill="#fff" stroke="#fff" stroke-width="${st.sticker * (sc / 1.6) * 0.7}"/>` : "";
    let svg = `<clipPath id="${id}"><ellipse cx="${n2(c[0])}" cy="${n2(c[1])}" rx="${n2(rx)}" ry="${n2(ry)}"/></clipPath>`
      + `<g clip-path="url(#${id})"><ellipse cx="${n2(c[0])}" cy="${n2(c[1])}" rx="${n2(rx)}" ry="${n2(ry)}" fill="${base}"/>`
      + `<ellipse cx="${n2(c[0] + rx * 0.35)}" cy="${n2(c[1] + ry * 0.4)}" rx="${n2(rx * 0.95)}" ry="${n2(ry * 0.95)}" fill="${tone(base, st.tone.right, st.tone.cool)}"/>`
      + `<ellipse cx="${n2(c[0] - rx * 0.12)}" cy="${n2(c[1] - ry * 0.12)}" rx="${n2(rx * 0.9)}" ry="${n2(ry * 0.88)}" fill="${base}"/>`
      + `<ellipse cx="${n2(c[0] - rx * 0.35)}" cy="${n2(c[1] - ry * 0.4)}" rx="${n2(rx * 0.3)}" ry="${n2(ry * 0.22)}" fill="#fff" fill-opacity=".45"/></g>`
      + `<ellipse cx="${n2(c[0])}" cy="${n2(c[1])}" rx="${n2(rx)}" ry="${n2(ry)}" fill="none" stroke="${st.ink}" stroke-width="${n2(ow)}"/>`;
    const pts = [[c[0] - rx, c[1] - ry], [c[0] + rx, c[1] + ry], [c[0] - rx, c[1] + ry], [c[0] + rx, c[1] - ry]];
    return { svg, pts, halo };
  }

  /* ───────────────── 书：封面和书脊由同一组参数生成 ───────────────── */
  const FONTS = { serif: "'Songti SC','SimSun','Noto Serif SC',serif", sans: "'Microsoft YaHei','PingFang SC','Noto Sans SC',sans-serif", round: "'YouYuan','Microsoft YaHei','PingFang SC',sans-serif" };
  /** 书上最多显示书名的前 8 个字（书脊、封面、列表都一样；完整书名只在「修改书籍详情」里看得到）。
   *  汉字算一个字，英文字母 / 数字 / 半角符号算半个；断在英文单词或数字中间时退回到这个词的开头，不把词劈成两半。 */
  const head8 = (t) => {
    const a = Array.from(String(t || "").trim());
    let u = 0, i = 0;
    for (; i < a.length; i++) {
      const w = a[i].codePointAt(0) < 0x250 ? 0.5 : 1;
      if (u + w > 8 + 1e-9) break;
      u += w;
    }
    const isW = (ch) => /[A-Za-z0-9À-ɏ'’]/.test(ch);
    if (i < a.length && i > 0 && isW(a[i]) && isW(a[i - 1])) { let j = i; while (j > 0 && isW(a[j - 1])) j--; if (j > 0) i = j; }
    return a.slice(0, i).join("").trim();
  };
  const clipTitle = (t, n) => { const a = Array.from(String(t || "")); return a.length > n ? a.slice(0, n - 1).join("") + "…" : a.join(""); };

  function bookDefaults(look) {
    const l = Object.assign({ v: 1, main: "#d8e0ee", accent: "#8fa5c8", ink: "#2f3f5c", pattern: "plain", w: 2.8, h: 24, cw: 16, tl: "v", tf: "serif", tp: "t", band: 1, emblem: "none" }, look || {});
    l.w = clamp(+l.w || 2.8, 1.2, 6); l.h = clamp(+l.h || 24, 14, 34); l.cw = clamp(+l.cw || 16, 11, 24);
    return l;
  }
  /** 花纹：画在 W×H（cm）的区域里。书脊、封面都用它，pattern 相同、颜色相同，所以看上去是同一本书。 */
  function patternSvg(look, W, H) {
    const l = bookDefaults(look), a = l.accent;
    const step = Math.max(2.2, Math.min(W, H) / 5);
    switch (l.pattern) {
      case "stripes": { let s = ""; for (let t = step / 2; t < W; t += step) s += `<rect x="${n2(t - step * 0.18)}" y="0" width="${n2(step * 0.36)}" height="${n2(H)}"/>`; return `<g fill="${a}" fill-opacity=".55">${s}</g>`; }
      case "dots": { let s = ""; for (let y = step / 2, r = 0; y < H; y += step, r++) for (let x = step / 2 + (r % 2 ? step / 2 : 0); x < W; x += step) s += `<circle cx="${n2(x)}" cy="${n2(y)}" r="${n2(step * 0.16)}"/>`; return `<g fill="${a}" fill-opacity=".6">${s}</g>`; }
      case "band": return `<rect x="0" y="${n2(H * 0.42)}" width="${n2(W)}" height="${n2(H * 0.16)}" fill="${a}" fill-opacity=".55"/>`;
      case "grid": { let s = ""; for (let x = step; x < W; x += step) s += `M${n2(x)} 0V${n2(H)}`; for (let y = step; y < H; y += step) s += `M0 ${n2(y)}H${n2(W)}`; return `<path d="${s}" stroke="${a}" stroke-opacity=".5" stroke-width="${n2(step * 0.07)}" fill="none"/>`; }
      case "diamond": { let s = ""; for (let y = 0, r = 0; y < H + step; y += step, r++) for (let x = (r % 2 ? step / 2 : 0); x < W + step; x += step) s += `<path d="M${n2(x)} ${n2(y - step * 0.3)}l${n2(step * 0.3)} ${n2(step * 0.3)}l${n2(-step * 0.3)} ${n2(step * 0.3)}l${n2(-step * 0.3)} ${n2(-step * 0.3)}z"/>`; return `<g fill="${a}" fill-opacity=".5">${s}</g>`; }
      case "wave": { let s = ""; for (let y = step * 0.6; y < H; y += step * 0.9) { s += `M0 ${n2(y)}`; for (let x = 0; x < W; x += step * 0.8) s += `q${n2(step * 0.2)} ${n2(-step * 0.22)} ${n2(step * 0.4)} 0t${n2(step * 0.4)} 0`; } return `<path d="${s}" stroke="${a}" stroke-opacity=".55" stroke-width="${n2(step * 0.09)}" fill="none"/>`; }
      default: return "";
    }
  }
  /* ───────────────── 书名排版 ─────────────────
   * 不用 SVG 自带的竖排（writing-mode）：数字会被转倒、不同字体下对不齐。改成自己一格一格地摆：
   *   汉字 / 标点：一字一格，居中；
   *   数字：两位一组，一组一格，横着正放（「1984」= 19 / 84），不转；
   *   英文单词：整个词转 90° 读（从上往下），占几格看词有多长，不会被劈成两半；
   *   竖排分栏时只在记号之间断开，栏从右往左读，每栏顶端对齐。 */
  const LH = 1.12;                                                       // 竖排一格的高度 = 字号 × LH
  const NARROW_CH = "ijlI1.,;:!|'’", WIDE_CH = "mwMW@";
  /** 一段文字横排时的宽度（单位：字号）。汉字 = 1，英文字母、数字按大致的字宽算。 */
  function emW(str) {
    let w = 0;
    for (const ch of String(str)) {
      const c = ch.codePointAt(0);
      w += c > 0x24f && c !== 0x2019 ? 1 : ch === " " ? 0.3 : NARROW_CH.includes(ch) ? 0.3 : WIDE_CH.includes(ch) ? 0.88 : ch >= "A" && ch <= "Z" ? 0.66 : ch >= "0" && ch <= "9" ? 0.58 : 0.54;
    }
    return w;
  }
  /** 书名 → 记号：c 单字 / n 数字（≤2 位）/ w 英文单词 / s 词间空格。len 是竖排时占的格数。 */
  function titleTokens(title) {
    const t = head8(title), toks = [], re = /([A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’-]*)|(\d+)|(\s+)|([\s\S])/gu;
    let m;
    while ((m = re.exec(t))) {
      if (m[1]) toks.push({ k: "w", s: m[1], len: (emW(m[1]) + 0.25) / LH });
      else if (m[2]) for (let i = 0; i < m[2].length; i += 2) toks.push({ k: "n", s: m[2].slice(i, i + 2), len: 1 });
      else if (m[3]) toks.push({ k: "s", s: " ", len: 0.4 });
      else toks.push({ k: "c", s: m[4], len: /[·•・\-–—]/.test(m[4]) ? 0.7 : 1 });
    }
    return toks.filter((tk, i) => tk.k !== "s" || (toks[i - 1] && toks[i + 1] && toks[i - 1].k !== "c" && toks[i + 1].k !== "c"));   // 空格只留在两个词 / 数字之间
  }
  const colLen = (col) => col.reduce((a, t) => a + t.len, 0);
  /** 把记号分成 n 栏（只在记号之间断开），每栏尽量一样高；返回从右到左的栏。 */
  function splitCols(toks, n) {
    if (!toks.length) return [];
    const fits = (C) => { let cols = 1, cur = 0; for (const t of toks) { if (cur + t.len > C + 1e-9) { cols++; cur = t.len; } else cur += t.len; } return cols <= n; };
    let lo = Math.max(...toks.map((t) => t.len)), hi = colLen(toks);
    for (let i = 0; i < 30; i++) { const mid = (lo + hi) / 2; if (fits(mid)) hi = mid; else lo = mid; }
    const out = [[]];
    let cur = 0;
    for (const t of toks) {
      if (cur + t.len > hi + 1e-6 && out[out.length - 1].length) { out.push([]); cur = 0; }
      out[out.length - 1].push(t);
      cur += t.len;
    }
    return out.map((c) => c.filter((t, i) => !(t.k === "s" && (i === 0 || i === c.length - 1)))).filter((c) => c.length);
  }
  /** 一栏竖排：x 是栏的中线，y0 是栏顶。返回 SVG 片段。 */
  function vcolSvg(col, x, y0, fs, f, ink, weight) {
    let y = y0, s = "";
    const base = `font-size="${n2(fs)}" font-family="${f}" font-weight="${weight}" fill="${ink}" text-anchor="middle"`;
    for (const t of col) {
      const h = t.len * fs * LH, yc = y + h / 2;
      if (t.k === "c") s += `<text x="${n2(x)}" y="${n2(yc)}" dy=".36em" ${base}>${esc(t.s)}</text>`;
      else if (t.k === "n") s += `<text x="${n2(x)}" y="${n2(yc)}" dy=".36em" ${base}${t.s.length > 1 ? ` textLength="${n2(fs * 0.98)}" lengthAdjust="spacingAndGlyphs"` : ""}>${esc(t.s)}</text>`;
      else if (t.k === "w") s += `<text transform="translate(${n2(x)} ${n2(yc)}) rotate(90)" dy=".36em" ${base}>${esc(t.s)}</text>`;
      y += h;
    }
    return s;
  }
  /** 横排：把记号分成最多 n 行（汉字可以在任意两个字之间断，英文单词 / 数字不拆），返回 [{text, w}]。 */
  function hLines(toks, n) {
    const items = [];
    toks.forEach((t) => {                                               // 连着的数字组并回一个整体
      const last = items[items.length - 1];
      if (t.k === "n" && last && last.n) { last.s += t.s; last.w = emW(last.s); }
      else items.push({ s: t.s, w: emW(t.s), sp: t.k === "s", n: t.k === "n" });
    });
    const fits = (C) => { let lines = 1, cur = 0; for (const it of items) { if (cur + it.w > C + 1e-9 && !it.sp) { lines++; cur = it.w; } else cur += it.w; } return lines <= n; };
    let lo = Math.max(...items.map((i) => i.w)), hi = items.reduce((a, i) => a + i.w, 0);
    for (let i = 0; i < 30; i++) { const mid = (lo + hi) / 2; if (fits(mid)) hi = mid; else lo = mid; }
    const out = [{ text: "" }];
    let cur = 0;
    for (const it of items) {
      if (cur + it.w > hi + 1e-6 && out[out.length - 1].text && !it.sp) { out.push({ text: "" }); cur = 0; }
      out[out.length - 1].text += it.s;
      cur += it.w;
    }
    return out.map((l) => ({ text: l.text.trim(), w: emW(l.text.trim()) })).filter((l) => l.text);
  }

  /** 书脊：W = 书脊宽（厚度）、H = 书高。装饰带、花纹、书名（竖排 / 横排 / 分栏）。 */
  function spineSvg(look, title) {
    const l = bookDefaults(look), W = l.w, H = l.h, f = FONTS[l.tf] || FONTS.serif;
    let s = `<rect x="0" y="0" width="${n2(W)}" height="${n2(H)}" fill="${l.main}"/>` + patternSvg(l, W, H);
    if (l.band) {
      const bh = Math.min(2.4, H * 0.09);
      s += `<rect x="0" y="${n2(H * 0.06)}" width="${n2(W)}" height="${n2(bh * 0.55)}" fill="${l.accent}"/><rect x="0" y="${n2(H - H * 0.06 - bh * 0.55)}" width="${n2(W)}" height="${n2(bh * 0.55)}" fill="${l.accent}"/>`;
      if (l.band > 1) s += `<rect x="0" y="${n2(H * 0.06 + bh)}" width="${n2(W)}" height="${n2(bh * 0.22)}" fill="${l.accent}"/>`;
    }
    const t = head8(title), toks = titleTokens(title);
    if (!t || !toks.length || l.tl === "none") return s;
    if (l.tl === "h") {                                                   // 整行横排，转 90° 沿书脊读（从下往上）
      const fs = clamp(Math.min(W * 0.7, (H * 0.7) / Math.max(1, emW(t))), 0.8, 2.4);
      s += `<text transform="translate(${n2(W / 2)} ${n2(H / 2)}) rotate(-90)" dy=".36em" font-size="${n2(fs)}" font-family="${f}" font-weight="600" fill="${l.ink}" text-anchor="middle">${esc(t)}</text>`;
      return s;
    }
    const two = l.tl === "s" && W >= 3.4 && colLen(toks) >= 4.5;      // 很短的书名分两栏反而读不顺（「1984」会变成「8419」），短的还是一栏
    const cols = splitCols(toks, two ? 2 : 1), C = Math.max(...cols.map(colLen));
    const fs = two && cols.length > 1 ? clamp(Math.min(W * 0.38, (H * 0.66) / (C * LH)), 0.8, 1.9) : clamp(Math.min(W * 0.62, (H * 0.72) / (C * LH)), 0.8, 2.4);
    const hc = C * fs * LH, y0 = l.tp === "t" ? H * 0.17 : l.tp === "b" ? H - H * 0.17 - hc : (H - hc) / 2;
    cols.forEach((col, i) => { s += vcolSvg(col, cols.length > 1 ? W * (i === 0 ? 0.7 : 0.3) : W / 2, y0, fs, f, l.ink, 600); });
    return s;
  }
  /** 躺着的书的书脊（横着看）：书名横排，字是正的。W = 书高（沿 x）、H = 书脊厚度。 */
  function spineFlatSvg(look, title) {
    const l = bookDefaults(look), W = l.h, H = l.w, f = FONTS[l.tf] || FONTS.serif;
    let s = `<rect x="0" y="0" width="${n2(W)}" height="${n2(H)}" fill="${l.main}"/>` + patternSvg(Object.assign({}, l, { pattern: l.pattern === "stripes" ? "stripes" : l.pattern }), W, H);
    if (l.band) s += `<rect x="${n2(W * 0.06)}" y="0" width="${n2(Math.min(1.1, W * 0.04))}" height="${n2(H)}" fill="${l.accent}"/><rect x="${n2(W * 0.94 - Math.min(1.1, W * 0.04))}" y="0" width="${n2(Math.min(1.1, W * 0.04))}" height="${n2(H)}" fill="${l.accent}"/>`;
    const t = head8(title), fs = clamp(Math.min(H * 0.62, (W * 0.7) / Math.max(1, emW(t))), 0.7, 2);
    if (t) s += `<text x="${n2(W / 2)}" y="${n2(H / 2)}" dy=".36em" font-size="${n2(fs)}" font-family="${f}" font-weight="600" fill="${l.ink}" text-anchor="middle">${esc(t)}</text>`;
    return s;
  }
  /** 封面：和书脊同一组参数（主色、花纹、装饰色、书名字体和颜色）。W×H = 封面宽 × 书高（cm）。 */
  function coverSvg(look, title, author) {
    const l = bookDefaults(look), W = l.cw, H = l.h, f = FONTS[l.tf] || FONTS.serif;
    let s = `<rect x="0" y="0" width="${n2(W)}" height="${n2(H)}" fill="${l.main}"/>` + patternSvg(l, W, H);
    const strip = Math.min(2.2, l.w * 0.8), cx = (strip + W) / 2;       // 书脊那条之外的封面正中
    s += `<rect x="0" y="0" width="${n2(strip)}" height="${n2(H)}" fill="${l.accent}" fill-opacity=".35"/><rect x="${n2(strip)}" y="0" width="0.25" height="${n2(H)}" fill="${l.ink}" fill-opacity=".25"/>`;
    if (l.band) s += `<rect x="0" y="${n2(H * 0.07)}" width="${n2(W)}" height="${n2(H * 0.025)}" fill="${l.accent}"/><rect x="0" y="${n2(H * 0.905)}" width="${n2(W)}" height="${n2(H * 0.025)}" fill="${l.accent}"/>`;
    if (l.emblem === "dot") s += `<circle cx="${n2(cx)}" cy="${n2(H * 0.22)}" r="${n2(W * 0.07)}" fill="${l.accent}"/>`;
    else if (l.emblem === "star") { let p = ""; for (let k = 0; k < 10; k++) { const an = (k * Math.PI) / 5 - Math.PI / 2, r = k % 2 ? W * 0.035 : W * 0.085; p += `${n2(cx + r * Math.cos(an))},${n2(H * 0.22 + r * Math.sin(an))} `; } s += `<polygon points="${p}" fill="${l.accent}"/>`; }
    else if (l.emblem === "leaf") s += `<path d="M${n2(cx)} ${n2(H * 0.3)}c${n2(-W * 0.12)} ${n2(-H * 0.05)} ${n2(-W * 0.08)} ${n2(-H * 0.13)} 0 ${n2(-H * 0.14)}c${n2(W * 0.08)} ${n2(H * 0.01)} ${n2(W * 0.12)} ${n2(H * 0.09)} 0 ${n2(H * 0.14)}z" fill="${l.accent}"/>`;
    else if (l.emblem === "line") s += `<rect x="${n2(cx - W * 0.16)}" y="${n2(H * 0.2)}" width="${n2(W * 0.32)}" height="0.35" fill="${l.accent}"/>`;
    const toks = titleTokens(title);
    if (toks.length) {
      const top = l.emblem !== "none" ? H * 0.33 : H * 0.2, bot = H * 0.8, areaH = bot - top, avail = W - strip - 1.8, fs0 = clamp(W * 0.14, 1.2, 3.2);
      const place = (h) => (l.tp === "b" ? bot - h : l.tp === "c" ? top + (areaH - h) / 2 : top);
      if (l.tl === "h" || l.tl === "s") {                                // 横排：最多三行，英文单词 / 数字不拆开
        const total = toks.reduce((a, t) => a + emW(t.s), 0);
        const lines = hLines(toks, total <= avail / fs0 ? 1 : total <= 2 * (avail / fs0) ? 2 : 3);
        const maxW = Math.max(...lines.map((x) => x.w));
        const fs = Math.min(fs0, avail / Math.max(1, maxW), areaH / (lines.length * 1.3)), y0 = place(lines.length * fs * 1.3);
        lines.forEach((ln, i) => { s += `<text x="${n2(cx)}" y="${n2(y0 + (i + 0.5) * fs * 1.3)}" dy=".36em" font-size="${n2(fs)}" font-family="${f}" font-weight="700" fill="${l.ink}" text-anchor="middle">${esc(ln.text)}</text>`; });
      } else {                                                           // 竖排：5 格以内一栏，再多两栏，更长三栏；栏从右往左读、顶端对齐、整块在封面正中
        const total = colLen(toks), cols = splitCols(toks, total <= 5.5 ? 1 : total <= 11 ? 2 : 3), C = Math.max(...cols.map(colLen));
        const fs = Math.min(fs0, areaH / (C * LH), avail / (cols.length * 1.3)), pitch = fs * 1.3, y0 = place(C * fs * LH);
        cols.forEach((col, i) => { s += vcolSvg(col, cx + ((cols.length - 1) / 2 - i) * pitch, y0, fs, f, l.ink, 700); });
      }
    }
    if (author) s += `<text x="${n2(cx)}" y="${n2(H * 0.86)}" font-size="${n2(Math.min(1.5, W * 0.085))}" font-family="${f}" fill="${l.ink}" fill-opacity=".7" text-anchor="middle">${esc(clipTitle(author, 10))}</text>`;
    return s;
  }
  /** 封面的独立小图（点开书架上的书看到的）：返回一个完整的 <svg>。 */
  function coverStandalone(look, title, author, px) {
    const l = bookDefaults(look), W = l.cw, H = l.h, k = (px || 220) / W;
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-0.6 -0.6 ${n2(W + 1.2)} ${n2(H + 1.2)}" width="${Math.round(W * k)}" height="${Math.round(H * k)}">`
      + `<rect x="-0.3" y="-0.3" width="${n2(W + 0.6)}" height="${n2(H + 0.6)}" rx="0.9" fill="${l.ink}" fill-opacity=".9"/><clipPath id="cvc"><rect x="0" y="0" width="${n2(W)}" height="${n2(H)}" rx="0.7"/></clipPath>`
      + `<g clip-path="url(#cvc)">${coverSvg(l, title, author)}</g></svg>`;
  }

  /* ───────────────── 书的方块：站着（书脊朝外）/ 躺着（堆在地上） ───────────────── */
  function bookObject(b, pose, x, y, z, yaw, g) {
    const l = bookDefaults(b.look);
    const o = pose === "stand" ? { kind: "box", w: l.w, d: l.cw, h: l.h } : { kind: "box", w: l.h, d: l.cw, h: l.w };
    Object.assign(o, { g, name: `book-${b.id}`, x, y, z, yaw: yaw || 0, col: l.main, book: b, pose, sharp: true, noRim: true, noHatch: true, noSticker: g !== "pile" });
    return o;
  }
  /** 书的装饰。站着：书脊画在左面（朝 +y），露在外面的侧面是封底（和封面同一套参数）；躺着：书脊横着画在左面，封面画在顶面。 */
  function bookDeco(o) {
    const l = bookDefaults(o.book.look), title = o.book.title;
    if (o.pose === "stand") {
      return [{ type: "raw", face: "left", svg: spineSvg(l, title) },
              { type: "raw", face: "right", svg: coverSvg(l, title, o.book.author) },
              { type: "raw", face: "top", svg: `<rect x="0" y="0" width="${n2(l.w)}" height="${n2(l.cw)}" fill="${l.accent}" fill-opacity=".5"/><rect x="${n2(l.w * 0.16)}" y="${n2(l.cw * 0.03)}" width="${n2(l.w * 0.8)}" height="${n2(l.cw * 0.94)}" fill="#f7f3e8"/>` }];
    }
    return [{ type: "raw", face: "left", svg: spineFlatSvg(l, title) },
            { type: "raw", face: "top", svg: `<g transform="translate(0 ${n2(l.cw)}) rotate(-90)">${coverSvg(l, title, o.book.author)}</g>` }];
  }

  /* ───────────────── 小物件：外观参数决定样子 ───────────────── */
  const PROP_DEFAULT = { plant: { shape: "plant", color: "#8fb7a8", accent: "#6f9a8d", pot: "#e8d9c4", size: 1 }, bear: { shape: "plush", color: "#cdb7a8", accent: "#a88f80", size: 1 },
    book: { shape: "stack", color: "#c8d4e8", accent: "#8fa5c8", size: 1 }, dress: { shape: "cloth", color: "#d8c5d2", accent: "#b89fb0", size: 1 }, game: { shape: "device", color: "#c8d4e8", accent: "#8fa5c8", size: 1 },
    lamp: { shape: "lamp", color: "#f0e2b8", accent: "#c9b078", size: 1 }, gift: { shape: "box", color: "#d6c3df", accent: "#b59cc4", size: 1 } };
  function propObjects(item, sx, sy, sz, st) {
    const look = Object.assign({}, PROP_DEFAULT[item.icon] || PROP_DEFAULT.gift, item.look || {}), k = clamp(+look.size || 1, 0.6, 1.5), g = "props";
    const col = look.color, acc = look.accent, O = [], id = `it-${item.id}`;
    const box = (name, x, y, z, w, d, h, c, extra) => O.push(Object.assign({ kind: "box", g, name: `${id}-${name}`, x, y, z, w, d, h, col: c, item, noRim: true, noHatch: true, rad: 3 }, extra || {}));
    const cyl = (name, cx, cy, z, rx, h, c) => O.push({ kind: "cyl", g, name: `${id}-${name}`, cx, cy, rx, ry: rx, z, h, col: c, item });
    const ball = (name, cx, cy, cz, r, c) => O.push({ kind: "ball", g, name: `${id}-${name}`, cx, cy, cz, r, col: c, item });
    switch (look.shape) {
      case "plant": cyl("pot", sx, sy, sz, 5 * k, 7 * k, look.pot || "#e8d9c4"); ball("leaf-1", sx - 2.5 * k, sy + 0.5, sz + 12 * k, 5.2 * k, col); ball("leaf-2", sx + 3 * k, sy - 1, sz + 14.5 * k, 4.6 * k, acc); ball("leaf-3", sx, sy + 1, sz + 18 * k, 3.8 * k, col); break;
      case "plush": ball("body", sx, sy, sz + 5 * k, 6.2 * k, col); ball("head", sx, sy, sz + 12.5 * k, 5.2 * k, col); ball("ear-l", sx - 3.6 * k, sy + 3.4 * k, sz + 17 * k, 2 * k, acc); ball("ear-r", sx + 3.6 * k, sy - 3.4 * k, sz + 17 * k, 2 * k, acc); break;
      case "stack": box("b1", sx - 6 * k, sy - 5 * k, sz, 12 * k, 10 * k, 3 * k, col); box("b2", sx - 5 * k, sy - 4.5 * k, sz + 3 * k, 10 * k, 9 * k, 2.6 * k, acc); box("b3", sx - 6.5 * k, sy - 5 * k, sz + 5.6 * k, 12.5 * k, 10 * k, 2.8 * k, col); break;
      case "cloth": box("c1", sx - 7 * k, sy - 5 * k, sz, 14 * k, 10 * k, 3 * k, col, { pill: false, deco: [{ type: "stripes", face: "top", dir: "v", gap: 3.2, color: acc, op: 0.5, w: 1.1 }] }); box("c2", sx - 6 * k, sy - 4.6 * k, sz + 3 * k, 12 * k, 9 * k, 3 * k, acc); break;
      case "device": box("d", sx - 6 * k, sy - 4 * k, sz, 12 * k, 8 * k, 3.2 * k, col, { deco: [{ type: "rect", face: "top", x: 2 * k, y: 1.6 * k, w: 8 * k, h: 4.8 * k, rx: 1, color: acc }, { type: "circle", face: "top", x: 3.2 * k, y: 6.9 * k, r: 0.7, color: acc }] }); break;
      case "lamp": cyl("base", sx, sy, sz, 3.6 * k, 2 * k, acc); cyl("stem", sx, sy, sz + 2 * k, 0.9 * k, 10 * k, acc); cyl("shade", sx, sy, sz + 12 * k, 4.8 * k, 5.5 * k, col); break;
      default: box("box", sx - 5 * k, sy - 5 * k, sz, 10 * k, 10 * k, 8 * k, col, { deco: [{ type: "rect", face: "left", x: 4.4 * k, y: 0, w: 1.2 * k, h: 8 * k, color: acc }, { type: "rect", face: "top", x: 4.4 * k, y: 0, w: 1.2 * k, h: 10 * k, color: acc }] }); box("lid", sx - 5.6 * k, sy - 5.6 * k, sz + 8 * k, 11.2 * k, 11.2 * k, 2.4 * k, acc); break;
    }
    return O;
  }

  /* ───────────────── 房间：家具（几何同 greybox.js 第 4 版，外观由风格 + 参数决定） ───────────────── */
  const GROUPS = [
    { id: "bed", label: "床" }, { id: "nightstand", label: "床头柜" }, { id: "deskshelf", label: "书桌" }, { id: "chair", label: "椅子" },
    { id: "wardrobe", label: "衣柜" }, { id: "window", label: "窗边" }, { id: "bookshelf", label: "书架" },
  ];
  const SHELF = { w: 120, x: ROOM.W - 16 - 120, d: 40, h: 225, base: 6, top: 4, bt: 3, dh: 75, slab: 4 };
  /** 书架的几个格子：{ z: 格子底面高度, h: 净高 }。第 0 格在桌面板下面，上面三格放书。 */
  function shelfCells() {
    const { base, top, bt, dh, slab, h } = SHELF, row = (h - top - dh - 2 * bt) / 3;
    return [{ z: base, h: dh - slab - base }, { z: dh, h: row }, { z: dh + row + bt, h: row }, { z: dh + 2 * row + 2 * bt, h: row }];
  }
  /** 家具上能放小物件的位置（x, y, z），按区域名。 */
  function propSlots() {
    const sx = SHELF.x, wx = sx - 30 - 100, T = SHELF.top;
    return {
      "窗边": [0, 1, 2, 3, 4].map((i) => [wx + 14 + i * 20, 10, 95]),
      "书桌": [0, 1, 2, 3, 4, 5].map((i) => [172 + (i % 3) * 38, 22 + Math.floor(i / 3) * 30, 75]),
      "床": [[60, 280, 46], [60, 340, 46], [110, 300, 52], [150, 335, 52]],
      "床头柜": [[24, 226, 50]],
      "衣柜": [[30, 30, 210], [66, 30, 210], [102, 30, 210]],
      "书架": [0, 1, 2, 3].map((i) => [sx + 18 + i * 28, 20, SHELF.h]),
    };
  }

  /** 构建场景。opts：{ furniture: {bed:{...}, ...}, books: {shelf:[], floor:[]}, basket: {count}, items: [...], chair: true } */
  function buildScene(opts, st) {
    opts = opts || {};
    const pal = st.pal, fu = opts.furniture || {}, O = [];
    const { W, D, H, T, F } = ROOM;
    const box = (g, name, x, y, z, w, d, h, col, extra) => { const o = Object.assign({ kind: "box", g, name, x, y, z, w, d, h, col }, extra || {}); O.push(o); return o; };
    const cyl = (g, name, cx, cy, z, rx, ry, h, col) => O.push({ kind: "cyl", g, name, cx, cy, rx, ry, z, h, col });
    const detail = st.detail;

    box("shell", "floor", -T, -T, -F, W + T, D + T, F, pal.floor, { rad: 6, noRim: true, deco: [{ type: "stripes", face: "top", dir: "h", gap: 50, color: pal.floorLine, op: 0.6, w: 1, off: 0 }, { type: "stripes", face: "top", dir: "v", gap: 50, color: pal.floorLine, op: 0.6, w: 1, off: 0 }] });
    box("shell", "wall-n", -T, -T, 0, W + T, T, H, pal.wallN, { sharp: true, noRim: true, noHatch: true });
    box("shell", "wall-w", -T, 0, 0, T, D, H, pal.wallW, { sharp: true, noRim: true, noHatch: true });
    box("shell", "trim-n", 0, 0, 0, W, 1.5, 8, pal.trim, { sharp: true, noRim: true, noHatch: true, noSticker: true });
    box("shell", "trim-w", 0, 0, 0, 1.5, D, 8, pal.trim, { sharp: true, noRim: true, noHatch: true, noSticker: true });

    { // 床
      const f = Object.assign({ frame: pal.bedFrame, head: pal.bedHead, mattress: pal.mattress, pillow: pal.pillow, blanket: pal.blanket, blanketB: pal.blanketB, pattern: detail >= 3 ? "stitch" : detail === 2 ? "dots" : "stripes" }, fu.bed || {});
      const bx = 6, bl = 205, bd = 120, by = D - bd - 30, hb = 10, mx = bx + hb, ml = bl - hb - 4, my = by + 4, md = bd - 8;
      box("bed", "headboard", bx, by, 0, hb, bd, 85, f.head, { rad: 11, deco: detail >= 2 ? [{ type: "rect", face: "right", x: 10, y: 10, w: bd - 20, h: 22, rx: 5, color: tone(f.head, 0.07, 0), op: 0.9 }] : [] });
      box("bed", "frame", mx, by, 0, bl - hb, bd, 26, f.frame, { rad: 7, deco: detail >= 3 ? [{ type: "stripes", face: "left", dir: "v", gap: 24, color: tone(f.frame, -0.1, 6), op: 0.4, w: 1 }] : [] });
      box("bed", "mattress", mx, my, 26, ml, md, 20, f.mattress, { rad: 8, deco: [{ type: "scallop", face: "left", y: 20, gap: 12, color: tone(f.mattress, -0.12, 6), op: 0.7, min: 3 }] });
      box("bed", "pillow-1", mx + 5, my + 5, 46, 30, 48, 10, f.pillow, { pill: true, deco: detail >= 3 ? [{ type: "stitch", face: "top", inset: 3.5, color: "#9db3d4", op: 0.7 }] : [] });
      box("bed", "pillow-2", mx + 5, my + 59, 46, 30, 48, 10, f.pillow, { pill: true, deco: detail >= 3 ? [{ type: "stitch", face: "top", inset: 3.5, color: "#9db3d4", op: 0.7 }] : [] });
      const bw = ml - 62, bdp = md - 4;
      const deco = [];
      if (f.pattern === "stripes") deco.push({ type: "stripes", face: "top", dir: "v", gap: 18, color: f.blanketB, op: 0.95, w: 8 });
      else if (f.pattern === "gingham") deco.push({ type: "gingham", face: "top", gap: 16, color: f.blanketB });
      else if (f.pattern === "dots") deco.push({ type: "dots", face: "top", gap: 14, r: 2.2, color: f.blanketB, op: 0.95 }, { type: "star", face: "top", x: bw * 0.72, y: bdp * 0.3, r: 4.4, color: "#fff", op: 0.9, min: 2 }, { type: "star", face: "top", x: bw * 0.35, y: bdp * 0.7, r: 3.4, color: "#fff", op: 0.9, min: 2 });
      else if (f.pattern === "stitch") deco.push({ type: "stripes", face: "top", dir: "v", gap: 22, color: f.blanketB, op: 0.8, w: 9 }, { type: "stitch", face: "top", inset: 4, color: "#fff", op: 0.85, sw: 0.9 });
      deco.push({ type: "scallop", face: "left", y: 6, gap: 11, color: tone(f.blanket, -0.14, 6), op: 0.8, min: 1 });
      box("bed", "blanket", mx + 60, my + 2, 46, bw, bdp, 6, f.blanket, { rad: 5, deco });
    }

    { // 床头柜
      const f = Object.assign({ body: pal.nsBody, top: pal.nsTop, drawer: pal.drawer, knob: pal.knob }, fu.nightstand || {});
      const bx = 6, by = D - 120 - 30, nx = bx, nd = 40, nw = 42, ny = by - 4 - nw;
      box("nightstand", "feet", nx + 3, ny + 3, 0, nd - 6, nw - 6, 6, tone(f.body, -0.1, 6), { rad: 3 });
      box("nightstand", "body", nx, ny, 6, nd, nw, 40, f.body, { rad: 6 });
      box("nightstand", "top", nx, ny - 1.5, 46, nd + 1.5, nw + 3, 4, f.top, { rad: 5 });
      box("nightstand", "drawer-1", nx + nd, ny + 3, 10, 1.5, nw - 6, 15, f.drawer, { rad: 3, noRim: true, deco: [{ type: "circle", face: "right", x: (nw - 6) / 2, y: 7.5, r: 1.6, color: f.knob, stroke: st.ink, sw: 0.5 }] });
      box("nightstand", "drawer-2", nx + nd, ny + 3, 28, 1.5, nw - 6, 15, f.drawer, { rad: 3, noRim: true, deco: [{ type: "circle", face: "right", x: (nw - 6) / 2, y: 7.5, r: 1.6, color: f.knob, stroke: st.ink, sw: 0.5 }] });
    }

    { // 书桌 + 书架
      const f = Object.assign({ top: pal.deskTop, leg: pal.deskLeg, shelf: pal.shelf, board: pal.shelfBoard, back: pal.shelfBack, shelfTop: pal.shelfTop }, fu.desk || {});
      const bw = SHELF.w, bx = SHELF.x, bd = SHELF.d, sh = SHELF.h, base = SHELF.base, top = SHELF.top, bt = SHELF.bt, dh = SHELF.dh, slab = SHELF.slab;
      const dx = 150, dw = bx - dx, dd = 75;
      box("deskshelf", "desk-top", dx, 0, dh - slab, dw, dd, slab, f.top, { rad: 6, deco: detail >= 3 ? [{ type: "grain", face: "top", color: tone(f.top, -0.13, 6), op: 0.7, n: 7, seed: 11 }] : detail === 2 ? [{ type: "dots", face: "top", gap: 22, r: 1.1, color: "#fff", op: 0.7 }] : [] });
      box("deskshelf", "desk-panel", dx, 3, 0, 4, dd - 6, dh - slab, f.leg, { rad: 2 });
      box("deskshelf", "desk-post", bx - 6, dd - 8, 0, 5, 5, dh - slab, f.leg, { rad: 2 });
      box("bookshelf", "base", bx, 0, 0, bw, bd, base, tone(f.shelf, -0.08, 6), { rad: 3 });
      box("bookshelf", "side-l", bx, 0, base, 4, bd, sh - base - top, f.shelf, { rad: 2 });
      box("bookshelf", "side-r", bx + bw - 4, 0, base, 4, bd, sh - base - top, f.shelf, { rad: 2 });
      box("bookshelf", "back", bx + 4, 0, base, bw - 8, 1.5, sh - base - top, f.back, { sharp: true, noRim: true, noHatch: true });
      const row = (sh - top - dh - 2 * bt) / 3;
      [[dh - slab, slab], [dh + row, bt], [dh + 2 * row + bt, bt]].forEach(([z, t], i) => box("bookshelf", `board-${i + 1}`, bx + 4, 1.5, z, bw - 8, bd - 1.5, t, f.board, { rad: 1.5, noRim: true }));
      box("bookshelf", "top", bx, 0, sh - top, bw, bd, top, f.shelfTop, { rad: 3 });
    }

    if (opts.chair !== false) { // 椅子
      const f = Object.assign({ seat: pal.chairSeat, leg: pal.chairLeg, back: pal.chairBack }, fu.chair || {});
      const dx = 150, dw = SHELF.x - dx, dd = 75, cw = 42, cd = 40, cx = dx + dw / 2 - cw / 2, cy = dd - 13, seatZ = 43, backZ = 90;
      box("chair", "seat", cx, cy, seatZ, cw, cd, 4, f.seat, { rad: 5, deco: detail >= 2 ? [{ type: "stitch", face: "top", inset: 4, color: "#fff", op: 0.7 }] : [] });
      [[0, 0], [cw - 3.5, 0], [0, cd - 3.5], [cw - 3.5, cd - 3.5]].forEach(([ox, oy], i) => box("chair", `leg-${i}`, cx + ox, cy + oy, 0, 3.5, 3.5, seatZ, f.leg, { rad: 1.5, noRim: true }));
      [0, cw - 3.5].forEach((ox, i) => box("chair", `post-${i}`, cx + ox, cy + cd - 3.5, seatZ + 4, 3.5, 3.5, backZ - seatZ - 4, f.leg, { rad: 1.5, noRim: true }));
      box("chair", "backrest", cx + 3.5, cy + cd - 3, 62, cw - 7, 3, 24, f.back, { rad: 4 });
    }

    { // 衣柜
      const f = Object.assign({ body: pal.wardrobe, door: pal.wardDoor, cornice: pal.cornice, knob: pal.knob }, fu.wardrobe || {});
      const wx = 2, ww = 130, wd = 60;
      box("wardrobe", "feet", wx + 3, 3, 0, ww - 6, wd - 6, 8, tone(f.body, -0.1, 6), { rad: 3 });
      box("wardrobe", "body", wx, 0, 8, ww, wd, 194, f.body, { rad: 7 });
      box("wardrobe", "cornice", wx - 2, 0, 202, ww + 4, wd + 2, 8, f.cornice, { rad: 5 });
      const knob = (x) => ({ type: "circle", face: "left", x, y: 90, r: 2.2, color: f.knob, stroke: st.ink, sw: 0.6 });
      box("wardrobe", "door-l", wx + 3, wd, 14, 60, 1.5, 182, f.door, { rad: 4, noRim: true, deco: [{ type: "stitch", face: "left", inset: 6, color: tone(f.door, -0.12, 6), op: 0.9, sw: 0.9, dash: "100 0", rx: 3, min: 2 }, knob(54)] });
      box("wardrobe", "door-r", wx + 67, wd, 14, 60, 1.5, 182, f.door, { rad: 4, noRim: true, deco: [{ type: "stitch", face: "left", inset: 6, color: tone(f.door, -0.12, 6), op: 0.9, sw: 0.9, dash: "100 0", rx: 3, min: 2 }, knob(6)] });
    }

    { // 窗 + 窗帘
      const f = Object.assign({ frame: pal.frame, glass: pal.glass, curtain: pal.curtain, rod: pal.rod }, fu.window || {});
      const ww = 100, wx = SHELF.x - 30 - ww, zb = 95, zt = 195, fh = zt - zb, bar = 5, ih = fh - 2 * bar;
      box("window", "sill", wx - 8, 0, zb - 9, ww + 16, 16, 9, f.frame, { rad: 3 });
      box("window", "pane", wx + bar, 0, zb + bar, ww - 2 * bar, 0.8, ih, f.glass, { sharp: true, noRim: true, noHatch: true, deco: [{ type: "stripes", face: "left", dir: "v", gap: 22, color: "#fff", op: 0.5, w: 6, off: 6 }] });
      box("window", "frame-bottom", wx, 0, zb, ww, 4, bar, f.frame, { rad: 2, noRim: true });
      box("window", "frame-top", wx, 0, zt - bar, ww, 4, bar, f.frame, { rad: 2, noRim: true });
      box("window", "frame-l", wx, 0, zb + bar, bar, 4, ih, f.frame, { rad: 2, noRim: true });
      box("window", "frame-r", wx + ww - bar, 0, zb + bar, bar, 4, ih, f.frame, { rad: 2, noRim: true });
      box("window", "mullion-v", wx + ww / 2 - 1.5, 0.8, zb + bar, 3, 3.2, ih, f.frame, { sharp: true, noRim: true, noHatch: true });
      const half = (ww - 2 * bar - 3) / 2;
      box("window", "mullion-h-l", wx + bar, 0.8, zt - 0.55 * fh - 1.5, half, 3.2, 3, f.frame, { sharp: true, noRim: true, noHatch: true });
      box("window", "mullion-h-r", wx + ww / 2 + 1.5, 0.8, zt - 0.55 * fh - 1.5, half, 3.2, 3, f.frame, { sharp: true, noRim: true, noHatch: true });
      const c1 = wx - 26, c2 = wx + ww + 2;
      const cd = detail >= 2 ? [{ type: "stripes", face: "left", dir: "v", gap: 8, color: tone(f.curtain, -0.1, 6), op: 0.5, w: 1.1 }] : [];
      box("window", "curtain-l", c1, 10, 82, 24, 6, 130, f.curtain, { rad: 5, deco: cd });
      box("window", "curtain-r", c2, 10, 82, 24, 6, 130, f.curtain, { rad: 5, deco: cd });
      box("window", "rod", c1 - 2, 11, 212, c2 + 24 - c1 + 4, 3, 3, f.rod, { rad: 2, noRim: true });
    }

    // 书架上的书（书脊朝外）、地上的书堆、收纳篮
    const bk = opts.books || {};
    if (bk.shelf && bk.shelf.length) layoutShelfBooks(bk.shelf, O);
    if (bk.floor && bk.floor.length) layoutFloorBooks(bk.floor, O);
    if (opts.basket) {
      const f = { body: pal.basket, rim: pal.basketB };
      const bx = 376, by = 50, bw = 44, bd = 38, bh = 28;
      box("basket", "basket", bx, by, 0, bw, bd, bh, f.body, { rad: 6, deco: [{ type: "stripes", face: "left", dir: "h", gap: 5, color: f.rim, op: 0.7, w: 1.4 }, { type: "stripes", face: "right", dir: "h", gap: 5, color: f.rim, op: 0.7, w: 1.4 }, { type: "stripes", face: "top", dir: "v", gap: 5, color: f.rim, op: 0.5, w: 1.2 }] });
      box("basket", "rim", bx - 1.5, by - 1.5, bh - 1, bw + 3, bd + 3, 4, f.rim, { rad: 4, noRim: true });
      (bk.storage || []).slice(0, 5).forEach((b, i) => O.push(bookObject(b, "stand", bx + 5 + i * 7.5, by + 8, bh + 2, 0, "basket")));
    }
    // 家具上的小物件
    if (opts.items && opts.items.length) {
      const slots = propSlots(), used = {};
      opts.items.forEach((it) => {
        const list = slots[it.location];
        if (!list) return;
        const i = used[it.location] || 0;
        if (i >= list.length) return;
        used[it.location] = i + 1;
        const [sx, sy, sz] = list[i];
        propObjects(it, sx, sy, sz, st).forEach((p) => O.push(p));
      });
    }
    return O;
  }

  /** 书架上的书：一格一格排（先排桌面板上面的三格，最后才用最下面那格），每格从左往右；书脊朝外（朝 +y）。 */
  function layoutShelfBooks(books, O) {
    const all = shelfCells(), cells = all.slice(1).concat(all.slice(0, 1)), x0 = SHELF.x + 6, x1 = SHELF.x + SHELF.w - 6;
    let ci = 0, cx = x0;
    for (const b of books) {
      const l = bookDefaults(b.look);
      while (ci < cells.length && (cx + l.w > x1 || l.h > cells[ci].h - 1)) { ci++; cx = x0; }
      if (ci >= cells.length) break;
      const cd = Math.min(l.cw, SHELF.d - 5);
      O.push(bookObject(b, "stand", cx, SHELF.d - cd - 1.5, cells[ci].z, 0, "books"));
      cx += l.w + 0.25;
    }
  }
  /** 地上的书：书桌下面靠墙，几摞横着叠，略显凌乱（每本有个小偏移和小角度）。 */
  function layoutFloorBooks(books, O) {
    let seed = 20261022;
    const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
    const piles = [[258, 44], [284, 30], [262, 10], [300, 8], [184, 44], [172, 14]];
    const per = Math.max(1, Math.ceil(books.length / Math.min(piles.length, Math.ceil(books.length / 5))));
    let pi = 0, z = 0, count = 0;
    books.forEach((b) => {
      if (count >= per) { pi = Math.min(pi + 1, piles.length - 1); z = 0; count = 0; }
      const l = bookDefaults(b.look), p = piles[pi], jx = (rnd() - 0.5) * 7, jy = (rnd() - 0.5) * 6, yaw = (rnd() - 0.5) * 34;
      O.push(bookObject(b, "lie", p[0] + jx, p[1] + jy, z, yaw, "pile"));
      z += l.w;
      count++;
    });
  }

  /* ───────────────── 画整个场景 ───────────────── */
  const drawObj = (o, P, st, sc) => (o.kind === "box" ? (o.yaw ? drawPrism(o, P, st, sc) : drawBox(o, P, st, sc)) : o.kind === "ball" ? drawBall(o, P, st, sc) : drawCyl(o, P, st, sc));
  const ballAsCyl = (o) => ({ kind: "cyl", cx: o.cx, cy: o.cy, rx: o.r, ry: o.r, z: o.cz - o.r, h: 2 * o.r });

  /** 地上的软影子：家具的投影（光从左上，影子偏右下）。整组一个半透明块，重叠的地方不会更深。 */
  const SHADOW_GROUPS = ["bed", "nightstand", "deskshelf", "bookshelf", "chair", "wardrobe", "basket", "pile"];
  function shadowEntry(scene, P, st) {
    const dx = 6, dy = 3, polys = [];
    scene.forEach((o) => {
      if (!SHADOW_GROUPS.includes(o.g)) return;
      if ((o.g === "pile" || o.g === "basket") && o.z > 1) return;
      let fp;
      if (o.kind === "ball") return;
      if (o.kind === "cyl") fp = Array.from({ length: 14 }, (_, i) => [o.cx + o.rx * Math.cos((i / 14) * Math.PI * 2) + dx, o.cy + o.ry * Math.sin((i / 14) * Math.PI * 2) + dy]);
      else if (o.yaw) fp = prismCorners(o).map((c) => [c[0] + dx, c[1] + dy]);
      else fp = [[o.x + dx, o.y + dy], [o.x + o.w + dx, o.y + dy], [o.x + o.w + dx, o.y + o.d + dy], [o.x + dx, o.y + o.d + dy]];
      polys.push(`<polygon points="${fp.map((c) => { const q = P(c[0], c[1], 0); return `${n2(q[0])},${n2(q[1])}`; }).join(" ")}"/>`);
    });
    if (!polys.length) return null;
    return { o: { name: "shadow", g: "shadow", shadow: true }, svg: `<g opacity="${st.shadow == null ? 0.16 : st.shadow}"><g fill="${st.ink}" stroke="${st.ink}" stroke-width="1.5" stroke-linejoin="round">${polys.join("")}</g></g>`, b: null };
  }

  /** 依次画好；贴纸风格（B）按「组」垫一圈白边：一组家具外面只有一圈，不是每个零件都勾。 */
  function buildOrder(sorted, P, st, sc) {
    const order = [], hot = {}, halos = {};
    let pts = [];
    sorted.forEach((o) => {
      const r = drawObj(o, P, st, sc), b = bounds(o);
      if (r.halo && o.g !== "shell") {
        let h = halos[o.g];
        if (!h) { h = halos[o.g] = { entry: { o: { name: `halo-${o.g}`, g: o.g, halo: true }, svg: "", b }, parts: [] }; order.push(h.entry); }
        h.parts.push(r.halo);
      }
      order.push({ o, svg: r.svg, b });
      pts = pts.concat(r.pts);
      if (o.g && o.g !== "shell") (hot[o.g] = hot[o.g] || []).push(...r.pts);
    });
    Object.values(halos).forEach((h) => { h.entry.svg = h.parts.join(""); });
    return { order, pts, hot };
  }
  function frame(pts, sc, padPx) {
    const pad = padPx * (sc / 1.6), x0 = Math.min(...pts.map((q) => q[0])) - pad, y0 = Math.min(...pts.map((q) => q[1])) - pad;
    const w = Math.max(...pts.map((q) => q[0])) + pad - x0, h = Math.max(...pts.map((q) => q[1])) + pad - y0;
    return { viewBox: [n2(x0), n2(y0), n2(w), n2(h)], width: Math.ceil(w), height: Math.ceil(h) };
  }

  /** 返回 { order:[{o, svg, b}], viewBox, width, height, P, hotspots, st }。order 是画家算法排好的顺序，房间页面据此把角色插进去。 */
  function renderScene(opts, styleId, scale) {
    const st = styleOf(styleId), sc = scale || 1.6, P = camera(sc).P;
    const scene = buildScene(opts, st);
    scene.forEach((o) => { if (o.book) o.deco = bookDeco(o); });
    const { order, pts, hot } = buildOrder(sortObjects(scene, P), P, st, sc);
    order.forEach((e) => { if (e.o.kind === "ball") e.b = bounds(ballAsCyl(e.o)); });
    const sh = shadowEntry(scene, P, st);
    if (sh) { const i = order.findIndex((e) => e.o.g !== "shell"); order.splice(i < 0 ? order.length : i, 0, sh); }
    const hotspots = {};
    Object.keys(hot).forEach((g) => { hotspots[g] = hull(hot[g]); });
    return Object.assign({ order, P, hotspots, st, scale: sc, scene }, frame(pts, sc, 14));
  }
  const svgBody = (r) => `<g stroke-linejoin="round" stroke-linecap="round">${r.order.map((e) => `<g data-n="${e.o.name}" data-g="${e.o.g || ""}">${e.svg}</g>`).join("")}</g>`;
  function toSvg(r, extra) {
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${r.viewBox.join(" ")}" width="${r.width}" height="${r.height}">${svgBody(r)}${extra || ""}</svg>`;
  }
  /** 只画某几组家具（风格小样用）：不要后墙 / 地板。 */
  function renderGroups(groups, opts, styleId, scale) {
    const st = styleOf(styleId), sc = scale || 1.6, P = camera(sc).P;
    const scene = buildScene(opts, st).filter((o) => groups.includes(o.g));
    scene.forEach((o) => { if (o.book) o.deco = bookDeco(o); });
    const { order, pts } = buildOrder(sortObjects(scene, P), P, st, sc), f = frame(pts, sc, 12), sh = shadowEntry(scene, P, st);
    if (sh) order.unshift(sh);
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${f.viewBox.join(" ")}" width="${f.width}" height="${f.height}"><g stroke-linejoin="round" stroke-linecap="round">${order.map((e) => `<g>${e.svg}</g>`).join("")}</g></svg>`;
  }

  const API = { ROOM, SHELF, STYLES, GROUPS, styleOf, camera, faceMatrix, bounds, behind, sortObjects, buildScene, renderScene, renderGroups, toSvg, svgBody, shelfCells, propSlots,
    bookDefaults, spineSvg, spineFlatSvg, coverSvg, coverStandalone, head8, patternSvg, tone, mix, hull, esc };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.PhalIso = API;
})(typeof window !== "undefined" ? window : globalThis);
