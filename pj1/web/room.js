/* 她的房间：2.5D 等距小房间（卡通扁平、圆角、描边、低饱和蓝色调）+ 按分类查看。
 *
 * 数据来自 /api/room。房间是 PhalRoom3D（web/room3d.js，greyBoxTest 的统一灰模 + 三套风格）画出来的 SVG，有两个视角：
 *   · 房间视角（等距）：整间房 + 右侧栏；点书桌 → 书桌视角；
 *   · 书桌视角（透视）：铺满整个页面；Esc / 左上角「回到房间」/ 顶栏「返回」回到房间视角。
 *   两个视角是同一份场景、同一个角色（世界坐标），所以她在房间里背对书桌走的时候，书桌视角里看到的就是她的背面。
 *   区域名字和小物件的分类标签平时藏着，鼠标移到对应的家具上才出现。
 * 书、小物件、家具的外观都是参数画出来的：
 *   · 书架上的书书脊朝外，每本书的外观由一组参数决定（封面和书脊同一组参数，所以一致）；点一本书看封面；
 *   · 书架放满（默认 50 本）横向堆在书桌下面靠墙的地上（默认 20 本）；再放不下，她会把自己买的书「收纳」进收纳篮（点篮子管理）；
 *   · 家具上的小物件也是参数画的（形状、颜色、大小）；每个区域的分类标签（图标 + 名字 + 数量）点开看物品卡片；
 *   · 角色（web/roomchar.js）按状态在房间里走动，纯前端动画；书桌上的笔记本跟着她的状态合上 / 摊开（manifest.states.<状态>.desk）。
 * 图标只出现在「分类名」旁边，物品卡片上没有图标。分类表、区域表在设置的「她的房间」里改。
 */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const ROOT = $("#roomRoot");
  if (!ROOT) return;

  const S = { data: null, ambKey: "", ambForce: null, view: "plan", rview: "room", deskState: "A", deskForce: null, actor: null, actorV: null, pop: null, editing: null, sure: null, loading: false, assets: null, charState: null, scene: null, life: null, away: null, winHidden: false };
  const windowHidden = () => document.hidden || S.winHidden;
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* 无痕 */ } },
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = j.detail || detail; } catch (_) { /* 非 JSON */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  let stateTimer = null;
  function setState(text, bad) {
    const n = $("#roomState");
    n.textContent = text;
    n.classList.toggle("is-bad", !!bad);
    clearTimeout(stateTimer);
    if (text && !bad && !text.endsWith("…")) stateTimer = setTimeout(() => { n.textContent = ""; }, 2400);
  }

  /* ───────────────── 分类图标：线条 + 淡淡的底色（.f 是填色的部分） ───────────────── */
  const ICONS = {
    plant: '<path d="M12 17.4v-7"/><path class="f" d="M12 12.4C12 9.5 9.9 7.9 7 7.9c0 3 1.9 4.5 5 4.5z"/><path class="f" d="M12 10.4C12 7.1 14.2 5 17.5 5c0 3.4-2.1 5.4-5.5 5.4z"/><path class="f" d="M7.9 17.4h8.2l-.9 3.6H8.8z"/>',
    bear: '<circle class="f" cx="6.8" cy="7.6" r="2.4"/><circle class="f" cx="17.2" cy="7.6" r="2.4"/><circle class="f" cx="12" cy="13.2" r="6.4"/><ellipse cx="12" cy="15.4" rx="2.4" ry="1.7"/><circle class="d" cx="9.5" cy="12.3" r=".7"/><circle class="d" cx="14.5" cy="12.3" r=".7"/><path d="M11.2 14.6h1.6"/>',
    book: '<rect class="f" x="5.5" y="3.5" width="13" height="17" rx="2.4"/><path d="M5.5 7.5h-1.6M5.5 12h-1.6M5.5 16.5h-1.6"/><path d="M9.4 3.5v17"/><path d="M12.4 8.2h3.4M12.4 11h2.4"/>',
    dress: '<path d="M9.6 3.5v3.1M14.4 3.5v3.1"/><path class="f" d="M9.2 6.6c.5 1.5 1.4 2.3 2.8 2.3s2.3-.8 2.8-2.3l1.9 3-1.2 1.6 3.2 8.8H5.3l3.2-8.8-1.2-1.6z"/><path d="M8.6 12.2h6.8"/>',
    game: '<path class="f" d="M7.6 8.2h8.8c2.3 0 3.7 1.8 4.2 4.7l.6 3.5c.3 1.9-1.6 2.9-3 1.6l-1.9-2.1H7.7l-1.9 2.1c-1.4 1.3-3.3.3-3-1.6l.6-3.5c.5-2.9 1.9-4.7 4.2-4.7z"/><path d="M8.2 10.6v3M6.7 12.1h3"/><circle class="d" cx="15.6" cy="11.4" r=".8"/><circle class="d" cx="17.6" cy="13.2" r=".8"/>',
    lamp: '<path class="f" d="M8.4 4.4h7.2l2.2 6.6H6.2z"/><path d="M12 11v7.8"/><path d="M8.6 20.4c0-1 1.5-1.6 3.4-1.6s3.4.6 3.4 1.6z"/>',
    gift: '<rect class="f" x="4.6" y="10.4" width="14.8" height="10" rx="2"/><rect class="f" x="3.6" y="7.2" width="16.8" height="3.4" rx="1.2"/><path d="M12 7.2v13.2"/><path d="M12 7.2c-1.1-2.9-4.8-3.3-4.8-1.3 0 1.3 2.9 1.3 4.8 1.3zM12 7.2c1.1-2.9 4.8-3.3 4.8-1.3 0 1.3-2.9 1.3-4.8 1.3z"/>',
  };
  const iconKey = (token) => (ICONS[token] ? token : "");
  /** 分类图标：内置的名字画成线条图标，其它（比如 emoji）原样显示。 */
  function icon(token) {
    const k = iconKey(token);
    const s = el("span", "rm-ic " + (k ? `rm-ic--${k}` : "rm-ic--emoji"));
    if (k) s.innerHTML = `<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[k]}</svg>`;
    else s.textContent = token || "•";
    return s;
  }
  const catOf = (name) => S.data.categories.find((c) => c.name === name) || { name, icon: "gift" };
  const catIn = (area) => S.data.categories.map((c) => [c, itemsIn(area, c.name).length]).filter(([, n]) => n);
  const itemsIn = (area, cat) => S.data.items.filter((i) => i.location === area && (!cat || i.category === cat));

  /* ───────────────── 房间（两个视角：房间 = 等距，书桌 = 透视；同一份场景） ───────────────── */
  const R3 = () => window.PhalRoom3D;
  const STYLE_IDS = ["A", "B"];                 // C（线稿）已删掉；以前存的 C 会退回默认
  const manifest = () => (S.assets && S.assets.manifest) || null;
  const assetStyle = () => {
    const q = new URLSearchParams(location.search).get("roomstyle");
    if (STYLE_IDS.includes(q)) return q;
    const l = LS.get("phal.room.style", "");
    if (STYLE_IDS.includes(l)) return l;
    const m = manifest();
    return (m && m.room && m.room.style) || "B";
  };
  const assetScale = () => { const m = manifest(); return (m && m.room && m.room.scale) || 1.6; };
  /** 书桌上的笔记本现在是合上（A）还是摊开（B）：跟着她的状态走（manifest.states.<key>.desk），调试时 PhalRoom.setDeskState 可以强制。 */
  function deskStateFor(key) {
    if (S.deskForce) return S.deskForce;
    const m = manifest(), st = m && m.states && m.states[key];
    return (st && st.desk) || (m && m.desk && m.desk.default) || "A";
  }
  const DESK_GROUPS = new Set(["desk", "props", "hutch", "girlchair"]);
  const viewIs = (v) => S.view === "plan" && S.rview === v;

  const CLUTTER_KINDS = [{ key: "snack", label: "零食", icon: "🍪" }, { key: "milktea", label: "奶茶", icon: "🧋" }, { key: "dessert", label: "甜点", icon: "🍰" },
    { key: "fruit", label: "水果", icon: "🍎" }, { key: "meal", label: "饭菜", icon: "🍱" }];
  const FOOD_CAT = "食物";                                                            // 「添加物品」里的特殊分类：放进杂物区 / 食物桌，不进物品表
  const foodAreas = () => (S.data && (S.data.food_areas || [S.data.clutter_area])) || [];
  const isClutter = (area) => !!S.data && foodAreas().includes(area);
  const foodIn = (area) => (S.data.clutter || []).filter((c) => (c.area || S.data.clutter_area) === area);
  const clutterKinds = (area) => CLUTTER_KINDS.map((k) => [k, foodIn(area).filter((c) => c.kind === k.key).length]).filter(([, n]) => n);
  function areaTag(area, cls) {
    const box = el("div", cls);
    box.dataset.area = area;
    const name = el("button", "rm-zone__name", area);
    name.type = "button";
    name.title = area === "书架" ? "打开书架" : "看这个区域里的全部东西";
    name.addEventListener("click", () => (area === "书架" ? openPop({ kind: "shelf", tab: "shelf" }) : openPop({ kind: "area", area })));
    box.appendChild(name);
    const chips = el("div", "rm-zone__chips");
    if (isClutter(area)) clutterKinds(area).forEach(([k, n]) => { const b = chip({ name: k.label, icon: k.icon }, n, area); b.onclick = () => openPop({ kind: "area", area }); chips.appendChild(b); });
    else catIn(area).forEach(([c, n]) => chips.appendChild(chip(c, n, area)));
    if (!chips.childNodes.length && area !== "书架") chips.appendChild(el("span", "rm-zone__empty", "空空的"));
    if (chips.childNodes.length) box.appendChild(chips);
    return box;
  }
  function chip(c, n, area) {
    const b = el("button", "rm-chip");
    b.type = "button";
    b.title = `${c.name} ${n} 件`;
    b.appendChild(icon(c.icon));
    b.appendChild(el("span", "rm-chip__name", c.name));
    b.appendChild(el("b", null, String(n)));
    b.addEventListener("click", () => openPop({ kind: "cell", area, cat: c.name }));
    return b;
  }

  /** 窗外的光线：服务器给天气种类和日出日落，钟点用本机的（和服务器同一台机器）；PhalRoom.setAmbient 可以强制（试天气 / 夜里的样子）。 */
  function ambientNow() {
    const a = S.data && S.data.ambient;
    if (!a) return null;
    const n = new Date();
    return Object.assign({}, a, { hour: n.getHours(), minute: n.getMinutes() }, S.ambForce || {});
  }
  /** 杂物区的贴图（manifest.room.clutter）：{ milktea, snack_single, snack_chunk } → 带 href 的配置；没配就用代码画的形体。 */
  function clutterSprites() {
    const m = manifest(), c = m && m.room && m.room.clutter;
    if (!c || !S.assets.available) return null;
    const out = {};
    ["milktea", "snack_single", "snack_chunk", "dessert", "fruit", "meal"].forEach((k) => { if (c[k] && c[k].file && c[k].w && c[k].h) out[k] = Object.assign({}, c[k], { href: `/assets/${encodeURI(c[k].file)}?v=${S.assets.version}` }); });
    return Object.keys(out).length ? out : null;
  }
  function sceneOptions() {
    const lay = S.data.books;
    return {
      state: S.deskState, books: { shelf: lay.shelf, floor: lay.floor, storage: lay.storage }, basket: true, clutter: S.data.clutter || [], ambient: ambientNow(), sprites: clutterSprites(),
      weather: WX_PARAMS, layered: S.rview === "desk" && !!window.PhalWeatherFX,           // 书桌视角：窗外拆成动画层（web/roomweather.js）
      items: S.data.items.map((i) => ({ id: i.id, name: i.name, icon: i.icon, look: i.look, location: i.location })),
    };
  }

  /** 家具 / 书 / 小物件属于哪个区域（悬停时显示这个区域的名字标签）。 */
  function areaOfPart(o) {
    const R = R3();
    if (o.item) return o.item.location;
    if (o.clutter) return o.clutter.area || S.data.clutter_area;
    if (o.book || o.g === "books" || o.g === "basket" || o.g === "pile") return "书架";
    return R.AREA_OF[o.g] || null;
  }

  /** 把房间画进容器：SVG + 可点的家具 / 书 / 收纳篮 + 悬停才出现的区域标签 + 角色。S.rview 决定画哪个视角。 */
  function drawScene(box) {
    const R = R3(), kind = S.rview;
    const r = R.render(sceneOptions(), assetStyle(), kind, assetScale());
    box.innerHTML = "";
    if (kind === "room") box.style.setProperty("--rm-aspect", String(r.viewBox[2] / r.viewBox[3]));
    const holder = el("div", "rm-iso__svg");
    holder.innerHTML = R.toSvg(r);
    const svg = holder.querySelector("svg");
    svg.removeAttribute("width"); svg.removeAttribute("height");
    svg.setAttribute("preserveAspectRatio", kind === "desk" ? "xMidYMid slice" : "xMidYMid meet");      // 书桌视角铺满整个页面（多出来的边裁掉），房间视角完整显示
    svg.classList.add("rm-svg-iso");
    box.appendChild(holder);
    const nodes = new Map();
    [...svg.querySelectorAll("g[data-n]")].forEach((n, i) => nodes.set(r.order[i], n));

    // 区域标签：平时藏着，鼠标移到对应的家具 / 书上才显示（标签自己也能停留、点击）
    const tags = el("div", "rm-iso__tags"), tagEls = new Map();
    S.data.areas.concat(foodAreas()).forEach((a) => { const t = areaTag(a, "rm-zone"); tagEls.set(a, t); tags.appendChild(t); });
    box.appendChild(tags);
    // 这个区域的家具（不含书、小物件）在画面里的包围盒：标签放在它正上方；放不下（太靠上）就放下面。这样标签不会盖住鼠标正指着的家具
    const bodyOfArea = new Map();
    r.order.forEach((e) => { const o = e.o, n = nodes.get(e); if (!n || o.halo || o.shadow || R.SHELL.has(o.g) || o.book && o.g !== "books") return; const a = o.g === "books" ? "书架" : R.AREA_OF[o.g]; if (!a) return; if (!bodyOfArea.has(a)) bodyOfArea.set(a, []); bodyOfArea.get(a).push(n); });
    const placeTag = (area, ev) => {
      const t = tagEls.get(area), parts = bodyOfArea.get(area);
      if (!t || !parts) return false;
      const cr = box.getBoundingClientRect();
      if (t.dataset.placed === "1") return true;                           // 位置固定：第一次算好就不再跟着鼠标动
      let l = 1e9, rr = -1e9, tp = 1e9, bt = -1e9;
      parts.forEach((n) => { const q = n.getBoundingClientRect(); if (!q.width || !q.height) return; l = Math.min(l, q.left); rr = Math.max(rr, q.right); tp = Math.min(tp, q.top); bt = Math.max(bt, q.bottom); });
      if (l > rr) return false;
      l = Math.max(l, cr.left); rr = Math.min(rr, cr.right); tp = Math.max(tp, cr.top); bt = Math.min(bt, cr.bottom);     // 书桌视角里家具常常出画：只算画面里看得见的那部分
      t.dataset.placed = "1";
      const below = tp - cr.top < 96;
      t.classList.toggle("is-below", below);
      t.style.left = `${Math.max(70, Math.min(cr.width - 70, (l + rr) / 2 - cr.left))}px`;
      t.style.top = `${below ? Math.min(cr.height - 60, bt - cr.top + 8) : Math.max(8, tp - cr.top - 8)}px`;
      return true;
    };
    let hideT = 0;
    const showTag = (area, ev) => { clearTimeout(hideT); tagEls.forEach((t, a) => t.classList.toggle("is-show", a === area && placeTag(a, ev))); };
    let hotKey = "";                                                       // 现在提示的是哪一件（在同一件家具的几个部件之间移动时不重新提示，免得闪）
    const hideTags = () => { clearTimeout(hideT); hideT = setTimeout(() => tagEls.forEach((t) => t.classList.remove("is-show")), 240); };
    tagEls.forEach((t) => { t.addEventListener("mouseenter", () => clearTimeout(hideT)); t.addEventListener("mouseleave", hideTags); });
    S.showTag = showTag;

    // 点家具 / 书 / 收纳篮 / 小物件；悬停时整件家具微微提亮 + 显示它的标签
    const partsOfArea = new Map();
    r.order.forEach((e) => { const o = e.o, a = nodes.get(e) && !o.halo && !o.shadow && !R.SHELL.has(o.g) ? areaOfPart(o) : null; if (a && !o.book) { if (!partsOfArea.has(a)) partsOfArea.set(a, []); partsOfArea.get(a).push(nodes.get(e)); } });
    r.order.forEach((e) => {
      const o = e.o, node = nodes.get(e);
      if (!node || o.halo || o.shadow || R.SHELL.has(o.g)) return;
      let act = null;
      if (o.book) act = () => openPop({ kind: "book", book: o.book });
      else if (o.g === "basket") act = () => openPop({ kind: "shelf", tab: "storage" });
      else if (o.g === "bookcase") act = () => openPop({ kind: "shelf", tab: "shelf" });
      else if (o.g === "pile") act = () => openPop({ kind: "shelf", tab: "floor" });
      else if (o.item) act = () => openPop({ kind: "area", area: o.item.location });
      else if (o.g === "props" && /^notebook/.test(o.name || "") && kind === "desk") act = () => { if (window.PhalNotebook) window.PhalNotebook.open(); };   // 书桌上的笔记本 → 她的小本子
      else if (DESK_GROUPS.has(o.g) && kind === "room") act = () => setRoomView("desk");      // 房间视角里点书桌 → 去书桌视角
      else if (R.AREA_OF[o.g]) act = () => openPop({ kind: "area", area: R.AREA_OF[o.g] });
      if (!act) return;
      const area = areaOfPart(o);
      node.classList.add("rm-hit");
      node.addEventListener("click", (ev) => { ev.stopPropagation(); act(); });
      if (o.book) node.addEventListener("contextmenu", (ev) => { ev.preventDefault(); ev.stopPropagation(); openPop({ kind: "bookedit", book: o.book }); });   // 右键：修改书籍详情
      const isNb = o.g === "props" && /^notebook/.test(o.name || "") && kind === "desk";
      const key = o.book ? `b${o.book.id || o.book.pid}` : o.item ? `i${o.item.id}` : isNb ? "nb" : `a${area}|${o.g === "basket" || o.g === "pile" ? o.g : ""}`;
      node.addEventListener("mouseenter", (ev) => {
        if (key !== hotKey) {
          svg.querySelectorAll(".is-hot,.is-pick").forEach((n) => n.classList.remove("is-hot", "is-pick"));
          hotKey = key;
          if (o.book) node.classList.add("is-pick");
          else if (o.item) (partsOfArea.get(area) || []).forEach((n) => { if (n.dataset.n.startsWith(`it-${o.item.id}-`)) n.classList.add("is-hot"); });
          else if (isNb) svg.querySelectorAll('g[data-n^="notebook"]').forEach((n) => n.classList.add("is-hot"));
          else (o.g === "basket" || o.g === "pile" ? svg.querySelectorAll(`g[data-g="${o.g}"]`) : partsOfArea.get(area) || []).forEach((n) => n.classList.add("is-hot"));
        }
        if (area) showTag(area, ev);
      });
      node.addEventListener("mouseleave", (ev) => {
        const to = ev.relatedTarget && ev.relatedTarget.closest ? ev.relatedTarget.closest("g[data-n]") : null;
        if (to && svg.contains(to)) return;                                // 移到了别的部件上：交给它的 mouseenter
        hotKey = "";
        svg.querySelectorAll(".is-hot,.is-pick").forEach((n) => n.classList.remove("is-hot", "is-pick"));
        hideTags();
      });
      const t = o.book ? `《${window.PhalIso.head8(o.book.title)}》（右键修改详情）` : DESK_GROUPS.has(o.g) && kind === "room" ? "点一下，看书桌视角" : null;
      if (t) { const ti = document.createElementNS("http://www.w3.org/2000/svg", "title"); ti.textContent = t; node.appendChild(ti); }
    });

    S.scene = { r, svg, nodes, kind };
    initChar(r, svg, nodes, kind);
    const FXW = window.PhalWeatherFX;
    if (FXW) {
      if (r.layered) { FXW.setQuality(S.data.render_quality); FXW.mount(box, r, r.ambient, WX_PARAMS); syncWeather(); }
      else FXW.unmount();
    }
  }

  /* ───────────────── 窗外天气：动画只在书桌视角、页面看得见的时候跑；参数用出厂值（web/room3d-weather.js） ───────────────── */
  const WX_PARAMS = window.PhalWeatherLook ? window.PhalWeatherLook.defaults() : null;
  function syncWeather() {
    const FXW = window.PhalWeatherFX;
    if (FXW) FXW.setActive(!ROOT.hidden && !windowHidden() && viewIs("desk"));
  }

  /* ───────────────── 角色 ───────────────── */
  async function initChar(r, svg, nodes, kind) {
    const C = window.PhalRoomChar, a = S.assets;
    if (!C || !a || !a.available || !a.manifest.character) { setAway("素材目录里还没有角色素材（见 assets/素材清单.md）"); return; }
    try {
      if (!S.charAssets || S.charAssets.v !== a.version) S.charAssets = { v: a.version, data: await C.loadAll(a) };
      if (!S.scene || S.scene.r !== r) return;              // 加载期间又重画了
      let fresh = false;
      if (!S.actor || S.actorV !== a.version) {
        if (S.actor) S.actor.destroy();
        S.actor = C.create({ payload: a, assets: S.charAssets.data, fakeSeat: new URLSearchParams(location.search).has("fakeseat"), onAway: (w) => { S.away = w; renderSide(); renderDeskNow(); }, onOcclude: applyXray,
          tuning: () => S.tuning || {} });
        S.actorV = a.version; fresh = true;
      }
      S.actor.attach({ kind, svg, r, getNode: (e) => nodes.get(e) });
      if (S.poseLock) S.actor.setState(S.poseLock.key, "", true, S.poseLock.variant);
      else if (fresh) { if (S.life) S.actor.setState(S.life.key, S.life.label, true); else if (S.charState) S.actor.setState(S.charState, "", true); }
      if (!ROOT.hidden && !windowHidden()) S.actor.resume();
    } catch (e) {
      console.warn("角色素材加载失败", e);
      setAway(`角色素材加载失败：${e.message}`);
    }
  }
  /** 她被大家具挡着时，把挡着她的家具淡成半透明（房间视角）；组名 → 一起淡的几组（书架和架子上的书是一件东西）。 */
  const XRAY_WITH = { bookcase: ["bookcase", "books"], books: ["bookcase", "books"], corner: ["corner"] };
  function applyXray(groups) {
    const svg = S.scene && S.scene.svg;
    if (!svg) return;
    svg.querySelectorAll(".is-xray").forEach((n) => n.classList.remove("is-xray"));
    new Set(groups.flatMap((g) => XRAY_WITH[g] || [g])).forEach((g) => svg.querySelectorAll(`g[data-g="${g}"]`).forEach((n) => n.classList.add("is-xray")));
  }
  function setAway(msg) { S.away = msg; renderSide(); renderDeskNow(); }

  async function pollLife() {
    if (ROOT.hidden) return;
    try {
      const st = (await api("/api/life")).status;
      if (!st || st.enabled === false) { S.life = null; renderSide(); renderDeskNow(); return; }
      const changed = !S.life || S.life.key !== st.key;
      S.life = st;
      if (S.actor && changed && !S.poseLock) S.actor.setState(st.key, st.label, false);      // 姿势调试器开着时：她停在调试的状态里
      const ds = deskStateFor(S.poseLock ? S.poseLock.key : st.key);
      if (ds !== S.deskState) { S.deskState = ds; if (S.view === "plan" && S.data) { render(); renderPop(); } }
      renderSide(); renderDeskNow();
    } catch (_) { /* 取不到就算了，下次再试 */ }
  }
  let lifeTimer = null;

  /* ───────────────── 书桌视角：铺满页面，左上角一个返回键 + 她现在在做什么 ───────────────── */
  function renderDeskNow(node) {
    const box = node || $(".rm-desk__now");
    if (!box) return;
    box.innerHTML = "";
    box.appendChild(el("b", null, S.life ? S.life.label || S.life.key : "她的生活现在是关着的"));
    if (S.life && S.life.mood) box.appendChild(el("span", null, `心情：${S.life.mood}`));
    if (S.away) box.appendChild(el("span", "rm-desk__away", S.away));
  }
  function renderDesk(box) {
    const scene = el("div", "rm-desk"), bar = el("div", "rm-desk__bar");
    const back = el("button", "rm-desk__back", "‹ 回到房间");
    back.type = "button";
    back.addEventListener("click", () => setRoomView("room"));
    const now = el("div", "rm-desk__now");
    bar.append(back, now);
    box.append(scene, bar);
    drawScene(scene);
    renderDeskNow(now);
  }
  function setRoomView(kind) {
    if (S.rview === kind) return;
    S.rview = kind;
    S.pop = null; S.editing = null; S.sure = null;
    render();
    renderPop();
  }

  /* ───────────────── 右侧：她现在在做什么 + 房间概况 + 书架 ───────────────── */
  function renderSide() {
    const box = $("#roomSide");
    if (!box || !S.data) return;
    box.innerHTML = "";
    const card = (title) => { const c = el("section", "rm-side__card"); c.appendChild(el("h3", "rm-side__title", title)); box.appendChild(c); return c; };

    const now = card("她现在");
    if (S.life) {
      now.appendChild(el("div", "rm-side__state", S.life.label || S.life.key));
      if (S.life.blurb) now.appendChild(el("div", "rm-side__sub", S.life.blurb));
      if (S.life.mood) now.appendChild(el("div", "rm-side__pill", `心情：${S.life.mood}`));
    } else now.appendChild(el("div", "rm-side__sub", "她的生活现在是关着的。"));
    if (S.away) now.appendChild(el("div", "rm-side__away", S.away));

    const lay = S.data.books, mine = lay.shelf.concat(lay.floor).filter((b) => b.kind !== "imported").length;
    const sh = card("书架");
    sh.appendChild(el("div", "rm-side__sub", `书架 ${lay.shelf.length} / ${lay.caps.shelf} 本（导入的 ${lay.imported} 本，她买的摆设书 ${mine} 本）`));
    const meta = [];
    if (lay.floor.length) meta.push(`地上堆着 ${lay.floor.length} 本`);
    if (lay.hidden) meta.push(`还有 ${lay.hidden} 本放不下`);
    meta.push(`收纳篮 ${lay.storage.length} 本`);
    sh.appendChild(el("div", "rm-side__sub", meta.join("　")));
    const go = el("button", "btn btn--mini", "管理书架和收纳篮");
    go.type = "button";
    go.addEventListener("click", () => openPop({ kind: "shelf", tab: "shelf" }));
    sh.appendChild(go);

    const ar = card("区域");
    S.data.areas.forEach((a) => {
      const n = itemsIn(a).length, row = el("button", "rm-side__row");
      row.type = "button";
      row.append(el("span", null, a), el("b", null, a === "书架" ? `${lay.shelf.length + lay.floor.length}` : String(n)));
      row.addEventListener("click", () => (a === "书架" ? openPop({ kind: "shelf", tab: "shelf" }) : openPop({ kind: "area", area: a })));
      ar.appendChild(row);
    });
    foodAreas().forEach((a) => {
      const n = foodIn(a).length;
      if (!n && a === S.data.clutter_area) return;
      const row = el("button", "rm-side__row");
      row.type = "button";
      row.append(el("span", null, a), el("b", null, String(n)));
      row.addEventListener("click", () => openPop({ kind: "area", area: a }));
      ar.appendChild(row);
    });
    if (S.data.items.some((i) => i.location === S.data.other_area)) {
      const row = el("button", "rm-side__row");
      row.type = "button";
      row.append(el("span", null, S.data.other_area), el("b", null, String(itemsIn(S.data.other_area).length)));
      row.addEventListener("click", () => openPop({ kind: "area", area: S.data.other_area }));
      ar.appendChild(row);
    }
    const ca = card("按分类");
    S.data.categories.forEach((c) => {
      const n = c.name === FOOD_CAT ? (S.data.clutter || []).length : S.data.items.filter((i) => i.category === c.name).length;
      if (!n) return;
      const row = el("button", "rm-side__row");
      row.type = "button";
      const l = el("span", "rm-side__cat");
      l.append(icon(c.icon), document.createTextNode(c.name));
      row.append(l, el("b", null, String(n)));
      row.addEventListener("click", () => { S.view = "cat"; LS.set("phal.room.view", "cat"); render(); });
      ca.appendChild(row);
    });
    if (!ca.querySelector(".rm-side__row")) ca.appendChild(el("div", "rm-side__sub", "房间里还没有小物件。"));
  }

  function renderPlan(box) {
    const wrap = el("div", "rm-iso");
    const scene = el("div", "rm-iso__scene");
    const side = el("aside", "rm-side");
    side.id = "roomSide";
    wrap.append(scene, side);
    box.appendChild(wrap);
    drawScene(scene);
    renderSide();
    if (!S.data.items.length && !S.data.books.shelf.length) box.appendChild(el("p", "rm-hint", "房间里还没有东西。"));

  }

  /* ───────────────── 按分类查看 ───────────────── */
  function renderCats(box) {
    const d = S.data;
    let any = false;
    d.categories.forEach((c) => {
      const food = c.name === FOOD_CAT;
      const list = food ? (d.clutter || []) : d.items.filter((i) => i.category === c.name);
      if (!list.length) return;
      any = true;
      const sec = el("section", "rm-cat");
      const head = el("h3", "rm-cat__head");
      head.appendChild(icon(c.icon));
      head.appendChild(el("span", "rm-cat__name", c.name));
      head.appendChild(el("span", "rm-cat__n", `${list.length}`));
      sec.appendChild(head);
      const grid = el("div", "rm-cat__grid");
      list.forEach((i) => grid.appendChild(food ? clutterCard(i) : card(i, { showLoc: true })));
      sec.appendChild(grid);
      box.appendChild(sec);
    });
    if (!any) box.appendChild(el("p", "rm-hint", "房间里还没有小物件。"));
  }

  /* ───────────────── 物品卡片（没有图标；查看 / 编辑 / 删除） ───────────────── */
  function card(item, opts = {}) {
    if (S.editing === item.id) return editForm(item);
    const c = el("div", "rm-card");
    const top = el("div", "rm-card__top");
    top.appendChild(el("span", "rm-card__name", item.name));
    top.appendChild(el("span", `rm-pill rm-pill--${item.source}`, item.source_label));
    const ops = el("div", "rm-card__ops");
    const edit = el("button", "rm-op", "编辑");
    edit.type = "button";
    edit.addEventListener("click", () => { S.editing = item.id; S.sure = null; rerender(); });
    ops.append(edit, sureButton(`i${item.id}`, async () => {
      S.data = await api(`/api/room/items/${item.id}`, { method: "DELETE" });
      setState("已删除");
    }));
    top.appendChild(ops);
    c.appendChild(top);
    const meta = [item.got_on];
    if (opts.showLoc) meta.push(item.location);
    if (opts.showCat) meta.push(item.category);
    c.appendChild(el("div", "rm-card__meta", meta.join(" · ")));
    if (item.note) c.appendChild(el("div", "rm-card__note", `「${item.note}」`));
    return c;
  }

  /** 改一份食物：名字、种类、位置（书桌 / 食物桌）、来源、保质期（从现在起还有几小时）、还剩几次。 */
  function foodEdit(c) {
    const box = el("div", "rm-card rm-card--edit");
    const name = input(c.name, "", 30);
    const kind = select(CLUTTER_KINDS.map((k) => [k.key, k.label]), c.kind);
    const loc = select((S.data.food_locations || ["书桌", "食物桌"]).map((a) => [a, a]), c.location || "食物桌");
    const srcs = (S.data.sources || []).map((s) => [s.key, s.label]);
    if (!srcs.some(([k]) => k === c.source)) srcs.push([c.source, c.source_label || c.source]);
    const src = select(srcs, c.source);
    const left = Math.max(0.5, Math.round(((c.expires_at * 1000 - Date.now()) / 3600000) * 2) / 2);
    const life = input(c.expired ? "" : String(left), c.expired ? "已过期，填几小时就是重新算" : "小时", 8);
    life.type = "number"; life.min = "0.5"; life.step = "0.5";
    const parts = input(String(c.portions_left), "", 4);
    parts.type = "number"; parts.min = "1"; parts.max = "20";
    box.appendChild(formRows([["名称", name], ["种类", kind], ["位置", loc], ["来源", src], ["还有几小时过期", life], ["还能吃几次", parts]]));
    const ops = el("div", "rm-form__ops");
    const ok = el("button", "btn btn--mini", "保存"), cancel = el("button", "btn btn--ghost btn--mini", "取消");
    ok.type = cancel.type = "button";
    cancel.addEventListener("click", () => { S.editing = null; rerender(); });
    ok.addEventListener("click", async () => {
      const body = { name: name.value.trim(), kind: kind.value, location: loc.value, source: src.value, portions_left: Math.max(1, Math.round(Number(parts.value)) || 1) };
      if (life.value !== "") body.expires_h = Number(life.value);
      try { S.data = await api(`/api/room/clutter/${c.id}`, { method: "PATCH", body: JSON.stringify(body) }); S.editing = null; setState("改好了"); rerender(); }
      catch (e) { setState(`改不了：${e.message}`, true); }
    });
    ops.append(ok, cancel);
    box.appendChild(ops);
    return box;
  }

  /** 一份食物：名字、种类、谁放的、保质期、还剩几次；可以改、手动收掉。 */
  function clutterCard(c) {
    if (S.editing === `f${c.id}`) return foodEdit(c);
    const card = el("div", "rm-card" + (c.expired ? " is-expired" : "")), top = el("div", "rm-card__top");
    top.appendChild(el("span", "rm-card__name", c.name));
    top.appendChild(el("span", "rm-pill rm-pill--bought", c.label));
    const ops = el("div", "rm-card__ops"), del = el("button", "rm-op rm-op--del", "收掉");
    const edit = el("button", "rm-op", "编辑");
    edit.type = "button";
    edit.addEventListener("click", () => { S.editing = `f${c.id}`; S.sure = null; rerender(); });
    ops.appendChild(edit);
    del.type = "button";
    del.addEventListener("click", async () => {
      try { S.data = await api(`/api/room/clutter/${c.id}`, { method: "DELETE" }); setState("已收掉"); rerender(); } catch (e) { setState(`收不掉：${e.message}`, true); }
    });
    ops.appendChild(del);
    top.appendChild(ops);
    card.appendChild(top);
    const left = c.portions > 1 ? ` · 还能吃 ${c.portions_left} / ${c.portions} 次` : "";
    card.appendChild(el("div", "rm-card__meta", `${c.source_label || ""} · ${c.location || c.area} · ${c.bought_hm} 放上来 · ${c.expires_text || ""}（${c.expires_hm}）${left}${c.expired ? " · 睡前会收掉" : ""}`));
    return card;
  }

  /** 删除按钮：点一下变成「确定删除？」，再点一下才真的删（3.5 秒内不点就还原）。 */
  function sureButton(key, run, label = "删除") {
    const b = el("button", "rm-op rm-op--del", S.sure === key ? "确定删除？" : label);
    b.type = "button";
    b.addEventListener("click", async () => {
      if (S.sure !== key) {
        S.sure = key;
        rerender();
        setTimeout(() => { if (S.sure === key) { S.sure = null; rerender(); } }, 3500);
        return;
      }
      try { await run(); S.sure = null; rerender(); } catch (e) { setState(`删除失败：${e.message}`, true); }
    });
    return b;
  }

  function select(options, value) {
    const s = el("select", "input input--tiny");
    options.forEach(([v, label, disabled]) => {
      const o = el("option", null, label);
      o.value = v;
      if (disabled) o.disabled = true;
      s.appendChild(o);
    });
    s.value = value;
    return s;
  }
  function formRows(fields) {
    const form = el("div", "rm-form");
    fields.forEach(([label, node]) => { form.appendChild(el("label", null, label)); form.appendChild(node); });
    return form;
  }
  function input(value, ph, max) {
    const i = el("input", "input input--tiny");
    i.type = "text";
    i.value = value || "";
    if (ph) i.placeholder = ph;
    if (max) i.maxLength = max;
    return i;
  }
  function dateInput(value) {
    const i = el("input", "input input--tiny");
    i.type = "date";
    i.value = value || "";
    return i;
  }

  function editForm(item) {
    const d = S.data;
    const box = el("div", "rm-card rm-card--edit");
    const name = input(item.name, "名称", 30);
    const cat = select(d.categories.map((c) => [c.name, c.name]), item.category);
    const inArea = d.areas.includes(item.location);
    const loc = select([...(inArea ? [] : [["", `${item.location}（请选一个区域）`, true]]), ...d.areas.map((a) => [a, a])], inArea ? item.location : "");
    const src = select(d.sources.map((s) => [s.key, s.label]), item.source);
    const day = dateInput(item.got_on);
    const note = input(item.note, "她的一句备注", 60);
    box.appendChild(formRows([["名称", name], ["分类", cat], ["位置", loc], ["来源", src], ["获得日期", day], ["她的备注", note]]));
    const ops = el("div", "rm-form__ops");
    const save = el("button", "btn btn--mini", "保存");
    save.type = "button";
    const cancel = el("button", "btn btn--ghost btn--mini", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", () => { S.editing = null; rerender(); });
    save.addEventListener("click", async () => {
      const patch = {};
      if (name.value.trim() !== item.name) patch.name = name.value.trim();
      if (cat.value !== item.category) patch.category = cat.value;
      if (loc.value && loc.value !== item.location) patch.location = loc.value;
      if (src.value !== item.source) patch.source = src.value;
      if (day.value && day.value !== item.got_on) patch.got_on = day.value;
      if (note.value.trim() !== item.note) patch.note = note.value.trim();
      if (!Object.keys(patch).length) { S.editing = null; rerender(); return; }
      save.disabled = true;
      try {
        S.data = await api(`/api/room/items/${item.id}`, { method: "PATCH", body: JSON.stringify(patch) });
        S.editing = null;
        setState("已保存");
        rerender();
      } catch (e) {
        save.disabled = false;
        setState(`保存失败：${e.message}`, true);
      }
    });
    ops.append(save, cancel);
    box.appendChild(ops);
    return box;
  }

  /* ───────────────── 手动添加 ───────────────── */
  function addForm() {
    const d = S.data;
    const kind = select([["item", "一件小物件"], ["book", "一本摆设书（不可阅读，外观随机）"]], "item");
    const name = input("", "比如：一盆小多肉", 30);
    const cat = select([["", "（让 Phal 自己归类）"], ...d.categories.map((c) => [c.name, c.name === FOOD_CAT ? `${c.name}（她可能会吃掉）` : c.name])], "");
    const loc = select([["", "（让她自己决定摆哪）"], ...d.areas.map((a) => [a, a])], "");
    const src = select(d.sources.map((s) => [s.key, s.label]), "gift");
    const day = dateInput(d.today);
    const note = input("", "可以留空", 60);
    const box = el("div", "rm-card rm-card--edit rm-card--add");
    const rows = formRows([["放什么", kind], ["名称", name], ["分类", cat], ["位置", loc], ["来源", src], ["获得日期", day], ["她的备注", note]]);
    box.appendChild(rows);
    // 分类选「食物」：位置只能书桌 / 食物桌；种类留空 = 她按名字判断；保质期留空 = 按种类随机；分几次留空 = 随机
    const fKind = select([["", "（她按名字判断）"], ...CLUTTER_KINDS.map((k) => [k.key, k.label])], "");
    const fLife = input("", "留空 = 按种类随机", 8);
    fLife.type = "number"; fLife.min = "0.5"; fLife.step = "0.5";
    const fParts = input("", "留空 = 随机", 4);
    fParts.type = "number"; fParts.min = "1"; fParts.max = "20";
    const foodRows = formRows([["种类", fKind], ["保质期（小时）", fLife], ["分几次吃完", fParts]]);
    foodRows.style.display = "none";
    box.appendChild(foodRows);
    const hint = el("p", "rm-hint", "分类和位置留空时，会让模型按分类表和区域表替她选；没接入模型就归到「其他」，放在常规的位置。");
    box.appendChild(hint);
    const isFood = () => kind.value === "item" && cat.value === FOOD_CAT;
    const setLocs = (food) => {
      const list = food ? (d.food_locations || ["书桌", "食物桌"]) : d.areas;
      const keep = list.includes(loc.value) ? loc.value : "";
      loc.innerHTML = "";
      [["", food ? "（让她自己决定，默认食物桌）" : "（让她自己决定摆哪）"], ...list.map((a) => [a, a])].forEach(([v, l]) => { const o = el("option", null, l); o.value = v; loc.appendChild(o); });
      loc.value = keep;
    };
    let wasFood = false;
    const syncFood = () => {
      const food = isFood();
      foodRows.style.display = food ? "" : "none";
      [...rows.children].forEach((n, i) => { if (i >= 10 && kind.value !== "book") n.style.display = food ? "none" : ""; });   // 获得日期、备注（每项是 标签 + 输入 两个节点）
      if (food !== wasFood) { setLocs(food); wasFood = food; }
      if (food) hint.textContent = "食物放在书桌或食物桌上，她边做事时可能拿来吃（心情差、累的时候更想吃），可以分几次吃完；过了保质期她不会再吃，睡前清理桌子时收掉。之后可以改。";
      else if (kind.value !== "book") hint.textContent = "分类和位置留空时，会让模型按分类表和区域表替她选；没接入模型就归到「其他」，放在常规的位置。";
    };
    cat.addEventListener("change", syncFood);
    kind.addEventListener("change", () => {
      const book = kind.value === "book";
      [...rows.children].forEach((n, i) => { if (i >= 4) n.style.display = book ? "none" : ""; });
      name.placeholder = book ? "书名，比如：夜航船" : "比如：一盆小多肉";
      hint.textContent = book ? "摆设书只是摆着（不可阅读），外观随机生成，放上书架；书架放满会堆在地上。之后可以改书名、换外观、删除。" : "分类和位置留空时，会让模型按分类表和区域表替她选；没接入模型就归到「其他」，放在常规的位置。";
      if (book) foodRows.style.display = "none"; else syncFood();
    });
    const ops = el("div", "rm-form__ops");
    const ok = el("button", "btn btn--mini", "放进房间");
    ok.type = "button";
    const cancel = el("button", "btn btn--ghost btn--mini", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", closePop);
    ok.addEventListener("click", async () => {
      if (!name.value.trim()) { name.focus(); return; }
      ok.disabled = true;
      setState("正在放进房间…");
      try {
        if (kind.value === "book") {
          S.data = await api("/api/room/books", { method: "POST", body: JSON.stringify({ title: name.value.trim() }) });
          setState("书放上书架了");
          closePop();
          return;
        }
        if (isFood()) {
          const life = fLife.value === "" ? null : Number(fLife.value), parts = fParts.value === "" ? null : Math.round(Number(fParts.value));
          if (life !== null && !(life > 0)) { ok.disabled = false; fLife.focus(); setState("保质期要大于 0", true); return; }
          const r = await api("/api/room/items", { method: "POST", body: JSON.stringify({ name: name.value.trim(), category: FOOD_CAT, location: loc.value, source: src.value,
            kind: fKind.value || null, expires_h: life, portions: parts && parts > 0 ? parts : null }) });
          S.data = r;
          setState(`放好了：${r.item.area}（${r.item.label}，${r.item.expires_text}）`);
          closePop();
          flash(r.item.area);
          return;
        }
        const r = await api("/api/room/items", { method: "POST", body: JSON.stringify({
          name: name.value.trim(), category: cat.value, location: loc.value, source: src.value, got_on: day.value, note: note.value.trim() }) });
        S.data = r;
        setState(`已放进房间：${r.item.location}`);
        closePop();
        flash(r.item.location);
      } catch (e) {
        ok.disabled = false;
        setState(`添加失败：${e.message}`, true);
      }
    });
    ops.append(ok, cancel);
    box.appendChild(ops);
    setTimeout(() => name.focus(), 30);
    return box;
  }

  /* ───────────────── 书：封面、修改详情（右键）、收纳 / 书架管理 ───────────────── */
  const KIND_LABEL = { imported: "导入的书", phal: "Phal 自己买的（摆设书，不可阅读）", user: "你手动加的摆设书（不可阅读）" };
  const head8 = (t) => window.PhalIso.head8(t);                              // 书架、封面、列表上都只显示书名的前 8 个字
  const bkey = (b) => (b.kind === "imported" ? b.id : `p${b.pid}`);
  const findBook = (key) => { const l = S.data.books; return l.shelf.concat(l.floor, l.storage).find((x) => bkey(x) === key) || null; };

  function coverBox(b, px) {
    const w = el("div", "rm-cover");
    w.innerHTML = window.PhalIso.coverStandalone(b.look, b.title, b.author, px || 200);
    return w;
  }

  async function patchBook(pid, body, msg) {
    S.data = await api(`/api/room/books/${pid}`, { method: "PATCH", body: JSON.stringify(body) });
    if (msg) setState(msg);
    rerender();
  }

  /** 点一下书：看封面。要改书名 / 作者 / 外观、收纳、删除，点「修改详情」或者直接右键这本书。 */
  function bookView(b) {
    const box = el("div", "rm-book");
    box.appendChild(coverBox(b, 210));
    const side = el("div", "rm-book__info");
    side.appendChild(el("div", "rm-book__title", `《${head8(b.title)}》`));
    if (b.author) side.appendChild(el("div", "rm-book__author", b.author));
    side.appendChild(el("div", "rm-book__kind", KIND_LABEL[b.kind] || ""));
    const meta = [];
    if (b.got_on) meta.push(`${b.got_on} 买的`);
    if (b.price) meta.push(`${(b.price / 10).toFixed(1)} 钱`);
    if (meta.length) side.appendChild(el("div", "rm-book__meta", meta.join(" · ")));
    if (b.note) side.appendChild(el("div", "rm-card__note", `「${b.note}」`));
    const ops = el("div", "rm-book__ops");
    const edit = el("button", "btn btn--mini", "修改详情");
    edit.type = "button";
    edit.addEventListener("click", () => openPop({ kind: "bookedit", book: b }));
    ops.appendChild(edit);
    if (b.kind === "imported") {
      const open = el("button", "btn btn--ghost btn--mini", "去阅读器打开");
      open.type = "button";
      open.addEventListener("click", () => { closePop(); close(); const br = document.getElementById("btnReader"); if (br) br.click(); });
      ops.appendChild(open);
    }
    side.appendChild(ops);
    side.appendChild(el("div", "rm-book__tip", "右键一本书也可以直接修改详情。"));
    box.appendChild(side);
    return box;
  }

  /** 修改书籍详情：书名、作者、（摆设书）备注、外观、放在哪、删除。书架上最多显示书名的前 8 个字，这里是完整的书名。 */
  function bookEdit(b) {
    const imported = b.kind === "imported", key = bkey(b);
    const box = el("div", "rm-book rm-book--edit");
    box.appendChild(coverBox(b, 120));
    const side = el("div", "rm-book__info");
    const t = input(b.title, "书名", imported ? 80 : 20), a = input(b.author, "作者", imported ? 40 : 12), n = input(b.note, "备注（可以不写）", 60);
    const fields = [["书名", t], ["作者", a]];
    if (!imported) fields.push(["备注", n]);
    side.appendChild(formRows(fields));
    const ops = el("div", "rm-book__ops");
    const save = el("button", "btn btn--mini", "保存");
    save.type = "button";
    save.addEventListener("click", async () => {
      if (!t.value.trim()) { t.focus(); return; }
      save.disabled = true;
      try {
        if (imported) {
          await api(`/api/books/${b.book_id}`, { method: "PATCH", body: JSON.stringify({ title: t.value.trim(), author: a.value.trim() }) });
          S.data = await api("/api/room");
        } else {
          S.data = await api(`/api/room/books/${b.pid}`, { method: "PATCH", body: JSON.stringify({ title: t.value.trim(), author: a.value.trim(), note: n.value.trim() }) });
        }
        setState("已保存");
        S.pop = { kind: "book", book: findBook(key) };
        rerender();
      } catch (e) { save.disabled = false; setState(e.message, true); }
    });
    const back = el("button", "btn btn--ghost btn--mini", "取消");
    back.type = "button";
    back.addEventListener("click", () => openPop({ kind: "book", book: b }));
    ops.append(save, back);
    side.appendChild(ops);
    if (!imported) {
      const more = el("div", "rm-book__ops");
      const rr = el("button", "rm-op", "换个外观");
      rr.type = "button";
      rr.addEventListener("click", async () => { try { await patchBook(b.pid, { reroll: true }, "换了外观"); S.pop = { kind: "bookedit", book: findBook(key) }; renderPop(); } catch (e) { setState(e.message, true); } });
      const inBasket = b.place === "storage";
      const st = el("button", "rm-op", inBasket ? "放回书架" : "收进收纳篮");
      st.type = "button";
      st.addEventListener("click", async () => { try { await patchBook(b.pid, { place: inBasket ? "shelf" : "storage" }, inBasket ? "放回书架了" : "收进收纳篮了"); closePop(); } catch (e) { setState(e.message, true); } });
      more.append(rr, st);
      const lay = S.data.books;
      const mates = inBasket ? lay.shelf.concat(lay.floor).filter((x) => x.kind !== "imported") : lay.storage;
      if (mates.length) {
        const sw = select([["", inBasket ? "和书架上的书交换…" : "和收纳篮里的书交换…"], ...mates.map((x) => [String(x.pid), `《${head8(x.title)}》`])], "");
        sw.className = "input input--tiny rm-brow__swap";
        sw.addEventListener("change", async () => {
          if (!sw.value) return;
          try { S.data = await api("/api/room/books/swap", { method: "POST", body: JSON.stringify({ a: b.pid, b: Number(sw.value) }) }); setState("已交换"); closePop(); } catch (e) { setState(e.message, true); }
        });
        more.appendChild(sw);
      }
      more.appendChild(sureButton(`b${b.pid}`, async () => { S.data = await api(`/api/room/books/${b.pid}`, { method: "DELETE" }); setState("已删除"); closePop(); }));
      side.appendChild(more);
    }
    box.appendChild(side);
    return box;
  }

  /** 书架管理里的一本书：横版小卡片，只有封面（大一点）和书名的前 8 个字。点一下看封面，右键修改详情。 */
  function bookCard(b) {
    const r = el("div", "rm-bcard");
    r.title = "点一下看封面，右键修改详情";
    const c = coverBox(b, 72);
    c.classList.add("rm-bcard__cover");
    r.appendChild(c);
    r.appendChild(el("div", "rm-bcard__title", head8(b.title)));
    r.addEventListener("click", () => openPop({ kind: "book", book: b }));
    r.addEventListener("contextmenu", (ev) => { ev.preventDefault(); openPop({ kind: "bookedit", book: b }); });
    return r;
  }

  function shelfView(tab) {
    const lay = S.data.books, box = el("div", "rm-shelf");
    const tabs = el("div", "rm-shelf__tabs");
    [["shelf", `书架 ${lay.shelf.length}/${lay.caps.shelf}`], ["floor", `地上 ${lay.floor.length}/${lay.caps.floor}`], ["storage", `收纳篮 ${lay.storage.length}`]].forEach(([k, label]) => {
      const b = el("button", "rm-shelf__tab" + (tab === k ? " is-on" : ""), label);
      b.type = "button";
      b.addEventListener("click", () => { S.pop.tab = k; renderPop(); });
      tabs.appendChild(b);
    });
    box.appendChild(tabs);
    const tips = { shelf: "书脊朝外摆在书架上的书。导入的书在前，她买的摆设书在后；放满了的堆到地上。", floor: "书架放满后，横着堆在椅子旁边靠墙的地上，略显凌乱。",
      storage: "她收纳起来的书：不对外展示（房间里只看得到一个篮子），但一直保存。" };
    box.appendChild(el("p", "rm-hint", `${tips[tab]}点一下看封面，右键一本书修改书名、作者、换外观、收纳 / 放回、交换、删除。`));
    const list = tab === "storage" ? lay.storage : lay[tab];
    if (!list.length) box.appendChild(el("div", "rm-hint", tab === "storage" ? "收纳篮是空的。" : "这里没有书。"));
    else {
      const grid = el("div", "rm-bgrid");
      list.forEach((b) => grid.appendChild(bookCard(b)));
      box.appendChild(grid);
    }
    if (tab === "shelf") {
      const area = itemsIn("书架");
      if (area.length) {
        box.appendChild(el("h4", "rm-shelf__sub", "书架上放着的小物件"));
        area.forEach((i) => box.appendChild(card(i, { showCat: true })));
      }
    }
    return box;
  }

  /* ───────────────── 弹层 ───────────────── */
  function openPop(pop) { S.pop = pop; S.editing = null; S.sure = null; renderPop(); }
  function closePop() { S.pop = null; S.editing = null; S.sure = null; renderPop(); render(); }

  function renderPop() {
    const pop = $("#roomPop");
    if (!S.pop || !S.data) { pop.hidden = true; return; }
    const body = $("#roomPopBody"), title = $("#roomPopTitle");
    body.innerHTML = "";
    title.innerHTML = "";
    pop.hidden = false;
    pop.classList.toggle("is-wide", S.pop.kind === "shelf");
    if (S.pop.kind === "add") { title.textContent = "添加物品"; body.appendChild(addForm()); return; }
    if (S.pop.kind === "book" || S.pop.kind === "bookedit") {
      const b = S.pop.book;
      if (!b) { closePop(); return; }
      title.appendChild(el("span", "rm-pop__area", S.pop.kind === "bookedit" ? "修改书籍详情" : "书"));
      body.appendChild(S.pop.kind === "bookedit" ? bookEdit(b) : bookView(b));
      return;
    }
    if (S.pop.kind === "shelf") {
      title.appendChild(el("span", "rm-pop__area", "书架"));
      body.appendChild(shelfView(S.pop.tab || "shelf"));
      return;
    }
    if (isClutter(S.pop.area)) {
      const cl = foodIn(S.pop.area);
      title.appendChild(el("span", "rm-pop__area", S.pop.area));
      title.appendChild(el("span", "rm-pop__n", `${cl.length} 件`));
      body.appendChild(el("p", "rm-hint", "放着的吃的。她边做事边可能拿来吃（心情差、累的时候更想吃）；过期的不会再吃，睡前清理桌子时收掉。"));
      if (!cl.length) body.appendChild(el("div", "rm-hint", "桌上现在是空的。"));
      cl.forEach((c) => body.appendChild(clutterCard(c)));
      return;
    }
    const list = itemsIn(S.pop.area, S.pop.cat);
    title.appendChild(el("span", "rm-pop__area", S.pop.area));
    if (S.pop.cat) {
      title.appendChild(el("span", "rm-pop__sep", "·"));
      title.appendChild(icon(catOf(S.pop.cat).icon));
      title.appendChild(el("span", null, S.pop.cat));
    }
    title.appendChild(el("span", "rm-pop__n", `${list.length} 件`));
    if (!list.length) body.appendChild(el("div", "rm-hint", "这里没有东西了。"));
    list.forEach((i) => body.appendChild(card(i, { showCat: !S.pop.cat })));
  }

  function flash(area) {
    setTimeout(() => {
      const t = ROOT.querySelector(`.rm-zone[data-area="${CSS.escape(area)}"]`);
      if (!t) return;
      if (S.showTag) S.showTag(area);
      t.classList.add("is-new");
      setTimeout(() => { t.classList.remove("is-new", "is-show"); }, 2200);
    }, 50);
  }

  /* ───────────────── 总渲染 ───────────────── */
  function render() {
    const main = $("#roomMain");
    main.innerHTML = "";
    if (!S.data) return;
    const desk = viewIs("desk");
    main.classList.toggle("is-desk", desk);
    const box = el("div", "roompage__inner" + (S.view === "cat" ? " roompage__inner--cat" : desk ? " roompage__inner--desk" : " roompage__inner--iso"));
    (S.view === "cat" ? renderCats : desk ? renderDesk : renderPlan)(box);
    main.appendChild(box);
    const nb = S.data.books.shelf.length + S.data.books.floor.length + S.data.books.storage.length;
    $("#roomCount").textContent = S.data.items.length || nb ? `${S.data.items.length} 件 · ${nb} 本书` : "";
    ROOT.querySelectorAll(".roompage__tab").forEach((b) => {
      const on = b.dataset.view === S.view;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (S.view !== "plan") { if (S.actor) S.actor.detach(); S.scene = null; }
    if (!desk && window.PhalWeatherFX) window.PhalWeatherFX.unmount();
    ROOT.querySelectorAll(".roompage__style button").forEach((b) => b.classList.toggle("is-on", b.dataset.style === assetStyle()));
    ROOT.querySelector(".roompage__style").hidden = S.view !== "plan";
  }

  function rerender() {
    // 弹层里的改动（删物品、收纳书……）只刷新弹层和房间；房间重画时角色会回到自己的位置
    render();
    renderPop();
  }

  async function loadAssets() {
    try { S.assets = await api("/api/assets"); } catch (_) { S.assets = { available: false }; }
  }

  async function load() {
    S.loading = true;
    try {
      if (!S.assets) await loadAssets();
      if (!S.tuning) { try { S.tuning = (await api("/api/room/pose-tuning")).tuning || {}; } catch (_) { S.tuning = {}; } }     // 姿势调试器调过的数值
      S.data = await api("/api/room");
      S.ambKey = ambKey(S.data);
      rerender();
    } catch (e) {
      setState(`读取失败：${e.message}`, true);
    } finally {
      S.loading = false;
    }
  }

  /** 每分钟看一眼：钟点换了（窗外该变了、夜里该暗了）、天气变了、杂物区有东西到点收掉 / 新买了 → 重画。有弹层开着时先不动。 */
  const ambKey = (d) => `${new Date().getHours()}|${d.ambient && d.ambient.kind}|${(d.clutter || []).map((c) => c.id).join(",")}`;
  let ambTimer = null;
  async function ambTick() {
    if (ROOT.hidden || S.loading || S.pop || S.editing || !S.data) return;
    try {
      const d = await api("/api/room");
      if (ambKey(d) === S.ambKey) return;
      S.data = d;
      S.ambKey = ambKey(d);
      render();
    } catch (_) { /* 下一分钟再试 */ }
  }
  function open() {
    S.view = LS.get("phal.room.view", "plan") === "cat" ? "cat" : "plan";
    S.rview = "room";
    ROOT.hidden = false;
    S.assets = null;                       // 每次打开重新读素材清单（你替换了素材，刷新就生效）
    load().then(() => { pollLife(); clearInterval(lifeTimer); lifeTimer = setInterval(pollLife, 8000); clearInterval(ambTimer); ambTimer = setInterval(ambTick, 60000); });
  }
  function close() {
    S.pop = null; S.editing = null; S.sure = null; ROOT.hidden = true; renderPop();
    clearInterval(lifeTimer);
    clearInterval(ambTimer);
    if (S.actor) S.actor.pause();
    syncWeather();
  }

  ROOT.querySelectorAll(".roompage__tab").forEach((b) => b.addEventListener("click", () => {
    S.view = b.dataset.view;
    LS.set("phal.room.view", S.view);
    render();
    if (S.view === "plan") pollLife();
  }));
  $("#roomBack").addEventListener("click", () => { if (viewIs("desk")) setRoomView("room"); else close(); });
  ROOT.querySelectorAll(".roompage__style button").forEach((b) => b.addEventListener("click", () => {
    LS.set("phal.room.style", b.dataset.style);
    try { const u = new URL(location.href); if (u.searchParams.has("roomstyle")) { u.searchParams.delete("roomstyle"); history.replaceState(null, "", u); } } catch (_) { /* 没有 URL 参数就算了 */ }     // 地址里的 ?roomstyle= 优先级最高，点了按钮就以按钮为准
    render();
  }));
  $("#roomAdd").addEventListener("click", () => { if (S.data) openPop({ kind: "add" }); });
  $("#roomPopClose").addEventListener("click", closePop);
  $("#roomPop").addEventListener("click", (e) => { if (e.target === e.currentTarget) closePop(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || ROOT.hidden) return;
    if (S.pop) closePop(); else if (viewIs("desk")) setRoomView("room"); else close();
  });
  document.addEventListener("phal:room-changed", () => { if (!ROOT.hidden && !S.loading) load(); });
  // 窗口隐藏 / 最小化时暂停角色动画；回来继续
  // 桌面版关掉了后台降频，页面的 visibilitychange 不一定会触发，所以外壳另外发 phal:window-visible
  const syncPlayback = () => {
    syncWeather();
    const ch = S.actor;
    if (!ch) return;
    if (windowHidden() || ROOT.hidden) ch.pause(); else ch.resume();
  };
  document.addEventListener("visibilitychange", syncPlayback);
  document.addEventListener("phal:window-visible", (e) => { S.winHidden = !(e.detail && e.detail.visible); syncPlayback(); });

  /** 对外的接口（别的模块 / 调试用）：
   *  setView("room" | "desk")：切视角；setDeskState("A" | "B" | null)：强制书桌上笔记本的状态（null = 跟着她的状态走）；
   *  setAmbient({ hour, kind, sunrise, sunset } | null)：强制窗外的钟点 / 天气（试夜里、雨天的样子；null = 用真实的）；
   *  setState(key, instant, variant)：让她切到某个状态（只改画面，不动她真实的生活状态；instant = true 就直接出现在那里，不走过去；variant = 指定第几种做法，比如发呆 0 站着 / 1 书桌边 / 2 沙发上）；setStyle("A" | "B" | null)。 */
  window.PhalRoom = {
    open, close, reload: load, debug: () => S,
    setView: (k) => setRoomView(k === "desk" ? "desk" : "room"),
    setDeskState: (v) => { S.deskForce = v || null; S.deskState = deskStateFor(S.life ? S.life.key : ""); if (S.data && S.view === "plan") { render(); renderPop(); } },
    setState: (key, instant, variant) => { if (S.actor) S.actor.setState(key, "", !!instant, variant); const ds = deskStateFor(key); if (ds !== S.deskState) { S.deskState = ds; if (S.data && S.view === "plan") render(); } },
    setAmbient: (a) => { S.ambForce = a || null; if (S.data && S.view === "plan") render(); },
    setStyle: (id) => { LS.set("phal.room.style", id || ""); if (S.data) render(); },
    // 姿势调试器（web/posetune.js）用：锁定 / 解锁某个状态、读写调过的数值
    lockPose: (key, variant) => {
      S.poseLock = key ? { key, variant: Number.isInteger(variant) ? variant : undefined } : null;
      const k = key || (S.life && S.life.key);
      if (S.actor && k) S.actor.setState(k, key ? "" : (S.life && S.life.label) || "", true, key ? S.poseLock.variant : undefined);
      const ds = deskStateFor(k || "");
      if (ds !== S.deskState) { S.deskState = ds; if (S.data && S.view === "plan") render(); }
    },
    getTuning: () => S.tuning || {},
    setTuning: (t) => { S.tuning = t || {}; if (S.actor) S.actor.refresh(); },
    actor: () => S.actor,
    manifest: () => (S.assets && S.assets.manifest) || null,
    view: () => (S.view === "plan" ? S.rview : S.view),
  };
})();
