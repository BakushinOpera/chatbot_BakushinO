/* 房间里的角色：纯前端动画，不调用模型。
 *
 * 角色是一个「世界里的人」：位置（x, y）、朝向、正在做的事都按房间的世界坐标算（厘米，x 向西、y 向南，和 room3d-scene.js 一样），
 * 和看的角度无关。房间视角（等距）、书桌视角（透视）只是两种画法：切换视角时把同一个角色重新挂到新画面上（attach），
 * 所以她在房间里乱走、背对书桌的时候，进书桌视角就能看到她的背面；她坐在书桌边，两个视角里也都坐在椅子上。
 *
 * 每个状态绑定房间里的一个位置（manifest.json 的 states / spots）；换状态时角色沿着空地板上的路线走过去，到了再播放对应的动作
 * （目前只有「喝茶」五连帧，别的状态站着，头上冒一个气泡写状态名）。素材不够用的地方有兜底：
 *   · 没有走路帧：用「侧面 / 背面 / 正面」静态图 + 上下弹跳 + 平移代替走路（往右走时侧面图左右镜像）；
 *   · 喝茶的五连帧不是真透明（棋盘格画进了像素里）：加载时自动把和边缘相连的浅灰 / 白背景抠掉（以后换成真透明的图就不抠了）。
 * 所有站着的图都按「头顶到脚底的像素高度」缩放到同一个身高（manifest.character.height_cm），所以不同图里的她一样大。
 *
 * 【坐在书桌边的接口】（现在没有素材，先留好）
 *   states.<key>.seat = { spot: "desk_seat", anim: "seat_write" }：这个状态下她坐在椅子上，用 character.assets.seat_write 这套图；
 *   坐姿的图可以按视角各给一套：assets.seat_write.views = { room: {...}, desk: {...} }（只给一套的话两个视角共用）；
 *   图里的锚点 anchor 是「坐骨（座面中心）」在图里的位置，z_cm 是它离地的高度（默认座面高 48），height_cm 是整张图折合多少厘米高；
 *   同一套接口也管「坐在沙发上」「躺在沙发上」：位置（spots）里 seat: true + pose: "sit" | "lie"（lie 再给 head = 头朝哪个方位角）、z_cm（身体离地多高）、after（必须画在它们后面的家具，比如 ["sofa/seat"]）；
 *   状态里 seat = { spot: "sofa_lie", anim: "sofa_lie_video" }；一个状态可以有几种做法（发呆：原地站着 / 坐在书桌边 / 坐在沙发上）：states.<key>.variants = [{ weight, seat? }, ...]，进入这个状态时按权重抽一种。
 *   还没有坐姿的图时：manifest.seat.placeholder 为 true（默认）就用站姿图临时顶替（书桌视角用正面图，房间视角用背面图，缩成 100cm、脚放在座面下面 25cm），
 *   这样她「坐」在椅子上、书桌上的笔记本摊开的画面是完整的；等有了真的坐姿图，同名的 seat_xxx 一放进去就自动换掉。placeholder 设成 false，就退回去站在书桌前（states.<key>.spot）。
 *   窗口隐藏 / 最小化、或者房间页面关着的时候，动画暂停。
 *
 * 【被家具挡住】房间视角（等距）里书架背对镜头、又高又宽，她站在书桌边 / 书架前的时候会被它整个挡住：
 *   她被某件大家具（在画面上盖住她 ≥ 18%）挡着时，通过 opts.onOcclude(组名列表) 通知房间页面，页面把这些家具淡成半透明（X 光），她走开了再恢复。
 */
(() => {
  "use strict";

  const NS = "http://www.w3.org/2000/svg", XL = "http://www.w3.org/1999/xlink";
  const el = (tag, attrs) => { const n = document.createElementNS(NS, tag); Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v)); return n; };
  const n2 = (v) => Math.round(v * 100) / 100;
  /** 方位角（从北往东量，0 北 / 90 东 / 180 南 / 270 西）→ 世界方向向量（北 = -y，东 = -x）。 */
  const bearingVec = (b) => { const a = (b * Math.PI) / 180; return [-Math.sin(a), -Math.cos(a)]; };
  const FRONT_BEARING = 135;          // 没有特别朝向时：面朝等距相机（东南），房间视角里看到正面，书桌视角里看到背面

  /* ───────────────── 素材：加载、抠背景、裁边 ───────────────── */
  function loadImage(url) {
    return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = () => rej(new Error(`图片加载失败：${url}`)); im.src = url; });
  }
  function toCanvas(im) {
    const c = document.createElement("canvas");
    c.width = im.naturalWidth; c.height = im.naturalHeight;
    c.getContext("2d", { willReadFrequently: true }).drawImage(im, 0, 0);
    return c;
  }
  function hasAlpha(c) {
    const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    for (let i = 3; i < d.length; i += 4 * 7) if (d[i] < 250) return true;
    return false;
  }
  /** 抠背景：从四周往里，把「和边缘相连的、偏灰白的」像素变透明，再去掉一圈白边。角色身上的白色（围裙、花边）不和边缘相连，不受影响。 */
  function keyBackground(c) {
    const w = c.width, h = c.height, ctx = c.getContext("2d"), img = ctx.getImageData(0, 0, w, h), d = img.data;
    const bgLike = (i) => { const r = d[i], g = d[i + 1], b = d[i + 2], mx = Math.max(r, g, b), mn = Math.min(r, g, b); return mx - mn < 26 && mn > 196; };
    const seen = new Uint8Array(w * h), stack = [];
    const push = (x, y) => { const k = y * w + x; if (!seen[k] && bgLike(k * 4)) { seen[k] = 1; stack.push(k); } };
    for (let x = 0; x < w; x++) { push(x, 0); push(x, h - 1); }
    for (let y = 0; y < h; y++) { push(0, y); push(w - 1, y); }
    while (stack.length) {
      const k = stack.pop(), x = k % w, y = (k / w) | 0;
      d[k * 4 + 3] = 0;
      if (x > 0) push(x - 1, y); if (x < w - 1) push(x + 1, y); if (y > 0) push(x, y - 1); if (y < h - 1) push(x, y + 1);
    }
    // 去白边：紧挨着透明区、又偏亮偏灰的像素也去掉一圈
    const fringe = [];
    for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
      const k = y * w + x, i = k * 4;
      if (d[i + 3] === 0) continue;
      if (d[i + 3 - 4 * w] === 0 || d[i + 3 + 4 * w] === 0 || d[i + 3 - 4] === 0 || d[i + 3 + 4] === 0) {
        const mx = Math.max(d[i], d[i + 1], d[i + 2]), mn = Math.min(d[i], d[i + 1], d[i + 2]);
        if (mx - mn < 40 && mn > 170) fringe.push(i + 3);
      }
    }
    fringe.forEach((i) => { d[i] = 0; });
    ctx.putImageData(img, 0, 0);
  }
  function bbox(c, x0, x1) {
    const d = c.getContext("2d").getImageData(x0, 0, x1 - x0, c.height).data, w = x1 - x0;
    let minx = 1e9, miny = 1e9, maxx = -1, maxy = -1;
    for (let y = 0; y < c.height; y++) for (let x = 0; x < w; x++) if (d[(y * w + x) * 4 + 3] > 24) { if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y; }
    return maxx < 0 ? null : { x: x0 + minx, y: miny, w: maxx - minx + 1, h: maxy - miny + 1 };
  }
  /** 加载一个素材 → { frames: [{url, w, h}], H, cfg }。帧图按公共的包围盒裁（所有帧同样大小、同样的脚底位置），H 是角色的像素高度。 */
  async function loadAsset(cfg, base, version) {
    const im = await loadImage(`${base}/${encodeURI(cfg.file)}?v=${version}`), c = toCanvas(im);
    if ((cfg.chroma || "auto") !== "off" && !hasAlpha(c)) keyBackground(c);
    const n = cfg.kind === "frames" ? Math.max(1, +cfg.frames || 1) : 1, fw = c.width / n, boxes = [];
    for (let i = 0; i < n; i++) boxes.push(bbox(c, Math.round(i * fw), Math.round((i + 1) * fw)));
    const ok = boxes.filter(Boolean);
    if (!ok.length) throw new Error(`素材是空的：${cfg.file}`);
    const top = Math.min(...ok.map((b) => b.y)), bot = Math.max(...ok.map((b) => b.y + b.h)), H = bot - top;
    const centers = boxes.map((b, i) => (b ? b.x + b.w / 2 : (Math.round(i * fw) + Math.round((i + 1) * fw)) / 2));
    const half = Math.max(...boxes.map((b, i) => (b ? Math.max(centers[i] - b.x, b.x + b.w - centers[i]) : 0))) + 2;       // 每一帧用同样宽的画布，角色居中，脚底对齐
    const frames = boxes.map((b, i) => {
      const out = document.createElement("canvas");
      out.width = Math.ceil(half * 2); out.height = H;
      const x0 = Math.round(i * fw), x1 = Math.round((i + 1) * fw), left = Math.round(centers[i] - half);
      const sx0 = Math.max(left, x0), sx1 = Math.min(left + out.width, x1);                     // 只取这一帧自己的格子，免得带进邻居的头发边
      if (sx1 > sx0) out.getContext("2d").drawImage(c, sx0, top, sx1 - sx0, H, sx0 - left, 0, sx1 - sx0, H);
      return { url: out.toDataURL("image/png"), w: out.width, h: H };
    });
    return { frames, H, cfg };
  }

  /** 加载 manifest.character.assets 里的全部素材。带 views 的（坐姿）按视角各载一套；optional 的找不到就跳过（先在清单里占个位）。 */
  async function loadAll(payload) {
    const m = payload.manifest.character, base = "/assets", out = {};
    await Promise.all(Object.entries(m.assets).map(async ([k, cfg]) => {
      if (cfg.views) {
        const views = {};
        await Promise.all(Object.entries(cfg.views).map(async ([v, vc]) => {
          try { views[v] = await loadAsset(Object.assign({ anchor: cfg.anchor, z_cm: cfg.z_cm, height_cm: cfg.height_cm }, vc), base, payload.version); }
          catch (e) { if (!cfg.optional) throw e; }
        }));
        if (Object.keys(views).length) out[k] = { views, seat: !cfg.stand, cfg };          // stand: true = 站着的动作（按身高缩放），只是两个视角各一张
        return;
      }
      try { out[k] = await loadAsset(cfg, base, payload.version); } catch (e) { if (!cfg.optional) throw e; }
    }));
    return out;
  }

  /* ───────────────── 路线：在空地板上的几个点之间走 ───────────────── */
  function route(manifest, from, toName, fromName) {
    const spots = manifest.spots, edges = manifest.waypoints.edges, names = Object.keys(spots).filter((k) => k !== "note");
    const adj = {};
    names.forEach((n) => { adj[n] = []; });
    edges.forEach(([a, b]) => { if (adj[a] && adj[b]) { adj[a].push(b); adj[b].push(a); } });
    const dist = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
    const start = fromName && adj[fromName] ? fromName : names.slice().sort((a, b) => dist(from, spots[a]) - dist(from, spots[b]))[0];
    const D = {}, prev = {}, left = new Set(names);
    names.forEach((n) => { D[n] = Infinity; });
    D[start] = 0;
    while (left.size) {
      let u = null;
      left.forEach((n) => { if (u === null || D[n] < D[u]) u = n; });
      if (D[u] === Infinity) break;
      left.delete(u);
      adj[u].forEach((v) => { const nd = D[u] + dist(spots[u], spots[v]); if (nd < D[v]) { D[v] = nd; prev[v] = u; } });
    }
    const path = [];
    for (let n = toName; n; n = prev[n]) { path.unshift({ x: spots[n].x, y: spots[n].y }); if (n === start) break; }
    return path.length ? path : [{ x: spots[toName].x, y: spots[toName].y }];
  }

  /* ───────────────── 角色 ───────────────── */
  function create(opts) {
    const { payload, assets, onAway } = opts;
    const M = payload.manifest, CH = M.character, SEAT = M.seat || { x: -60, y: 142, z_cm: 48, bearing: 25.6 };
    const seatsOf = (st) => [st.seat].concat((st.variants || []).map((v) => v.seat)).filter((x) => x && x.anim);
    // 调试：把坐姿图换成站姿图（只用来试坐姿的前后遮挡和切换，不是正式效果）
    if (opts.fakeSeat) Object.values(M.states).forEach((st) => seatsOf(st).forEach((sx) => { if (!assets[sx.anim]) assets[sx.anim] = { seat: true, fake: true, views: { room: assets.tea || assets.stand, desk: assets.tea || assets.stand } }; }));

    let layer = null, shadow = null, body = null, flip = null, image = null, bubble = null, bRect = null, bText = null, view = null, parentG = null;
    const S = { x: 0, y: 0, fx: bearingVec(FRONT_BEARING)[0], fy: bearingVec(FRONT_BEARING)[1], face: "front", mirror: false, anim: null, frameI: 0, frameT: 0, path: [], phase: "idle", state: null, at: null, t: 0, paused: true, raf: 0, last: 0, alpha: 1, fade: 0, away: null, label: "", bubbleText: "", orderIdx: -2, seat: null, h: 0, cfg: null, variant: -1 };

    const keyOf = (face) => (face === "back" ? "walk_back" : face === "side" ? "walk_side" : "stand");
    const pick = (face) => assets[keyOf(face)] || assets.stand;
    const PLACEHOLDER = SEAT.placeholder !== false;
    const hasReal = (anim) => !!(anim && assets[anim] && assets[anim].views);
    /** 坐姿的图：先找真的（按视角），没有就用站姿图临时顶替（placeholder）。 */
    function seatAsset(anim) {
      const a = anim && assets[anim], kind = view ? view.kind : "room";
      if (a && a.views) return a.views[kind] || a.views.room || a.views.desk || null;
      if (!PLACEHOLDER) return null;
      const sp = S.seat || { pose: "sit", z: SEAT.z_cm };
      if (sp.pose === "lie") return assets.stand ? Object.assign({}, assets.stand, { placeholder: true, cfg: Object.assign({}, assets.stand.cfg, { anchor: [0.5, 0.5], z_cm: sp.z + 10, height_cm: 85 }) }) : null;      // 躺着：站姿图横过来
      const base = kind === "desk" ? assets.stand : assets.walk_back || assets.stand;
      return base ? Object.assign({}, base, { placeholder: true, cfg: Object.assign({}, base.cfg, { anchor: [0.5, 1], z_cm: sp.z - 25, height_cm: 100 }) }) : null;
    }
    const hasSeat = (st) => !!(st && st.seat && st.seat.anim && (hasReal(st.seat.anim) || (PLACEHOLDER && assets.stand)));
    const pxCm = () => (view ? view.r.pxPerCm(S.x, S.y) : 1);

    /** 姿势调试器（Ctrl+Shift+P）调过的：这个状态（有几种做法的带 #序号）、这个视角的 {dx, dy, rot, scale, flip}。走路时不用。 */
    const tuneKey = () => (S.state ? S.state + (S.variant >= 0 ? `#${S.variant}` : "") : "");
    function tune() {
      if (!opts.tuning || !view || S.phase !== "idle") return null;
      const t = (opts.tuning() || {})[tuneKey()];
      return (t && t[view.kind]) || null;
    }
    /** 当前这张图的尺寸（站姿按身高，坐姿按图自己的 height_cm）和锚点。 */
    function metrics(a) {
      const cfg = a.cfg || {}, anchor = cfg.anchor || [0.5, 1], z = S.seat ? (cfg.z_cm != null ? cfg.z_cm : S.seat.z) : 0;
      const cm = S.seat ? (cfg.height_cm || 90) : CH.height_cm, t = tune();
      return { anchor, z, targetH: cm * pxCm() * (t && t.scale ? t.scale : 1) };
    }
    function setFrame(a, i) {
      if (!image || !a) return;
      const f = a.frames[i % a.frames.length], m = metrics(a), k = m.targetH / a.H, w = f.w * k, h = f.h * k;
      image.setAttributeNS(XL, "href", f.url);
      image.setAttribute("href", f.url);
      image.setAttribute("x", n2(-w * m.anchor[0])); image.setAttribute("y", n2(-h * m.anchor[1])); image.setAttribute("width", n2(w)); image.setAttribute("height", n2(h));
      S.h = h; S.ix = -w * m.anchor[0]; S.iy = -h * m.anchor[1]; S.iw = w;
    }
    /** 站着的动作：有分视角的图就按当前视角取（没有这个视角的就用另一个）。 */
    const animAsset = () => (S.anim && S.anim.views ? S.anim.views[view ? view.kind : "room"] || S.anim.views.room || S.anim.views.desk : S.anim);
    const currentAsset = () => (S.seat ? seatAsset(S.seat.anim) : animAsset() || pick(S.face));

    function mount() {
      layer = el("g", { class: "rm-char", "pointer-events": "none" });
      shadow = el("ellipse", { fill: "#1f2e4d", "fill-opacity": "0.2" });
      body = el("g"); flip = el("g"); image = el("image", { preserveAspectRatio: "none" });
      flip.appendChild(image); body.appendChild(flip);
      bubble = el("g", { class: "rm-char__bubble" }); bRect = el("rect", { rx: 9, fill: "#fff", stroke: "#2b4068", "stroke-width": 1.4 });
      bText = el("text", { "text-anchor": "middle", "font-size": 13, "font-weight": 600, fill: "#2b4068", "font-family": "'Microsoft YaHei',sans-serif" });
      bubble.append(bRect, bText);
      layer.append(shadow, body, bubble);
      bubble.setAttribute("display", "none");
      S.orderIdx = -2;
    }

    function faceNow() {
      const f = view.r.faceOf(S.fx, S.fy, S.x, S.y);
      S.face = f.face; S.mirror = f.face === "side" ? f.mirror : false;
    }

    let occKey = "", occT = 0, occTimer = 0;
    /** 被谁挡着：在她后面画的、画面上盖住她 ≥ 18% 的大家具（组名）。只在房间视角里用。 */
    function occlusion(idx, p) {
      if (!opts.onOcclude || !view || view.kind !== "room") return;
      const now = performance.now();
      if (now - occT < 220) {                                            // 走路时每帧都会进来，隔 0.2 秒算一次；停下来的那一下补算一次
        if (!occTimer) occTimer = setTimeout(() => { occTimer = 0; if (view && layer) place(); }, 240);
        return;
      }
      occT = now;
      const sb = [p[0] + S.ix, p[1] + S.iy, p[0] + S.ix + S.iw, p[1] + S.iy + S.h], area = Math.max(1, (sb[2] - sb[0]) * (sb[3] - sb[1])), best = {};
      view.r.order.forEach((e, i) => {
        if (i < idx || !e.sb || !e.o.verts || e.o.g === "things" || !XRAY.has(e.o.g)) return;
        const w = Math.min(sb[2], e.sb[2]) - Math.max(sb[0], e.sb[0]), h = Math.min(sb[3], e.sb[3]) - Math.max(sb[1], e.sb[1]);
        if (w > 0 && h > 0) best[e.o.g] = Math.max(best[e.o.g] || 0, (w * h) / area);
      });
      const groups = Object.keys(best).filter((g) => best[g] >= 0.18).sort(), key = groups.join(",");
      if (key !== occKey) { occKey = key; opts.onOcclude(groups); }
    }
    const XRAY = new Set(["bookcase", "books", "sofa", "corner", "wardrobe", "bed", "nightstand", "desk"]);

    function place() {
      if (!view || !layer) return;
      if (!S.seat) faceNow();
      const a = currentAsset(), m = a ? metrics(a) : { z: 0, targetH: CH.height_cm * pxCm() }, p = view.r.project(S.x, S.y, m.z), t = tune();
      const ox = t && t.dx ? t.dx * pxCm() : 0, oy = t && t.dy ? t.dy * pxCm() : 0;
      layer.setAttribute("transform", `translate(${n2(p[0] + ox)} ${n2(p[1] + oy)})`);
      const fl = view.r.project(S.x, S.y, 0);
      shadow.setAttribute("rx", n2(CH.height_cm * pxCm() * 0.2)); shadow.setAttribute("ry", n2(CH.height_cm * pxCm() * 0.06)); shadow.setAttribute("cy", n2(fl[1] - p[1]));
      let rot = "";
      if (a && a.placeholder && S.seat && S.seat.pose === "lie") {                // 躺着：把站姿图的「头」转到头朝的那个方向（按这个视角里的屏幕方向）
        const hv = bearingVec(S.seat.head == null ? 90 : S.seat.head), q = view.r.project(S.x + hv[0] * 30, S.y + hv[1] * 30, m.z);
        rot = `rotate(${n2((Math.atan2(q[0] - p[0], -(q[1] - p[1])) * 180) / Math.PI)})`;
      }
      const mir = S.mirror !== !!(t && t.flip);
      flip.setAttribute("transform", `${rot}${t && t.rot ? ` rotate(${n2(t.rot)})` : ""}${mir ? " scale(-1 1)" : ""}`.trim());
      layer.setAttribute("opacity", S.alpha);
      if (a && !S.seat) setFrame(a, S.frameI);
      reorder(p);
    }

    /** 画家算法：把角色插到该画在它前面的第一件东西之前（坐着时：椅子座面、靠背之后）。 */
    function reorder(p) {
      const r = view.r, order = r.order;
      const sp = S.seat;
      let cb;
      if (sp && sp.pose === "lie") {                                          // 躺着：沿头朝的方向 ±50cm、另一个方向 ±16cm
        const hv = bearingVec(sp.head == null ? 90 : sp.head), ax = Math.abs(hv[0]), ay = Math.abs(hv[1]), hx = ax * 50 + ay * 16, hy = ay * 50 + ax * 16;
        cb = { bb: [S.x - hx, S.x + hx, S.y - hy, S.y + hy, sp.z, sp.z + 32] };
      } else if (sp) cb = { bb: [S.x - 14, S.x + 14, S.y - 14, S.y + 14, sp.z, CH.height_cm] };
      else cb = { bb: [S.x - 10, S.x + 10, S.y - 8, S.y + 8, 0, CH.height_cm] };
      if (p && S.iw) cb.sb = [p[0] + S.ix, p[1] + S.iy, p[0] + S.ix + S.iw, p[1] + S.iy + S.h];     // 图在画面上实际占的范围（比身体的盒子宽）
      const idx = r.insertIndex(cb, sp ? sp.after : null, sp && sp.ahead ? sp.ahead[view.kind] || null : null);
      if (S.phase === "away" || S.alpha < 0.2) { if (occKey) { occKey = ""; if (opts.onOcclude) opts.onOcclude([]); } }
      else if (p) occlusion(idx, p);
      if (idx === S.orderIdx && layer.parentNode) return;
      S.orderIdx = idx;
      const ref = idx < order.length ? view.getNode(order[idx]) : null;
      if (ref && ref.parentNode) ref.parentNode.insertBefore(layer, ref); else parentG.appendChild(layer);
    }

    function showBubble(text) {
      S.bubbleText = text || "";
      if (!bubble) return;
      if (!text || !CH.bubble || S.phase !== "idle") { bubble.setAttribute("display", "none"); return; }
      bText.textContent = text;
      const k = view && view.kind === "desk" ? 1.7 : 1;                  // 书桌视角整幅画缩得更小，气泡放大一点才看得清
      bubble.setAttribute("transform", `scale(${k})`);
      const w = Math.max(34, Array.from(text).length * 13 + 16), y = -(S.h || CH.height_cm * pxCm()) / k - 26;
      bRect.setAttribute("x", n2(-w / 2)); bRect.setAttribute("y", y); bRect.setAttribute("width", w); bRect.setAttribute("height", 22);
      bText.setAttribute("x", 0); bText.setAttribute("y", y + 16);
      bubble.setAttribute("display", "");
    }

    function startWalk(path) { S.path = path.slice(); S.phase = "walking"; S.t = 0; S.anim = null; S.seat = null; showBubble(""); }
    function arrive() {
      S.phase = "idle";
      const spot = S.at && M.spots[S.at], st = S.cfg || M.states[S.state] || {};
      if (spot) { const v = bearingVec(spot.face == null ? FRONT_BEARING : spot.face); S.fx = v[0]; S.fy = v[1]; }
      S.seat = spot && spot.seat && hasSeat(st)                                            // 坐 / 躺在椅子、沙发上（有图或者开着占位才去，没有就站在原来的位置）
        ? { anim: st.seat.anim, pose: spot.pose || "sit", z: spot.z_cm != null ? spot.z_cm : SEAT.z_cm, after: spot.after || (S.at === "desk_seat" ? ["girlchair/seat", "girlchair/back"] : null), head: spot.head,
            ahead: spot.ahead || null }                                       // ahead = { room: ["girlchair/back"] }：这个视角里这些家具画在她前面
        : null;
      S.anim = !S.seat && st.anim && assets[st.anim] && (!assets[st.anim].views || assets[st.anim].seat === false) ? assets[st.anim] : null;
      S.frameI = 0; S.frameT = 0; S.mirror = false;
      if (view) {
        if (S.seat) { S.face = "seat"; } else faceNow();
        setFrame(currentAsset(), 0);
        showBubble(st.bubble === "zzz" ? "💤 " + (st.label || "") : (S.anim || S.seat ? "" : (st.label || S.label)));
        place();
      }
    }

    function pickFree() {
      const names = Object.keys(M.spots).filter((k) => /^free/.test(k) && k !== S.at);
      return names[Math.floor(Math.random() * names.length)] || "free_a";
    }

    /** 这个状态这一次的做法：有 variants 就按权重抽一种（variant 给了序号就用它，试的时候用）；抽到的字段盖在状态上面。 */
    function chooseCfg(key, label, variant) {
      const st = M.states[key] || { spot: "free", label: label || key }, vs = st.variants;
      if (!vs || !vs.length) { S.variant = -1; return st; }
      let i = Number.isInteger(variant) ? ((variant % vs.length) + vs.length) % vs.length : -1;
      if (i < 0) {
        const total = vs.reduce((n, v) => n + (v.weight == null ? 1 : +v.weight), 0);
        let x = Math.random() * total;
        i = vs.findIndex((v) => (x -= v.weight == null ? 1 : +v.weight) < 0);
        if (i < 0) i = vs.length - 1;
      }
      S.variant = i;
      return Object.assign({}, st, vs[i], { variants: undefined, weight: undefined });
    }
    /** 换状态：角色走到这个状态的位置（第一次直接出现在那里）。variant：试的时候指定第几种做法。 */
    function setState(key, label, first, variant) {
      if (S.state === key && S.phase !== "away") return;
      const st = chooseCfg(key, label, variant);
      S.cfg = st;
      S.state = key; S.label = st.label || label || key;
      if (st.spot === "away") {
        S.phase = "away"; S.away = st.where || "不在房间里"; S.path = []; S.seat = null;
        S.fade = -1; showBubble("");
        if (onAway) onAway(S.away);
        return;
      }
      if (onAway) onAway(null);
      const target = hasSeat(st) ? (st.seat.spot || "desk_seat") : st.spot === "free" ? pickFree() : st.spot, sp = M.spots[target];
      if (!sp) return;
      if (first || S.phase === "away" || S.at === null) {
        if (S.phase === "away") S.fade = 1;                      // 从房间外回来：在目标位置淡入
        S.x = sp.x; S.y = sp.y; S.at = target; S.alpha = S.phase === "away" ? 0 : 1; S.phase = "idle"; arrive(); return;
      }
      if (target === S.at) { arrive(); return; }
      const path = route(M, { x: S.x, y: S.y }, target, S.at);
      S.at = target;
      startWalk(path);
    }

    function tick(now) {
      if (S.paused) return;
      const dt = Math.min(0.1, (now - S.last) / 1000 || 0.016);
      S.last = now;
      S.t += dt;
      if (S.fade) {
        S.alpha = Math.max(0, Math.min(1, S.alpha + S.fade * dt * 1.6));
        if ((S.fade < 0 && S.alpha <= 0) || (S.fade > 0 && S.alpha >= 1)) S.fade = 0;
        if (S.phase === "away") { place(); S.raf = requestAnimationFrame(tick); return; }
        place();
      }
      if (S.phase === "walking" && S.path.length) {
        const to = S.path[0], dx = to.x - S.x, dy = to.y - S.y, d = Math.hypot(dx, dy), step = CH.walk.speed_cm_s * dt;
        if (d > 1e-6) { S.fx = dx / d; S.fy = dy / d; }
        if (view) { faceNow(); setFrame(pick(S.face), 0); }
        if (d <= step) { S.x = to.x; S.y = to.y; S.path.shift(); if (!S.path.length) { arrive(); S.raf = requestAnimationFrame(tick); return; } }
        else { S.x += (dx / d) * step; S.y += (dy / d) * step; }
        if (view) {
          const bob = Math.abs(Math.sin(S.t * Math.PI * 2 * CH.walk.bounce_hz)) * CH.walk.bounce_cm * pxCm();     // 弹跳代替走路
          body.setAttribute("transform", `translate(0 ${n2(-bob)})`);
          place();
        }
      } else if (S.phase === "idle" && view) {
        const a = S.seat ? seatAsset(S.seat.anim) : animAsset();
        if (a && a.frames.length > 1) {
          S.frameT += dt * 1000;
          const du = (a.cfg.durations_ms || [])[S.frameI] || 800;
          if (S.frameT >= du) { S.frameT = 0; S.frameI = (S.frameI + 1) % a.frames.length; setFrame(a, S.frameI); }
          body.setAttribute("transform", "");
        } else {
          const k = 1 + Math.sin(S.t * Math.PI * 2 * CH.idle.breathe_hz) * (CH.idle.breathe_pct / 100);     // 站着：轻微的呼吸起伏
          body.setAttribute("transform", `scale(1 ${n2(k)})`);
        }
      }
      S.raf = requestAnimationFrame(tick);
    }

    /** 挂到一个画面上：v = { kind: "room" | "desk", svg, r（room3d 的渲染结果）, getNode(entry) }。重画房间或者切视角之后都要重新挂一次。 */
    function attach(v) {
      detach();
      view = v;
      parentG = v.svg.querySelector("g");
      mount();
      const st = S.cfg || M.states[S.state] || {};
      if (S.phase === "idle" && S.seat && !hasSeat(st)) S.seat = null;
      setFrame(currentAsset(), 0);
      showBubble(S.bubbleText);
      place();
      if (S.phase === "idle" && S.at) { const t = S.cfg || M.states[S.state] || {}; showBubble(t.bubble === "zzz" ? "💤 " + (t.label || "") : (S.anim || S.seat ? "" : (t.label || S.label))); }
    }
    function detach() { clearTimeout(occTimer); occTimer = 0; if (layer) layer.remove(); layer = null; view = null; S.orderIdx = -2; if (occKey) { occKey = ""; if (opts.onOcclude) opts.onOcclude([]); } }

    function resume() { if (!S.paused) return; S.paused = false; S.last = performance.now(); S.raf = requestAnimationFrame(tick); }
    function pause() { S.paused = true; cancelAnimationFrame(S.raf); }
    const onVis = () => { if (document.hidden) pause(); };   // 回来时要不要继续由房间页面决定（页面开着、窗口在前台才继续）
    document.addEventListener("visibilitychange", onVis);
    function destroy() { pause(); document.removeEventListener("visibilitychange", onVis); detach(); }

    /** 调试器改了数值：重新摆一次（尺寸也重算）。 */
    function refresh() { if (!view || !layer) return; const a = currentAsset(); if (a) setFrame(a, S.frameI); place(); }
    return { setState, attach, detach, pause, resume: () => { if (!document.hidden) resume(); }, destroy, state: S, hasSeat, refresh, tuneKey };
  }

  window.PhalRoomChar = { loadAll, create, route, bearingVec };
})();
