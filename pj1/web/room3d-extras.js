/* 房间（新版）的附加件：A / C 两套新画法、杂物区的零食 / 奶茶、窗外的天空（天气 + 钟点）、夜里的暗光和微光。
 *
 *   · 风格：B = 贴纸白边（原来的，默认）；A = 水彩（暖奶油纸色、没有黑描边、边缘有颜料晕开的深色、线条略抖、纸纹）；
 *   · 杂物区：书桌上靠北墙那一小块（x −82~−32、y 34~66），放当日消耗品（零食平放、奶茶立着），外观由 booklook.clutter_look 的参数决定。
 *   · 窗外：天空渐变 + 太阳 / 月亮 / 星星 / 云 / 雨 / 雪 / 雾，按「钟点（每小时一档）+ 天气种类 + 日出日落」画；树、坡、樱花树也跟着变暗。
 *   · 夜里：整间房盖一层暗蓝，落地灯和显示器多一圈微光（微光用 screen 混合，盖在暗层上面）。
 * 只管「画」，不碰网络和 DOM。
 */
(function (root) {
  "use strict";

  const P = root.RoomPersp || require("./room3d-persp.js");
  const S3 = root.RoomScene3D || require("./room3d-scene.js");
  const Iso = root.PhalIso || require("./iso.js");
  const { tone, mix } = Iso;
  const L = S3.LAYOUT;
  const n2 = (v) => Math.round(v * 100) / 100;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const hash = (a, b = 0, c = 0) => { let h = Math.imul(a | 0, 374761393) ^ Math.imul(b | 0, 668265263) ^ Math.imul(c | 0, 2147483647); h = Math.imul(h ^ (h >>> 13), 1274126177); return (h ^ (h >>> 16)) >>> 0; };
  const rnd = (seed) => { let s = (seed >>> 0) || 1; return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; }; };

  /* ───────────────── 风格：A 水彩 / B 贴纸 ───────────────── */
  const STYLE = {
    A: { id: "A", mode: "wash", name: "A · 水彩", desc: "暖奶油纸色，没有黑描边，边缘有颜料晕开的深色，线条略抖，有纸纹。", ink: "#8a6c5c", inkW: 1.5, innerW: 0.8, radMax: 7, tone: { top: 0.045, right: -0.085, cool: 0 }, sticker: 0, rim: 0, detail: 2, hatch: 0, shadow: 0.13, jitter: 1.0 },
  };
  function styleOf(id) {
    if (id === "A") return STYLE[id];
    return Object.assign({}, Iso.styleOf("B"), { mode: "toon" });
  }

  const WASH = {
    floor: "#f2e6d4", floorLine: "#e3d2bb", wallN: "#f8f0e4", wallW: "#f0e3d1", trim: "#e6d5bd",
    bedHead: "#cf9a9a", bedFrame: "#dcb9a4", mattress: "#fbf5ec", pillow: "#fffaf2", blanket: "#a9c5c8", blanketB: "#f4d5c6",
    nsBody: "#cdb095", nsTop: "#e0c8ae", drawer: "#ead9c3", knob: "#f6e7c4",
    deskTop: "#ecd9bf", deskLeg: "#bb9c80", shelf: "#cdb08f", shelfBoard: "#e2cdb0", shelfBack: "#bda283", shelfTop: "#d8bf9f",
    chairSeat: "#dc9fa9", chairLeg: "#ab8f7a", chairBack: "#d38d9b", chairBase: "#ab8f7a",
    wardrobe: "#bccdb4", wardDoor: "#d1e0c9", cornice: "#aabca2", basket: "#e2cda5", basketB: "#c8ae80", paperLine: "#e1d4c0",
    ceiling: "#fbf5ec", sky: "#cfe4ee", tree: "#a9c8a5", hill: "#efc4cf", cherry: "#f4d3dc", poster: "#8da9ba", panel: "#a9bfca", mullion: "#fffaf2",
    sofaSeat: "#a2c2c9", sofaBack: "#8fb3be", sideboard: "#dbbb97", sideTop: "#e8cfb0", lampStem: "#ab8f7a", lampShade: "#f5d99c",
    monitor: "#93a6b6", monitorStand: "#abbac7", mug: "#f2c5cb", redMachine: "#ea8a82", diffuser: "#d1e4d1", plush: "#f1ddc7",
    pile: ["#efd9c4", "#f2c9cf", "#c9dde0", "#f3e3b8", "#d3e3c9", "#e4d3e8", "#f1d0b8"], sketch: "#f5e7c9", noteClosed: "#efb4ba", nbCover: "#e69ca7", page: "#fffaf0",
    hutch: "#cdb08f", hutchBoard: "#e2cdb0", pot: "#eed7ba", sprout: "#a2c99d", shadow: "#8c705c",
  };
  /** 新风格的配色：在 B 的配色（含旧版 pal 里没覆盖的键）上盖一层。 */
  function palFor(id, base) { return id === "A" ? Object.assign({}, base, WASH) : base; }

  /* ───────────────── 抖线（水彩用）：同一个点永远抖到同一个地方，所以相邻的面不会裂开 ───────────────── */
  function jit(p, amp, salt = 0) {
    if (!amp) return p;
    const h = hash(Math.round(p[0] * 2), Math.round(p[1] * 2), salt);
    return [p[0] + (((h & 1023) / 1023) - 0.5) * 2 * amp, p[1] + ((((h >>> 10) & 1023) / 1023) - 0.5) * 2 * amp];
  }

  /** 水彩：大块面（墙、地板、桌面）上颜料浓淡不均——几团略深 / 略浅的半透明色块，位置按这个面自己的坐标定（同一个面每次一样）。 */
  function washBlobs(pts, base) {
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]), x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const r = rnd(hash(Math.round(x0 / 4), Math.round(y0 / 4), Math.round(x1 / 4) + Math.round(y1 / 4)));
    let out = "";
    for (let i = 0; i < 6; i++) {
      const cx = x0 + r() * (x1 - x0), cy = y0 + r() * (y1 - y0), rx = (x1 - x0) * (0.12 + r() * 0.2), ry = (y1 - y0) * (0.12 + r() * 0.22);
      out += `<ellipse cx="${n2(cx)}" cy="${n2(cy)}" rx="${n2(rx)}" ry="${n2(ry)}" fill="${tone(base, (r() - 0.5) * 0.09, 0)}" fill-opacity="${n2(0.28 + r() * 0.22)}"/>`;
    }
    return out;
  }

  /** 水彩的纸纹：一小块可平铺的图案（颗粒 + 几根纤维），整幅画盖一层。 */
  function paperDefs(id) {
    const r = rnd(20261024);
    let s = "";
    for (let i = 0; i < 70; i++) s += `<circle cx="${n2(r() * 160)}" cy="${n2(r() * 160)}" r="${n2(0.4 + r() * 0.9)}" fill="#8a6c5c" fill-opacity="${n2(0.08 + r() * 0.14)}"/>`;
    for (let i = 0; i < 9; i++) { const x = r() * 150, y = r() * 150; s += `<path d="M${n2(x)} ${n2(y)}q${n2(6 + r() * 8)} ${n2(r() * 5 - 2.5)} ${n2(12 + r() * 10)} ${n2(r() * 4 - 2)}" fill="none" stroke="#a58b78" stroke-opacity=".14" stroke-width=".5"/>`; }
    return `<defs><pattern id="${id}" width="160" height="160" patternUnits="userSpaceOnUse">${s}</pattern></defs>`;
  }

  /* ───────────────── 杂物区：零食、奶茶 ───────────────── */
  // 往西挪了 10cm（靠近书摞和书桌相机那边）
  const CLUTTER = { x0: -72, x1: -22, y0: 34, y1: 66, z: L.DESK };
  const CLUTTER_SLOTS = [[-62, 42], [-44, 42], [-26, 42], [-62, 58], [-44, 58], [-26, 58]];
  // 奶茶立着、离书桌相机很近，会挡住看她的视线：先放东头（画面左边，显示器旁）；零食平放，放中间和西头
  const CUP_PREF = [0, 4, 3, 1, 5, 2], SNACK_PREF = [1, 2, 5, 4, 0, 3];
  // v10：北墙边的食物桌（x 60~200、y 30~80、高 75），10 个格子：靠墙一排、外面一排各 5 格；立着的（奶茶）先放靠墙那排
  const FT = L.FOODTABLE || { x0: 60, x1: 200, y0: 30, y1: 80, h: 75 };
  const TABLE_SLOTS = [0, 1, 2, 3, 4].map((i) => [FT.x0 + 14 + i * 28, FT.y0 + 13]).concat([0, 1, 2, 3, 4].map((i) => [FT.x0 + 14 + i * 28, FT.y0 + 34]));
  const ZONES = {
    desk: { slots: CLUTTER_SLOTS, z: CLUTTER.z, cup: CUP_PREF, flat: SNACK_PREF },
    table: { slots: TABLE_SLOTS, z: FT.h, cup: [2, 1, 3, 0, 4, 7, 6, 8, 5, 9], flat: [7, 6, 8, 5, 9, 2, 1, 3, 0, 4] },
  };
  let Z = ZONES.desk;                                   // 正在画的那一处（clutterMeshes 里按 place 切换）

  function assignSlots(items) {
    const used = new Set(), out = new Map();
    items.forEach((it) => {
      const pref = it.kind === "milktea" ? Z.cup : Z.flat;
      const s = pref.find((i) => !used.has(i));
      if (s == null) return;
      used.add(s);
      out.set(it.id, s);
    });
    return out;
  }
  const facing = (deg) => { const a = (deg * Math.PI) / 180; return [Math.sin(a), Math.cos(a)]; };

  /** 有贴图（manifest.room.clutter）时：奶茶一杯一张立着的图；零食一袋用 single、两袋以上合成一堆用 chunk（平放）。
   *  每张图配一个看不见的代理形体（只用来排前后、算悬停），画的时候换成 <image>（见 room3d.js 的 drawSprite）。 */
  function clutterSprites(items, sprites, slots) {
    const O = [], snacks = [];
    slots = slots || assignSlots(items || []);
    const proxy = (it, id, cx, cy, hx, hy, h, sprite) => O.push(Object.assign(P.rbox("clutter", id, 0.55, cx, cy, hx, hy, [0, 1], Z.z, Z.z + h), { col: "#ffffff", clutter: it, itemId: id, sprite, noRim: true, noHatch: true, noSticker: true }));
    (items || []).forEach((it) => {
      const si = slots.get(it.id);
      if (si == null) return;
      const [cx, cy] = Z.slots[si], k = clamp(+(it.look && it.look.size) || 1, 0.85, 1.15);
      if (it.kind === "milktea" && sprites.milktea) {
        const sp = sprites.milktea, h = (sp.height_cm || 18) * k;
        proxy(it, `c${it.id}-cup`, cx, cy, 3.6 * k, 3.6 * k, h, Object.assign({ mode: "stand", x: cx, y: cy, z: Z.z, hcm: h }, sp));
      } else if ((it.kind === "dessert" || it.kind === "fruit" || it.kind === "meal") && sprites[it.kind]) {   // 甜点 / 水果 / 饭菜：一份一张，平放（素材在 assets/room/food/）
        const sp = sprites[it.kind], w = (sp.width_cm || 12) * k;
        proxy(it, `c${it.id}-${it.kind}`, cx, cy, w / 2, (w * (sp.h / sp.w)) / 2, 5, Object.assign({ mode: "flat", x: cx, y: cy, z: Z.z, wcm: w }, sp));
      } else if (it.kind === "snack") snacks.push([it, cx, cy]);
    });
    if (snacks.length) {
      const sp = snacks.length === 1 ? sprites.snack_single : sprites.snack_chunk || sprites.snack_single;
      if (sp) {
        // 一堆的中心：横向取平均，前后取最靠里（南）的那排，免得离书桌相机太近、下半截出画
        const cx = snacks.reduce((a, s) => a + s[1], 0) / snacks.length, cy = snacks.length === 1 ? snacks[0][2] : Math.max(...snacks.map((s) => s[2]));
        const w = sp.width_cm || (snacks.length === 1 ? 20 : 30), d = w * (sp.h / sp.w);
        proxy(snacks[0][0], `c${snacks[0][0].id}-snack`, cx, cy, w / 2, Math.min(d, 26) / 2, snacks.length === 1 ? 2 : 3.5, Object.assign({ mode: "flat", x: cx, y: cy, z: Z.z, wcm: w, all: snacks.map((s) => s[0]) }, sp));
      }
    }
    return O;
  }

  /** 放着的食物 → 灰模形体（g = "clutter"，带 clutter = 这件东西，页面据此点击 / 悬停）。sprites：有贴图就用贴图。
   *  v10：分两处画——书桌的杂物区（place = desk）和食物桌（place = table），各自分格子。 */
  function clutterMeshes(items, pal, sprites, slotMap) {
    if (!slotMap) {
      const out = [];
      ["desk", "table"].forEach((pl) => {
        const part = (items || []).filter((it) => (it.place || "desk") === pl);
        if (!part.length) return;
        Z = ZONES[pl];
        clutterMeshes(part, pal, sprites, assignSlots(part)).forEach((m) => out.push(m));
      });
      Z = ZONES.desk;
      return out;
    }
    const allSlots = slotMap;                                                    // 位置只分一次：贴图和代码画的不会摆到同一格
    if (sprites && (sprites.milktea || sprites.snack_single || sprites.dessert || sprites.fruit || sprites.meal)) {
      const withArt = clutterSprites(items, sprites, allSlots), drawn = new Set(withArt.flatMap((m) => (m.sprite.all || [m.clutter]).map((c) => c.id)));
      const rest = (items || []).filter((it) => !drawn.has(it.id));                    // 缺哪种贴图，那种还用代码画的形体
      return withArt.concat(rest.length ? clutterMeshes(rest, pal, null, allSlots) : []);
    }
    const O = [], slots = allSlots;
    (items || []).forEach((it) => {
      const si = slots.get(it.id);
      if (si == null) return;
      const [cx, cy] = Z.slots[si], look = it.look || {}, z0 = Z.z, k = look.size || 1, f = facing(look.yaw || 0), r = [f[1], -f[0]], id = `c${it.id}`;
      const add = (m, col, extra) => { O.push(Object.assign(m, { col, clutter: it, itemId: id, noRim: true, noHatch: true, thin: 0.8, rad: 1.5 }, extra || {})); return m; };
      const slab = (name, ox, oy, hx, hy, h, col, extra, taper = 1, zz = z0) => add(P.rbox("clutter", `${id}-${name}`, 0.55, cx + r[0] * ox + f[0] * oy, cy + r[1] * ox + f[1] * oy, hx, hy, f, zz, zz + h, taper), col, extra);
      if (it.kind === "milktea") {
        const s = k, rr = (a, b, name, h, col, taper) => P.cyl("clutter", `${id}-${name}`, 0.55, cx, cy, a, a, b, b + h, 12, taper);
        let z = z0;
        const seg = (name, rad, h, col, taper = 1) => { const m = add(rr(rad, z, name, h, col, taper), col, { smooth: true, rad: 6 }); z += h; return m; };
        const topCol = look.topping === "pearl" ? "#5b463b" : look.topping === "jelly" ? "#f3b562" : look.topping === "foam" ? "#fffaf0" : look.drink;
        seg("bottom", 2.7 * s, 2.0 * s, topCol, 1.08);
        seg("drink", 2.92 * s, 1.5 * s, look.drink, 1.06);
        seg("sleeve", 3.1 * s, 4.6 * s, look.sleeve, 1.1);
        seg("upper", 3.4 * s, 2.7 * s, look.body, 1.04);
        seg("lid", 3.7 * s, 0.9 * s, look.lid, 0.97);
        seg("dome", 3.1 * s, 1.7 * s, look.lid, 0.55);
        add(P.frustum("clutter", `${id}-straw`, 0.55, cx + 0.5, cx + 1.3, cy - 0.4, cy + 0.4, z0 + 9.4 * s, z0 + 17.5 * s), look.straw, { rad: 0.3, sharp: true });
        return;
      }
      if (it.kind === "dessert" || look.shape === "cake") {                         // 甜点：小碟子 + 一块蛋糕 + 顶上一颗果子
        const s = k;
        add(P.cyl("clutter", `${id}-plate`, 0.55, cx, cy, 6.5 * s, 6.5 * s, z0, z0 + 0.7, 14), look.plate || "#ffffff", { smooth: true, rad: 3 });
        slab("cake", 0, 0, 3.6 * s, 2.8 * s, 4.2 * s, look.base || "#e8c08f", { rad: 1.2 }, 0.92, z0 + 0.7);
        slab("cream", 0, 0, 3.7 * s, 2.9 * s, 1.1 * s, look.cream || "#fff4e6", { rad: 1.2 }, 0.9, z0 + 0.7 + 4.2 * s);
        if (look.berry) add(P.ball("clutter", `${id}-berry`, 0.55, cx, cy, z0 + 0.7 + 5.3 * s + 0.9, 1.0 * s, 8), look.berry, { rad: 1 });
        return;
      }
      if (it.kind === "fruit" || look.shape === "fruit") {                         // 水果：小盘子 + 几个果子
        const s = k, n = Math.max(1, Math.min(4, look.count || 3));
        add(P.cyl("clutter", `${id}-plate`, 0.55, cx, cy, 7 * s, 7 * s, z0, z0 + 0.7, 14), look.plate || "#ffffff", { smooth: true, rad: 3 });
        [[0, 0], [-3.4, 1.8], [3.2, 1.6], [0.2, -3.4]].slice(0, n).forEach(([ox, oy], i) =>
          add(P.ball("clutter", `${id}-f${i}`, 0.55, cx + r[0] * ox + f[0] * oy, cy + r[1] * ox + f[1] * oy, z0 + 0.7 + 2.6 * s, 2.6 * s, 10), look.color || "#d9534f", { rad: 2 }));
        return;
      }
      if (it.kind === "meal" || look.shape === "bowl" || look.shape === "takeout") {   // 饭菜：一只碗（或一个外卖盒）+ 上面的菜
        const s = k;
        if (look.shape === "takeout") {
          slab("box", 0, 0, 7 * s, 5.5 * s, 4 * s, look.bowl || "#f4f1ec", { rad: 1 }, 0.94);
          slab("food", 0, 0, 6.2 * s, 4.8 * s, 0.8 * s, look.food || "#e9b866", { rad: 1 }, 0.9, z0 + 4 * s);
        } else {
          add(P.cyl("clutter", `${id}-bowl`, 0.55, cx, cy, 4 * s, 4 * s, z0, z0 + 5 * s, 14, 1.45), look.bowl || "#ffffff", { smooth: true, rad: 4 });
          add(P.cyl("clutter", `${id}-food`, 0.55, cx, cy, 5.4 * s, 5.4 * s, z0 + 5 * s, z0 + 5.9 * s, 14, 0.8), look.food || "#e9b866", { smooth: true, rad: 3 });
        }
        return;
      }
      const w = (look.w || 12) * k / 2, d = (look.d || 9) * k / 2, acc = look.accent, col = look.color, pat = look.pattern || "plain";
      const decoTop = (W, H, label = true) => {
        const o = [];
        if (pat === "stripes") o.push({ type: "stripes", face: "top", dir: "v", gap: 2.6, color: acc, op: 0.75, w: 1.0 });
        else if (pat === "dots") o.push({ type: "dots", face: "top", gap: 3, r: 0.6, color: acc, op: 0.85 });
        else if (pat === "band") o.push({ type: "rect", face: "top", x: 0, y: H * 0.36, w: W, h: H * 0.28, color: acc, op: 0.8 });
        if (label) o.push({ type: "rect", face: "top", x: W * 0.28, y: H * 0.26, w: W * 0.44, h: H * 0.48, rx: 1, color: "#fff", op: 0.9 }, { type: "circle", face: "top", x: W * 0.5, y: H * 0.5, r: Math.min(W, H) * 0.1, color: acc, op: 0.95 });
        return o;
      };
      switch (look.shape) {
        case "bar":
          slab("body", 0, 0, w, d * 0.42, 1.5, col, { deco: decoTop(w * 2, d * 0.84, false) });
          slab("end-l", -(w + 0.5), 0, 0.5, d * 0.36, 0.8, acc);
          slab("end-r", w + 0.5, 0, 0.5, d * 0.36, 0.8, acc);
          break;
        case "box":
          slab("body", 0, 0, w * 0.62, d * 0.9, 2.8, col, { deco: decoTop(w * 1.24, d * 1.8) }, 0.98);
          break;
        case "bread":
          slab("body", 0, 0, w * 0.72, d * 0.78, 3.4, mix(col, "#e8c48f", 0.75), { rad: 2.4 }, 0.7);
          slab("top", 0, 0, w * 0.5, d * 0.52, 0.9, mix(acc, "#c99a5b", 0.6), { rad: 1.6 }, 0.8, z0 + 3.4);
          break;
        case "candy":
          [[-3.4, -1.6, col], [0.8, 2.0, acc], [3.4, -1.2, "#ffffff"]].forEach(([ox, oy, c], i) => slab(`c${i}`, ox, oy, 1.5, 1.25, 1.7, c, { rad: 0.7 }));
          break;
        default:                                                            // bag：枕头状的袋子 + 两头压出的封边
          slab("body", 0, 0, w, d, 2.0, col, { deco: decoTop(w * 2, d * 2) }, 0.84);
          slab("seal-l", -(w + 0.55), 0, 0.55, d * 0.9, 0.7, acc);
          slab("seal-r", w + 0.55, 0, 0.55, d * 0.9, 0.7, acc);
      }
    });
    return O;
  }

  /* ───────────────── 窗外：天空 ───────────────── */
  const SKY_Y = 898;
  const DAY = {
    clear: ["#86c8f2", "#dff2fc"], cloudy: ["#9cc3e4", "#e1edf6"], overcast: ["#b1bcc9", "#dfe5ec"], fog: ["#cdd5dc", "#ecf0f3"],
    rain: ["#8998ae", "#c7d1de"], thunder: ["#6d7790", "#a6b0c3"], snow: ["#cdd8e5", "#f0f4f9"],
  };
  const NIGHT = { clear: ["#121a3a", "#2b3a6a"], cloudy: ["#1a2140", "#333f68"], overcast: ["#222840", "#383e56"], fog: ["#2a3047", "#454b61"], rain: ["#1c2540", "#34405e"], thunder: ["#171d33", "#2c3552"], snow: ["#2a3350", "#46506e"] };
  const CLEARNESS = { clear: 1, cloudy: 0.7, overcast: 0.25, fog: 0.2, rain: 0.1, thunder: 0.05, snow: 0.25 };

  /** 这个钟点（每小时一档，取半点）的天色：暗度 d（0 白天 ~ 1 深夜）、暖色 warm（日出日落前后）、天空渐变色、各种天象。 */
  function ambientInfo(amb) {
    if (!amb) return null;
    const kind = DAY[amb.kind] ? amb.kind : "clear", hour = Math.floor(amb.hour == null ? 12 : amb.hour), t = hour + 0.5;
    const rise = amb.sunrise == null ? 6.6 : amb.sunrise, set = amb.sunset == null ? 17.8 : amb.sunset;
    let d;
    if (t < rise - 0.6 || t > set + 0.6) d = 1;
    else if (t < rise + 0.9) d = 1 - (t - (rise - 0.6)) / 1.5;
    else if (t > set - 0.9) d = (t - (set - 0.9)) / 1.5;
    else d = 0;
    d = clamp(d, 0, 1);
    const warm = Math.max(0, 1 - Math.abs(t - rise) / 1.3, 1 - Math.abs(t - set) / 1.3) * (CLEARNESS[kind] > 0.15 ? 1 : 0.4);
    let top = mix(DAY[kind][0], "#f0a4b4", warm * 0.55 * CLEARNESS[kind]), bot = mix(DAY[kind][1], "#fbc7a0", warm * 0.75 * CLEARNESS[kind]);
    top = mix(top, NIGHT[kind][0], d); bot = mix(bot, NIGHT[kind][1], d);
    const clear = CLEARNESS[kind];
    const span = (set - rise) || 12, u = (t - rise) / span;
    const night = d > 0.55, nightLen = ((rise - set + 24) % 24) || 12, un = ((t - set + 24) % 24) / nightLen;
    return {
      kind, hour, t, d, warm, top, bot, clear, rise, set,
      sun: !night && u > 0.02 && u < 0.98 && (kind === "clear" || kind === "cloudy" || kind === "fog") ? { u, color: warm > 0.35 ? "#ffc27d" : "#ffe9a8" } : null,
      moon: night && un > 0 && un < 1 && (kind === "clear" || kind === "cloudy" || kind === "fog") ? { u: un } : null,
      stars: night && (kind === "clear" || kind === "cloudy") ? (kind === "clear" ? 1 : 0.5) : 0,
    };
  }

  /** 窗外的树 / 坡 / 樱花树：夜里往暗蓝里混，阴雨天再灰一点。 */
  function tintOutdoor(pal, info, sid) {
    if (!info) return pal;
    const out = Object.assign({}, pal), night = "#1d2b58", gray = "#8f98a6", soft = 0;
    ["tree", "hill", "cherry"].forEach((k) => { out[k] = mix(mix(mix(pal[k], gray, (1 - info.clear) * 0.4), night, info.d * 0.72), "#fffdf6", soft * (1 - info.d * 0.6)); });
    out.sky = info.bot;
    return out;
  }

  /** 画在天空那块板上的东西（坐标是板上的 x / z，投影到画面里）。cam：书桌相机；sc：1cm 在板那里是多少像素。 */
  function skySvg(info, cam, ctx) {
    if (!info) return "";
    const lineK = ctx.lineK, pj = (x, z) => cam.project([x, SKY_Y, z]);
    const a = pj(0, 150), b = pj(1, 150), sc = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    const gTop = pj(0, 330), gBot = pj(0, 40), gid = `r3sky${(ctx.uid = (ctx.uid || 0) + 1)}`;
    const X0 = -1300, X1 = 1300, Z0 = -20, Z1 = 830, soft = ctx.st.mode === "wash" ? 0.16 : 0, sk = (c) => mix(c, "#fffdf6", soft * (1 - info.d * 0.7));
    const c0 = pj(X0, Z1), c1 = pj(X1, Z0);
    let s = `<defs><linearGradient id="${gid}" gradientUnits="userSpaceOnUse" x1="0" y1="${n2(gTop[1])}" x2="0" y2="${n2(gBot[1])}"><stop offset="0" stop-color="${sk(info.top)}"/><stop offset="1" stop-color="${sk(info.bot)}"/></linearGradient></defs>`;
    s += `<rect x="${n2(Math.min(c0[0], c1[0]))}" y="${n2(Math.min(c0[1], c1[1]))}" width="${n2(Math.abs(c1[0] - c0[0]))}" height="${n2(Math.abs(c1[1] - c0[1]))}" fill="url(#${gid})"/>`;
    const seed = hash(info.hour, 7), r = rnd(seed), dark = info.d, kind = info.kind;
    const at = (x, z) => pj(x, z);
    if (info.stars) {
      const rs = rnd(777);
      for (let i = 0; i < 46; i++) { const x = -250 + rs() * 470, z = 110 + rs() * 300, p = at(x, z); s += `<circle cx="${n2(p[0])}" cy="${n2(p[1])}" r="${n2((0.7 + rs() * 1.3) * sc * 0.55)}" fill="#fff" fill-opacity="${n2((0.45 + rs() * 0.5) * info.stars * dark)}"/>`; }
    }
    if (info.sun) {
      const x = -200 + info.sun.u * 380, z = 90 + Math.sin(Math.PI * info.sun.u) * 175, p = at(x, z);
      s += `<circle cx="${n2(p[0])}" cy="${n2(p[1])}" r="${n2(30 * sc)}" fill="${info.sun.color}" fill-opacity=".28"/><circle cx="${n2(p[0])}" cy="${n2(p[1])}" r="${n2(16 * sc)}" fill="${info.sun.color}"/>`;
    }
    if (info.moon) {
      const x = -200 + info.moon.u * 380, z = 110 + Math.sin(Math.PI * info.moon.u) * 150, p = at(x, z), q = at(x + 6, z + 3);
      s += `<circle cx="${n2(p[0])}" cy="${n2(p[1])}" r="${n2(22 * sc)}" fill="#f7f1d8" fill-opacity=".16"/><circle cx="${n2(p[0])}" cy="${n2(p[1])}" r="${n2(13 * sc)}" fill="#f7f1d8"/><circle cx="${n2(q[0])}" cy="${n2(q[1])}" r="${n2(11 * sc)}" fill="${info.top}" fill-opacity=".9"/>`;
    }
    const cloudN = { clear: 1, cloudy: 4, overcast: 5, rain: 5, thunder: 5, snow: 4, fog: 0 }[kind] || 0;
    const cloudCol = mix(kind === "rain" || kind === "thunder" ? "#8895ab" : kind === "overcast" ? "#c9d1db" : "#ffffff", "#2a3560", dark * 0.8);
    for (let i = 0; i < cloudN; i++) {
      const w = (kind === "overcast" ? 150 : 80) + r() * 60, x = -230 + ((seed * 53 + i * 131) % 440), z = 150 + ((seed * 31 + i * 61) % 170), alpha = kind === "clear" ? 0.8 : kind === "overcast" ? 0.9 : 0.92;
      for (let j = 0; j < 4; j++) { const ox = (j - 1.5) * w * 0.26, oz = (j % 2 ? 0.1 : -0.04) * w, rr = w * (j === 1 || j === 2 ? 0.22 : 0.16), p = at(x + ox, z + oz); s += `<ellipse cx="${n2(p[0])}" cy="${n2(p[1])}" rx="${n2(rr * sc * 1.15)}" ry="${n2(rr * sc * 0.82)}" fill="${cloudCol}" fill-opacity="${alpha}"/>`; }
    }
    if (kind === "fog") { const p0 = at(-260, 0), p1 = at(260, 300); s += `<rect x="${n2(Math.min(p0[0], p1[0]))}" y="${n2(Math.min(p0[1], p1[1]))}" width="${n2(Math.abs(p1[0] - p0[0]))}" height="${n2(Math.abs(p1[1] - p0[1]))}" fill="${mix("#f1f3f4", "#2a3560", dark * 0.75)}" fill-opacity=".5"/>`; }
    if (kind === "rain" || kind === "thunder") {
      let d = "";
      for (let i = 0; i < 90; i++) { const x = -240 + r() * 440, z = 40 + r() * 300, p = at(x, z), q = at(x - 3, z - 15); d += `M${n2(p[0])} ${n2(p[1])}L${n2(q[0])} ${n2(q[1])}`; }
      s += `<path d="${d}" stroke="${mix("#eef6ff", "#9fb6e0", dark * 0.6)}" stroke-opacity=".8" stroke-width="${n2(1.1 * lineK)}" stroke-linecap="round" fill="none"/>`;
    }
    if (kind === "snow") {
      let d = "";
      for (let i = 0; i < 80; i++) { const x = -240 + r() * 440, z = 30 + r() * 320, p = at(x, z); d += `<circle cx="${n2(p[0])}" cy="${n2(p[1])}" r="${n2((0.8 + r() * 1.1) * sc * 0.7)}"/>`; }
      s += `<g fill="${mix("#ffffff", "#c8d3ee", dark * 0.5)}" fill-opacity=".92">${d}</g>`;
    }
    return s;
  }

  /** 夜里：整间房盖一层暗蓝；落地灯、显示器多一圈微光（screen 混合，盖在暗层上面）。 */
  function overlaySvg(info, cam, vb, mode, lineK, shapes) {
    if (!info || info.d < 0.03) return "";
    const d = info.d, pj = (x, y, z) => cam.project([x, y, z]);
    const dimCol = mode === "wash" ? "#2a2f62" : "#16225a", dimA = 0.22 * d;
    const u = ++overlaySvg.n;
    // 暗层只盖在房间画出来的形状上（房间视角四周是空的，不能盖成一块长方形）；书桌视角整幅都是画，直接铺满
    const clip = shapes && shapes.length && cam.kind !== "persp" ? `<clipPath id="r3dim${u}">${shapes.map((d) => `<path d="${d}"/>`).join("")}</clipPath>` : "";
    let s = `${clip}<g pointer-events="none"${clip ? ` clip-path="url(#r3dim${u})"` : ""}><rect x="${vb[0] - 4}" y="${vb[1] - 4}" width="${vb[2] + 8}" height="${vb[3] + 8}" fill="${dimCol}" fill-opacity="${n2(dimA)}"/></g>`;
    const glow = (id, x, y, z, rcm, color, alpha, ax) => {
      const c = pj(x, y, z), ex = pj(x + rcm, y, z), ey = pj(x, y, z + rcm), rx = Math.hypot(ex[0] - c[0], ex[1] - c[1]), ry = Math.hypot(ey[0] - c[0], ey[1] - c[1]), rr = ax ? Math.max(rx, ry) : (rx + ry) / 2;
      return `<radialGradient id="${id}"><stop offset="0" stop-color="${color}" stop-opacity="${n2(alpha)}"/><stop offset=".45" stop-color="${color}" stop-opacity="${n2(alpha * 0.4)}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient><circle cx="${n2(c[0])}" cy="${n2(c[1])}" r="${n2(rr)}" fill="url(#${id})"/>`;
    };
    const floorPool = (id, x, y, rcm, color, alpha) => {
      const c = pj(x, y, 0), a = pj(x + rcm, y, 0), b = pj(x, y + rcm, 0);
      return `<radialGradient id="${id}"><stop offset="0" stop-color="${color}" stop-opacity="${n2(alpha)}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient><circle r="1" fill="url(#${id})" transform="matrix(${n2(a[0] - c[0])} ${n2(a[1] - c[1])} ${n2(b[0] - c[0])} ${n2(b[1] - c[1])} ${n2(c[0])} ${n2(c[1])})"/>`;
    };
    const monitorZ = L.DESK + 24, deskView = cam.kind === "persp";
    s += `<g pointer-events="none" style="mix-blend-mode:screen">`
      + floorPool(`r3lp${u}`, 60, 305, 70, "#ffd58a", 0.5 * d)
      + glow(`r3lg${u}`, 60, 305, 141, 90, "#ffd58a", 0.85 * d)
      + floorPool(`r3ms${u}`, -100, 58, deskView ? 36 : 40, "#a9d6ff", 0.42 * d)
      + glow(`r3mg${u}`, L.MON[0], L.MON[1], monitorZ, deskView ? 34 : 50, "#9fd1ff", 0.7 * d)
      + `</g>`;
    return s;
  }
  overlaySvg.n = 0;

  const API = { STYLE, styleOf, palFor, jit, washBlobs, paperDefs, CLUTTER, CLUTTER_SLOTS, TABLE_SLOTS, ZONES, clutterMeshes, clutterSprites, assignSlots, ambientInfo, tintOutdoor, skySvg, overlaySvg, SKY_Y, WASH };
  if (typeof module !== "undefined" && module.exports) module.exports = API;
  else root.PhalRoom3DX = API;
})(typeof window !== "undefined" ? window : globalThis);
