/* 灰模渲染器：同一份场景（凸多面体）可以用两种相机画——书桌视角（透视）和房间视角（等距）。
 * 输出透明背景的 SVG，平涂 + 深色描边，光从左上。
 *
 * 【房间页面用的副本】从灰模原型 greyBoxTest/persp.js（不在仓库里）复制（2026-10-24），改动只有：
 *   · 全局名改成 RoomPersp（不和别的撞名）；
 *   · cyl / 球体 带 smooth 标记（风格渲染时不画侧面的棱线）；
 *   · 多导出了 before(cam)（角色插进画面顺序时用）、ball、clipPoly。
 * greyBoxTest 里的原文件不动；灰模改了以后，把新版和这份对一下再同步。
 *
 * 世界坐标（cm）就是书桌相机的坐标：x 向右、y 向前（离相机越来越远，窗墙在 y=262）、z 向上。原点 = 书桌相机所在的地面点。
 * 房间的方位：窗户在南墙，所以书桌相机朝南看；+y = 南、-y = 北；相机朝南时右手边是西，所以 +x = 西、-x = 东。
 *  - 书桌相机：针孔，在 (0, 0, 100)，水平看向南（+y）——可以绕自己所在的竖直轴转 yaw 度（正数 = 逆时针 = 向东转），不俯仰、不歪头，f=1000px（水平视角 90°）。
 *    画面坐标直接用参考图的像素：2000×1194，上面 27px 是窗口标题栏，场景区域是 y=27..1194，主点在 (1000, 610.5)。
 *    相机在北墙（y=30）外面，像游戏里一样看不到这面墙。
 *  - 房间相机：正交，真等距（轴 30°、三轴等长），从东南上方看向西北，能看到北墙和西墙的内侧，前面（南、东）两面墙拆掉。
 */
(function (root) {
  "use strict";

  const CAM = { f: 1000, cx: 1000, cy: 610.5, x: 0, y: 0, z: 100 };   // 书桌相机
  const VIEW = { x: 0, y: 27, w: 2000, h: 1167 };                      // 书桌视角的画面（去掉标题栏）
  const NEAR = 2;                                                      // 近平面：相机前方 2cm 之前的东西裁掉
  const INK = "#262a36";
  const n2 = (v) => Math.round(v * 100) / 100;
  const hex = (t) => { const v = Math.round(Math.max(0, Math.min(1, t)) * 255).toString(16).padStart(2, "0"); return `#${v}${v}${v}`; };

  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const unit = (a) => { const l = Math.hypot(...a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
  // 光永远从画面左上来：每个相机各有一个「朝向光源的方向」。
  // 书桌相机：向左（-x，东）、略偏靠相机（-y，北）、上；房间相机（从东南看）：画面左 = 南，所以向南（+y）、略偏向西（+x）、上。
  const LIGHT_DESK = unit([-0.5, -0.35, 0.8]);
  const LIGHT_ISO = unit([0.5, 0.35, 0.8]);

  /* ───────────────── 相机 ───────────────── */
  /** 书桌相机（透视）。yaw：绕相机所在的竖直轴转的角度（度）；0 = 正对南墙（对着参考图），
   *  正数 = 从上往下看逆时针 = 向东转（朝南看时就是往画面左边转）。
   *  x：相机横向（东西方向）的位置，沿北墙外面平移：+x = 往西、-x = 往东，默认 CAM.x = 0。高度、焦距、不俯仰都不变。
   *  F：水平朝向；R：画面右手边（水平）；光永远按画面左上打，所以光的方向也跟着 F、R 走（yaw=0 时就是 LIGHT_DESK）。 */
  function deskCamera({ yaw = 0, x = CAM.x } = {}) {
    const pos = [x, CAM.y, CAM.z];
    const a = (yaw * Math.PI) / 180, s = Math.sin(a), c = Math.cos(a);
    const F = [-s, c], R = [c, s];
    const depth = (p) => (p[0] - x) * F[0] + (p[1] - CAM.y) * F[1];
    return {
      kind: "persp", pos, view: VIEW, stroke: 2.2, yaw, F, R, depth,
      light: unit([-0.5 * R[0] - 0.35 * F[0], -0.5 * R[1] - 0.35 * F[1], 0.8]),
      project: (p) => { const d = Math.max(depth(p), NEAR); return [CAM.cx + (CAM.f * ((p[0] - x) * R[0] + (p[1] - CAM.y) * R[1])) / d, CAM.cy - (CAM.f * (p[2] - CAM.z)) / d]; },
      toCam: (p) => sub(pos, p),
      clip: (vs) => clipDepth(vs, depth, NEAR),
    };
  }

  /** 房间相机（等距），从东南上方看向西北：东 → 画面右下，南 → 画面左下，能看到北墙、西墙的内侧。
   *  场景坐标是书桌相机的坐标：+x 向西、+y 向南（朝窗墙）；所以东 = -x、北 = -y。
   *  westX：西墙内侧的 x；northY：北墙内侧的 y；scale：1cm 对应多少像素。 */
  function isoCamera({ westX, northY, scale = 1.2, view = "iso" }) {
    const el = view === "2:1" ? Math.PI / 6 : Math.asin(1 / Math.sqrt(3));
    const s = Math.sin(el), c = Math.cos(el), r = Math.SQRT1_2;
    const v = [-r * c, r * c, s];                                      // 从场景指向相机的方向（东南上 = -x、+y、上）
    return {
      kind: "ortho", pos: v.map((t) => t * 1e5), view: null, stroke: 1.5, pad: 14, scale, light: LIGHT_ISO,
      // xo：从西墙往东量；yo：从北墙往南量
      project: (p) => { const xo = westX - p[0], yo = p[1] - northY; return [(xo - yo) * r * scale, ((xo + yo) * r * s - p[2] * c) * scale]; },
      toCam: () => v,
      clip: (vs) => vs,
      axes: { x: Math.atan2(r * s, r) * 180 / Math.PI },
    };
  }

  /** 把多边形（3D 顶点）裁到「深度（沿相机朝向量）≥ near」这一侧。 */
  function clipDepth(vs, depth, near) {
    const out = [];
    vs.forEach((a, i) => {
      const b = vs[(i + 1) % vs.length], da = depth(a), db = depth(b), ia = da >= near, ib = db >= near;
      if (ia) out.push(a);
      if (ia !== ib) { const t = (near - da) / (db - da); out.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])]); }
    });
    return out;
  }

  /* ───────────────── 形体：都是凸多面体（顶点 + 面） ───────────────── */
  // fp：地面上的占地多边形（斜放的形体才需要，查穿插时用；不给就按外包盒算）
  function mesh(g, name, tone, verts, faces, fp) {
    const bb = [Infinity, -Infinity, Infinity, -Infinity, Infinity, -Infinity];
    verts.forEach((v) => { for (let k = 0; k < 3; k++) { bb[2 * k] = Math.min(bb[2 * k], v[k]); bb[2 * k + 1] = Math.max(bb[2 * k + 1], v[k]); } });
    return { g, name, tone, verts, faces, bb, fp };
  }

  /** 台体：底面 x0~x1 × y0~y1（z0），顶面按中心缩放 k（z1）。k=1 就是方块。 */
  function frustum(g, name, tone, x0, x1, y0, y1, z0, z1, k = 1) {
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, hx = ((x1 - x0) / 2) * k, hy = ((y1 - y0) / 2) * k;
    const v = [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [cx - hx, cy - hy, z1], [cx + hx, cy - hy, z1], [cx + hx, cy + hy, z1], [cx - hx, cy + hy, z1]];
    return mesh(g, name, tone, v, [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]);
  }

  /** 斜放的方块：中心 (cx,cy)，朝向 f=[fx,fy]（单位向量），沿右手边的半宽 hx、沿朝向的半长 hy；顶面按中心缩放 k。
   *  右手边 r = [fy, -fx]（面朝 f 的人，右手伸向的方向）。 */
  function rbox(g, name, tone, cx, cy, hx, hy, f, z0, z1, k = 1) {
    const r = [f[1], -f[0]];
    const ring = (s, z) => [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([a, b]) => [cx + s * (a * hx * r[0] + b * hy * f[0]), cy + s * (a * hx * r[1] + b * hy * f[1]), z]);
    const v = ring(1, z0).concat(ring(k, z1));
    return mesh(g, name, tone, v, [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]], v.slice(0, 4).map((p) => [p[0], p[1]]));
  }

  /** n 边形棱柱 / 棱台（圆柱的低模）：中心 (cx,cy)，半径 rx、ry，顶面半径 × k。 */
  function cyl(g, name, tone, cx, cy, rx, ry, z0, z1, n = 10, k = 1) {
    const v = [];
    for (let i = 0; i < n; i++) { const a = (i / n) * Math.PI * 2; v.push([cx + rx * Math.cos(a), cy + ry * Math.sin(a), z0]); }
    for (let i = 0; i < n; i++) { const a = (i / n) * Math.PI * 2; v.push([cx + rx * k * Math.cos(a), cy + ry * k * Math.sin(a), z1]); }
    const f = [Array.from({ length: n }, (_, i) => i), Array.from({ length: n }, (_, i) => n + i)];
    for (let i = 0; i < n; i++) f.push([i, (i + 1) % n, n + ((i + 1) % n), n + i]);
    const m = mesh(g, name, tone, v, f);
    m.smooth = true;                                                    // 圆柱的侧面不画棱线
    return m;
  }

  /** 低模球（赤道 + 上下 45° 两圈，n 条经线）：毛绒玩具、叶子用。中心 (cx,cy,cz)，半径 r。 */
  function ball(g, name, tone, cx, cy, cz, r, n = 8) {
    const ring = (lat, rr) => Array.from({ length: n }, (_, i) => { const a = (i / n) * Math.PI * 2; return [cx + rr * Math.cos(a), cy + rr * Math.sin(a), cz + lat]; });
    const s = Math.SQRT1_2, v = [].concat(ring(-r * s, r * s), ring(0, r), ring(r * s, r * s), [[cx, cy, cz - r], [cx, cy, cz + r]]);
    const f = [], bot = 3 * n, top = 3 * n + 1;
    for (let k = 0; k < 2; k++) for (let i = 0; i < n; i++) f.push([k * n + i, k * n + ((i + 1) % n), (k + 1) * n + ((i + 1) % n), (k + 1) * n + i]);
    for (let i = 0; i < n; i++) { f.push([bot, (i + 1) % n, i]); f.push([top, 2 * n + i, 2 * n + ((i + 1) % n)]); }
    const m = mesh(g, name, tone, v, f);
    m.smooth = true;
    return m;
  }

  /* ───────────────── 画家算法：先画远的，再画近的 ───────────────── */
  /** a 不可能挡住 b（a 先画）：某个轴上，a、b 被一张平面隔开，而且相机和 b 在同一侧。 */
  function makeBefore(cam) {
    const E = 1e-6, c = cam.pos;
    return (a, b) => {
      for (let k = 0; k < 3; k++) {
        if (a.bb[2 * k + 1] <= b.bb[2 * k] + E && c[k] >= a.bb[2 * k + 1] - E) return true;
        if (b.bb[2 * k + 1] <= a.bb[2 * k] + E && c[k] <= a.bb[2 * k] + E) return true;
      }
      return false;
    };
  }

  function order(objs, cam) {
    const E = 1e-6, c = cam.pos;
    // 每个形体在画面上的外包框：先按相机裁掉背后的部分（相机转了以后，西边的东西会有一部分在相机背后）
    const sb = objs.map((o) => {
      const xs = [], ys = [];
      o.faces.forEach((f) => cam.clip(f.map((i) => o.verts[i])).forEach((v) => { const p = cam.project(v); xs.push(p[0]); ys.push(p[1]); }));
      return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
    });
    const before = makeBefore(cam);
    const n = objs.length, out = objs.map(() => []), indeg = Array(n).fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (sb[i][2] <= sb[j][0] || sb[j][2] <= sb[i][0] || sb[i][3] <= sb[j][1] || sb[j][3] <= sb[i][1]) continue;
        const ij = before(objs[i], objs[j]), ji = before(objs[j], objs[i]);
        if (ij === ji) continue;
        const [a, b] = ij ? [i, j] : [j, i];
        out[a].push(b);
        indeg[b]++;
      }
    }
    const far = (i) => { const b = objs[i].bb; return Math.hypot((b[0] + b[1]) / 2 - c[0], (b[2] + b[3]) / 2 - c[1], (b[4] + b[5]) / 2 - c[2]); };
    const res = [], left = new Set(objs.keys());
    while (left.size) {
      let pick = -1;
      for (const i of left) if (indeg[i] === 0 && (pick < 0 || far(i) > far(pick))) pick = i;
      if (pick < 0) for (const i of left) if (pick < 0 || far(i) > far(pick)) pick = i;      // 有环：先画最远的
      left.delete(pick);
      res.push(objs[pick]);
      out[pick].forEach((b) => { indeg[b]--; });
    }
    return res;
  }

  /* ───────────────── 画 ───────────────── */
  function drawMesh(o, cam) {
    const cen = o.verts.reduce((s, v) => [s[0] + v[0], s[1] + v[1], s[2] + v[2]], [0, 0, 0]).map((s) => s / o.verts.length);
    let svg = "";
    const polys = [];
    o.faces.forEach((f) => {
      const vs = f.map((i) => o.verts[i]);
      const fc = vs.reduce((s, v) => [s[0] + v[0], s[1] + v[1], s[2] + v[2]], [0, 0, 0]).map((s) => s / vs.length);
      let nrm = unit(cross(sub(vs[1], vs[0]), sub(vs[2], vs[0])));
      if (dot(nrm, sub(fc, cen)) < 0) nrm = nrm.map((s) => -s);                    // 法线朝外
      if (dot(nrm, cam.toCam(fc)) <= 0) return;                                    // 背对相机的面不画
      const clipped = cam.clip(vs);
      if (clipped.length < 3) return;
      const pts = clipped.map((v) => cam.project(v).map(n2));
      const shade = 0.55 + 0.45 * Math.max(0, dot(nrm, cam.light));
      svg += `<polygon points="${pts.map((p) => p.join(",")).join(" ")}" fill="${hex(o.tone * shade)}"/>`;
      polys.push(pts);
    });
    return { svg, polys };
  }

  /** 画一组形体。opts：camera（默认书桌相机）、hide（不画的组）、labels + groups（标出每组的名字）、stroke。
   *  返回 { svg, inner, view, polys }；polys 是每个画出来的面（带所属的组），给检查用。 */
  function render(objs, opts = {}) {
    const o = Object.assign({ hide: [], labels: false, groups: [], camera: deskCamera() }, opts);
    const cam = o.camera;
    let list = objs.filter((x) => !o.hide.includes(x.g));
    if (cam.kind === "persp") list = list.filter((x) => x.verts.some((v) => cam.depth(v) > NEAR));   // 整个在相机背后的不画
    let body = "";
    const polys = [];
    order(list, cam).forEach((m) => {
      const r = drawMesh(m, cam);
      body += r.svg;
      r.polys.forEach((pts) => polys.push({ g: m.g, name: m.name, pts }));
    });
    let view = cam.view;
    if (!view) {
      const xs = polys.flatMap((p) => p.pts.map((q) => q[0])), ys = polys.flatMap((p) => p.pts.map((q) => q[1]));
      const pad = cam.pad || 12;
      view = { x: n2(Math.min(...xs) - pad), y: n2(Math.min(...ys) - pad), w: n2(Math.max(...xs) - Math.min(...xs) + 2 * pad), h: n2(Math.max(...ys) - Math.min(...ys) + 2 * pad) };
    }
    const stroke = o.stroke || cam.stroke;
    let text = "";
    if (o.labels) {
      o.groups.forEach((g) => {
        const ms = list.filter((x) => x.g === g.id);
        if (!ms.length) return;
        const mn = (k) => Math.min(...ms.map((m) => m.bb[k])), mx = (k) => Math.max(...ms.map((m) => m.bb[k]));
        const p = cam.project([(mn(0) + mx(1)) / 2, (mn(2) + mx(3)) / 2, mx(5) + 4]);
        text += `<text x="${n2(p[0])}" y="${n2(p[1])}" text-anchor="middle">${g.label}</text>`;
      });
    }
    const fs = cam.kind === "persp" ? 26 : 13;
    const inner = `<g stroke="${INK}" stroke-width="${stroke}" stroke-linejoin="round" stroke-linecap="round">${body}</g>`
      + (text ? `<g font-family="'Microsoft YaHei',sans-serif" font-size="${fs}" font-weight="700" fill="${INK}" stroke="#fff" stroke-width="${fs / 4}" paint-order="stroke" stroke-linejoin="round">${text}</g>` : "");
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${view.x} ${view.y} ${view.w} ${view.h}" width="${Math.ceil(view.w)}" height="${Math.ceil(view.h)}">${inner}</svg>`;
    return { svg, inner, view, polys };
  }

  /* ───────────────── 参考图坐标 ↔ 世界坐标（书桌相机） ───────────────── */
  const X = (u, d) => ((u - CAM.cx) * d) / CAM.f;            // 图像 u、深度 d → 世界 x
  const Z = (v, d) => CAM.z - ((v - CAM.cy) * d) / CAM.f;    // 图像 v、深度 d → 世界 z
  const D = (v, zBase) => (CAM.f * (CAM.z - zBase)) / (v - CAM.cy);   // 图像 v 上、高度 zBase 处的东西离相机多远

  const API = { CAM, VIEW, NEAR, deskCamera, isoCamera, mesh, frustum, rbox, cyl, ball, render, order, makeBefore, clipDepth, X, Z, D, INK, sub, dot, cross, unit };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.RoomPersp = API;
})(typeof window !== "undefined" ? window : globalThis);
