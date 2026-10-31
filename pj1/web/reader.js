/* 书籍阅读器：书架 + 实体书样式阅读。纯原生 JS。
 *
 * 位置（进度 / 书签 / 注释）一律是「章节 + 章节文本里的字符偏移」，和分页无关，
 * 所以字号、窗口大小怎么变，都能回到原位。
 */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const $$all = (s) => [...document.querySelectorAll(s)];
  const ROOT = $("#readerRoot");
  if (!ROOT) return;
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* 无痕模式 */ } },
  };

  const S = {
    book: null, toc: [], bookmarks: [],
    chapter: 0, data: null,            // 当前章节 {n,total,title,text,annotations}
    spread: 0, spreads: 1, cols: 2, step: 0, gap: 0, colW: 0,
    fs: Number(LS.get("reader.fs", 18)),
    night: LS.get("reader.night", "0") === "1",
    notes: LS.get("reader.notes", "1") !== "0",
    flipping: false, saveTimer: null, currentAnn: null,
    img: false,                          // 图片版（扫描 pdf）：一页一张图
    ink: {},                             // 我的笔迹 {页 key: [笔画…]}
    pen: { on: false, color: "#2f2a24", width: 2.4, hl: false },
  };

  /* ───────── 小工具 ───────── */
  async function api(path, opts = {}) {
    const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = j.detail || detail; } catch (_) { /* 非 JSON */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }
  let toastTimer = null;
  function toast(msg) {
    const t = $("#rtoast");
    t.textContent = msg;
    t.classList.add("is-on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("is-on"), 2200);
  }
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const COVERS = ["#e3ddd3", "#d9e0d9", "#e0d9df", "#d8dee7", "#e8ddd0", "#e2e2d3", "#e4d8d8", "#d6e1e3"];
  const coverOf = (id) => COVERS[id % COVERS.length];

  /* ───────── 书架 ───────── */
  async function loadShelf() {
    const grid = $("#shelfGrid");
    grid.innerHTML = "";
    let books = [];
    try { books = (await api("/api/books")).books; } catch (err) { toast(`书架加载失败：${err.message}`); }
    if (!books.length) {
      grid.appendChild(el("div", "empty", "书架还是空的。上传一本 epub、txt 或 pdf 吧。"));
      return;
    }
    books.forEach((b) => {
      const card = el("div", "bookcard");
      const cover = el("button", "bookcard__cover");
      cover.type = "button";
      cover.style.setProperty("--cover", coverOf(b.id));
      cover.append(el("div", "bookcard__t", b.title), el("div", "bookcard__a", b.author || ""));
      cover.addEventListener("click", () => openBook(b.id));
      const meta = el("div", "bookcard__meta");
      const bar = el("div", "bar");
      const fill = document.createElement("i");
      fill.style.width = `${b.my_percent}%`;
      bar.appendChild(fill);
      const mine = el("div", null, b.my_percent ? `我读到 ${b.my_percent}%` : "还没开始读");
      const phal = el("div");
      const tag = el("span", "phalstat", b.fmt === "pdfimg" ? "图片版 · Phal 看不了字" : `Phal：${b.phal_status_label}`);
      phal.append(tag);
      if (b.phal_status === "reading" || b.phal_status === "paused") phal.append(document.createTextNode(` ${b.phal_percent}%`));
      meta.append(bar, mine, phal);
      const del = el("button", "bookcard__del", "×");
      del.type = "button";
      del.title = "从书架删除";
      del.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`把《${b.title}》从书架删掉？\n书的文件、书签和 Phal 写的注释、笔记都会一起删除。`)) return;
        try { await api(`/api/books/${b.id}`, { method: "DELETE" }); loadShelf(); } catch (err) { toast(`删除失败：${err.message}`); }
      });
      card.append(cover, meta, del);
      grid.appendChild(card);
    });
  }

  async function uploadFiles(files) {
    const label = $("#uploadLabel");
    label.classList.add("is-busy");
    let ok = 0;
    for (const f of files) {
      $("#shelfHint").textContent = `正在解析《${f.name}》…`;
      try {
        await api(`/api/books?filename=${encodeURIComponent(f.name)}`, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: f,
        });
        ok += 1;
      } catch (err) {
        toast(`${f.name}：${err.message}`);
      }
    }
    label.classList.remove("is-busy");
    $("#shelfHint").textContent = "书只存在你自己的电脑上（data/books），不会上传到任何地方。";
    if (ok) toast(`已放上书架 ${ok} 本`);
    loadShelf();
  }

  function showShelf() {
    $("#readView").hidden = true;
    $("#shelfView").hidden = false;
    $("#rTools").hidden = true;
    $("#rTitle").textContent = "";
    document.title = "书架";
    history.replaceState(null, "", "/reader");
    closeDrawer();
    clearSearch();
    loadShelf();
  }

  /* ───────── 打开一本书 ───────── */
  async function openBook(id, at) {
    try {
      const info = await api(`/api/books/${id}`);
      S.book = info.book;
      S.toc = info.toc;
      S.bookmarks = info.bookmarks;
    } catch (err) {
      toast(`打开失败：${err.message}`);
      return;
    }
    S.img = S.book.fmt === "pdfimg";
    ROOT.classList.toggle("is-img", S.img);
    $("#flow").hidden = S.img;
    $("#pageImg").hidden = !S.img;
    $("#spread").classList.toggle("img", S.img);
    S.search = null;
    S.searchScope = "book";
    updateSearchInfo();
    S.ink = {};
    api(`/api/books/${id}/ink`).then((r) => { S.ink = r.ink || {}; renderInk(); }).catch(() => {});
    setPen(false);
    $("#shelfView").hidden = true;
    $("#readView").hidden = false;
    $("#rTools").hidden = false;
    $("#rTitle").textContent = S.book.title;
    document.title = S.book.title;
    history.replaceState(null, "", `/reader?book=${id}`);

    let ch = S.book.my_chapter, off = S.book.my_offset;
    if (at && typeof at === "object") { ch = at.chapter; off = at.offset; }
    else if (at === "phal") { ch = S.book.phal_chapter; off = S.book.phal_offset; }
    applyPrefs();
    await loadChapter(Math.min(ch, S.toc.length - 1), off);
  }

  async function loadChapter(n, target) {
    if (S.img) { await showImagePage(n); return; }
    try {
      S.data = await api(`/api/books/${S.book.id}/chapters/${n}`);
    } catch (err) {
      toast(`章节加载失败：${err.message}`);
      return;
    }
    S.chapter = n;
    buildFlow();
    relayout(target);
  }

  /* ───────── 排版：把一章渲染成按列分页的流 ───────── */
  function resolveAnchors(text, anns) {
    // 注释的位置是 偏移 + 原文片段：偏移处的文字对得上就用偏移，对不上就按片段重新找
    const out = [];
    anns.forEach((a) => {
      let off = a.offset;
      if (text.substr(off, a.snippet.length) !== a.snippet) {
        const idx = text.indexOf(a.snippet);
        if (idx < 0) return;
        off = idx;
      }
      out.push(Object.assign({}, a, { offset: off, length: a.snippet.length }));
    });
    return out.sort((x, y) => x.offset - y.offset);
  }

  function buildFlow() {
    const flow = $("#flow");
    flow.innerHTML = "";
    flow.appendChild(el("h2", "chapter-title", S.data.title));
    const anns = resolveAnchors(S.data.text, S.data.annotations || []);
    S.anns = anns;
    let pos = 0;
    S.data.text.split("\n").forEach((line) => {
      const p = document.createElement("p");
      p.dataset.o = String(pos);
      const end = pos + line.length;
      let cur = pos;
      for (const a of anns) {
        if (a.offset < cur || a.offset >= end) continue;
        const aEnd = Math.min(a.offset + a.length, end);
        if (a.offset > cur) p.appendChild(document.createTextNode(line.slice(cur - pos, a.offset - pos)));
        const m = el("mark", "ann", line.slice(a.offset - pos, aEnd - pos));
        m.dataset.id = String(a.id);
        m.dataset.o = String(a.offset);
        p.appendChild(m);
        cur = aEnd;
      }
      if (cur < end) p.appendChild(document.createTextNode(line.slice(cur - pos)));
      flow.appendChild(p);
      pos = end + 1;
    });
    applyHighlights();
  }

  function applyPrefs() {
    ROOT.classList.toggle("theme-night", S.night);
    ROOT.style.setProperty("--fs", `${S.fs}px`);
    $("#flow").classList.toggle("hide-notes", !S.notes);
    $("#btnNotes").classList.toggle("is-on", S.notes);
    $("#btnNight").classList.toggle("is-on", S.night);
  }

  function layout() {
    const vw = window.innerWidth, vh = window.innerHeight;
    S.cols = vw >= 820 ? 2 : 1;
    const W = Math.min(S.cols === 2 ? 1180 : 640, vw - (S.cols === 2 ? 40 : 16));
    const H = Math.max(360, vh - 62 - 70);
    // 外侧边距：双页时留得宽一点，Phal 的批注就写在这里（像真的在书页空白处写字）
    const padX = S.cols === 2 ? (W >= 1000 ? 140 : W >= 860 ? 96 : 56) : Math.max(22, Math.min(40, W * 0.07));
    S.gap = S.cols === 2 ? 72 : 0;
    S.padOut = padX;
    S.padY = 46;
    const root = ROOT.style;
    root.setProperty("--W", `${W}px`);
    root.setProperty("--H", `${H}px`);
    root.setProperty("--padX", `${padX}px`);
    root.setProperty("--padY", `${S.padY}px`);
    root.setProperty("--cols", String(S.cols));
    root.setProperty("--gap", `${S.gap}px`);
    $("#spread").classList.toggle("single", S.cols === 1);
    // 页面的实际宽度可能和算出来的差一点（窗口比 W 窄、系统缩放出小数、有滚动条……），翻一页就差一点点，
    // 翻到后面左页的开头几个字就被切掉了。所以以页面里真实量到的宽度为准。
    const flowW = $("#flow").getBoundingClientRect().width || W - 2 * padX;
    S.colW = (flowW - S.gap * (S.cols - 1)) / S.cols;
    S.step = flowW + S.gap;
  }

  function countSpreads() {
    const flow = $("#flow");
    flow.querySelectorAll(".flow-pad").forEach((n) => n.remove());
    let total = flow.scrollWidth + S.gap;                         // 所有列加起来（含列间距），整数
    let cols = Math.max(1, Math.round(total / (S.colW + S.gap)));
    if (cols % S.cols) {
      // 列数凑不满最后一页（双页时只剩一栏）：补一栏空白。不补的话，浏览器翻到底会把前一页的右栏顶到左边来，看上去像最后一页的字重复了
      const pad = el("div", "flow-pad");
      flow.appendChild(pad);
      total = flow.scrollWidth + S.gap;
      cols = Math.max(1, Math.round(total / (S.colW + S.gap)));
    }
    S.step = (total / cols) * S.cols;                             // 用总宽 / 列数反推每一页的真实步长，翻再多页也不会累积误差
    S.spreads = Math.max(1, Math.ceil(cols / S.cols));
  }

  function relayout(target) {
    if (!S.data) return;
    if (S.img) { layoutImage(); renderInk(); return; }
    layout();
    countSpreads();
    if (target === undefined) target = currentOffset();
    if (target === "last") setSpread(S.spreads - 1, false);
    else setSpread(spreadOfOffset(target || 0), false);
  }

  /* ───────── 位置 ↔ 页 ───────── */
  /** DOM 位置 (node, off) → 章节文本里的字符偏移。用 Range 量「段落开头到这里」有多少字。 */
  function offsetOf(node, off) {
    const base = node.nodeType === 1 ? node : node.parentElement;
    const p = base && base.closest ? base.closest("p[data-o]") : null;
    if (!p) return null;
    const r = document.createRange();
    r.selectNodeContents(p);
    try { r.setEnd(node, off); } catch (_) { return Number(p.dataset.o); }
    return Number(p.dataset.o) + r.toString().length;
  }

  function caretAt(x, y) {
    if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y);
    if (document.caretPositionFromPoint) {
      const c = document.caretPositionFromPoint(x, y);
      if (!c) return null;
      const r = document.createRange();
      r.setStart(c.offsetNode, c.offset);
      return r;
    }
    return null;
  }

  /** 当前页（左页最上面）第一个字符在章节里的偏移。 */
  function currentOffset() {
    const flow = $("#flow");
    if (!flow.firstChild) return 0;
    const r = flow.getBoundingClientRect();
    for (const dy of [8, 24, 48, 80]) {
      const range = caretAt(r.left + 8, r.top + dy);
      if (range && flow.contains(range.startContainer)) {
        const o = offsetOf(range.startContainer, range.startOffset);
        if (o != null) return o;
      }
    }
    for (const p of flow.querySelectorAll("p[data-o]")) {
      const pr = p.getBoundingClientRect();
      if (pr.right > r.left + 4 && pr.left < r.left + S.colW) return Number(p.dataset.o);
    }
    return 0;
  }

  function spreadOfOffset(off) {
    const flow = $("#flow");
    const ps = flow.querySelectorAll("p[data-o]");
    if (!ps.length || off <= 0) return 0;
    let lo = 0, hi = ps.length - 1;
    while (lo < hi) {            // 二分：最后一个 o <= off 的段落
      const mid = (lo + hi + 1) >> 1;
      if (Number(ps[mid].dataset.o) <= off) lo = mid; else hi = mid - 1;
    }
    const p = ps[lo];
    let rect = null;
    let remain = off - Number(p.dataset.o);
    const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    while (w.nextNode()) {
      const len = w.currentNode.length;
      if (remain <= len) {
        const range = document.createRange();
        const at = Math.min(remain, Math.max(0, len - 1));
        range.setStart(w.currentNode, at);
        range.setEnd(w.currentNode, Math.min(len, at + 1));
        rect = range.getBoundingClientRect();
        break;
      }
      remain -= len;
    }
    if (!rect || (!rect.width && !rect.height)) rect = p.getBoundingClientRect();
    const fr = flow.getBoundingClientRect();
    const x = rect.left - fr.left + flow.scrollLeft;
    return Math.max(0, Math.min(S.spreads - 1, Math.floor((x + 2) / S.step)));
  }

  function setSpread(i, animate, dir) {
    const flow = $("#flow");
    i = Math.max(0, Math.min(S.spreads - 1, i));
    const apply = () => {
      S.spread = i;
      flow.scrollLeft = i * S.step;
      afterMove();
    };
    if (animate && !S.flipping && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      S.flipping = true;
      const turn = $("#turn");
      turn.className = `turn ${dir === "prev" ? "is-prev" : "is-next"}${S.cols === 1 ? " single" : ""}`;
      turn.hidden = false;
      apply();
      setTimeout(() => { turn.hidden = true; S.flipping = false; }, 430);
    } else {
      apply();
    }
  }

  function afterMove() {
    const off = currentOffset();
    $("#pageInfo").textContent = `${S.data.title} · 第 ${S.spread + 1} / ${S.spreads} 页　（第 ${S.chapter + 1} / ${S.data.total} 章）`;
    const phal = $("#phalInfo");
    phal.textContent = "";
    if (S.book.phal_status && S.book.phal_status !== "unread") {
      const here = S.book.phal_chapter === S.chapter;
      phal.textContent = `Phal：${S.book.phal_status_label || ""}${here ? "（她读到这一章了）" : ""}`;
      phal.className = "phalmark";
    }
    const star = S.bookmarks.some((b) => b.chapter === S.chapter && spreadOfOffset(b.offset) === S.spread);
    $("#btnMarkHere").textContent = star ? "★" : "☆";
    renderHandNotes();
    renderInk();
    clearTimeout(S.saveTimer);
    S.saveTimer = setTimeout(() => {
      api(`/api/books/${S.book.id}/progress`, { method: "PUT", body: JSON.stringify({ chapter: S.chapter, offset: off }) }).catch(() => {});
      S.book.my_chapter = S.chapter;
      S.book.my_offset = off;
    }, 700);
    S.lastOffset = off;
  }

  /* ───────── 翻页 ───────── */
  async function next() {
    if (S.flipping || !S.data) return;
    if (S.spread < S.spreads - 1) setSpread(S.spread + 1, true, "next");
    else if (S.chapter < S.data.total - 1) { await loadChapter(S.chapter + 1, 0); flashTurn("next"); }
    else toast("已经是最后一页了");
  }
  async function prev() {
    if (S.flipping || !S.data) return;
    if (S.spread > 0) setSpread(S.spread - 1, true, "prev");
    else if (S.chapter > 0) { await loadChapter(S.chapter - 1, "last"); flashTurn("prev"); }
  }
  function flashTurn(dir) {
    const turn = $("#turn");
    turn.className = `turn ${dir === "prev" ? "is-prev" : "is-next"}${S.cols === 1 ? " single" : ""}`;
    turn.hidden = false;
    S.flipping = true;
    setTimeout(() => { turn.hidden = true; S.flipping = false; }, 430);
  }

  async function goTo(chapter, offset) {
    closeDrawer();
    if (chapter !== S.chapter) await loadChapter(chapter, offset);
    else if (!S.img) setSpread(spreadOfOffset(offset), false);
  }

  /* ───────── Phal 的注释：下划线 + 页边手写批注 ───────── */
  /* ───── Phal 的批注：像手写在书页空白处 ─────
     * 字一个个带着随机的角度 / 大小 / 基线抖动；有的横写在页边，有的沿页边竖着写，靠近页首页尾的写在天头地脚；
     * 再用一条手绘的小曲线指向被划线的原文。位置随性，但同一条批注每次摆的地方、字的样子都一样（按它的 id 定）。 */
  function rng(seed) {
    let a = (seed * 2654435761) >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function handWrite(node, text, rand, vertical) {
    for (const ch of text) {
      if (ch === " ") { node.appendChild(document.createTextNode(" ")); continue; }
      const c = el("span", "hc", ch);
      const rot = (rand() - 0.5) * 8;
      const off = (rand() - 0.5) * 3.2;
      const sc = 0.9 + rand() * 0.24;
      c.style.transform = `${vertical ? `translateX(${off}px)` : `translateY(${off}px)`} rotate(${rot}deg) scale(${sc})`;
      node.appendChild(c);
    }
  }

  function renderHandNotes() {
    const layer = $("#notesLayer");
    layer.innerHTML = "";
    if (!S.notes || !S.data) return;
    const flow = $("#flow");
    const sp = $("#spread").getBoundingClientRect();
    const fr = flow.getBoundingClientRect();
    const W = sp.width, H = sp.height, m = S.padOut, padY = S.padY;
    const wide = S.cols === 2 && m >= 90;
    const byId = new Map(S.anns.map((a) => [String(a.id), a]));

    const items = [];
    flow.querySelectorAll("mark.ann").forEach((mk) => {
      const r = mk.getBoundingClientRect();
      if (r.left < fr.left - 2 || r.left >= fr.right) return;            // 不在这一页
      const a = byId.get(mk.dataset.id);
      if (!a) return;
      const rel = { x: r.left - sp.left, y: r.top - sp.top, w: r.width, h: r.height };
      const left = S.cols === 2 ? r.left - fr.left < S.colW + S.gap / 2 : false;
      items.push({ a, rel, left });
    });
    items.sort((p, q) => p.rel.y - q.rel.y);

    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("class", "notes-svg");
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    layer.appendChild(svg);

    const taken = { l: [], r: [], t: [], b: [] };      // 各个空白区已经占掉的范围，免得写重叠
    const free = (zone, a0, a1) => taken[zone].every(([x0, x1]) => a1 < x0 - 6 || a0 > x1 + 6);

    items.forEach(({ a, rel, left }, i) => {
      const rand = rng(a.id);
      const text = a.note;
      const nearTop = rel.y < padY + 52;
      const nearBottom = rel.y > H - padY - 70;
      const pick = rand();
      let mode = "block";
      if (!wide) mode = "tick";
      else if (nearTop && pick < 0.75) mode = "top";
      else if (nearBottom && pick < 0.75) mode = "bottom";
      else if (text.length > 13 && pick < 0.55) mode = "vertical";

      const note = el("div", `hnote hnote--${mode}`);
      note.style.setProperty("--rot", `${((rand() - 0.5) * 7).toFixed(2)}deg`);
      note.style.animationDelay = `${i * 90}ms`;
      note.addEventListener("click", (e) => showAnn(a, e));
      let zone = left ? "l" : "r";
      let sx, sy, ex = left ? rel.x - 3 : rel.x + rel.w + 3, ey = rel.y + rel.h * 0.6;

      // 放不下 / 窄屏没有空白可写：页边留一个小记号，点开看
      const addTick = () => {
        const tick = el("div", "hnote hnote--tick", "✎");
        tick.style.setProperty("--rot", "-6deg");
        tick.addEventListener("click", (e) => showAnn(a, e));
        tick.style.left = `${S.cols === 2 ? (left ? 8 : W - 28) : W - 24}px`;
        tick.style.top = `${rel.y - 2}px`;
        layer.appendChild(tick);
      };
      if (mode === "tick") { addTick(); return; }

      if (mode === "top" || mode === "bottom") {
        zone = mode === "top" ? "t" : "b";
        handWrite(note, text, rand, false);
        layer.appendChild(note);
        const w = note.offsetWidth, h = note.offsetHeight;
        let x = Math.max(m * 0.5, Math.min(W - m * 0.5 - w, rel.x + rel.w / 2 - w / 2 + (rand() - 0.5) * 60));
        while (!free(zone, x, x + w) && x + w < W - 12) x += 14;
        if (!free(zone, x, x + w)) { note.remove(); addTick(); return; }
        const y = mode === "top" ? 6 + rand() * 10 : H - h - 8 - rand() * 8;
        note.style.left = `${x}px`;
        note.style.top = `${y}px`;
        taken[zone].push([x, x + w]);
        sx = x + w / 2;
        sy = mode === "top" ? y + h : y;
      } else {
        const vertical = mode === "vertical";
        if (vertical) {
          note.style.maxHeight = "210px";
          note.style.writingMode = "vertical-rl";
        } else {
          note.style.width = `${Math.min(m - 28, 132)}px`;
        }
        handWrite(note, text, rand, vertical);
        layer.appendChild(note);
        const w = note.offsetWidth, h = note.offsetHeight;
        const x = left
          ? (vertical ? Math.max(8, m - 26 - w - rand() * 18) : 12 + rand() * 8)
          : (vertical ? W - m + 20 + rand() * 18 : W - m + 16 + rand() * 8);
        let y = Math.max(padY, rel.y - 6 + (rand() - 0.5) * 12);
        let guard = 0;
        while (!free(zone, y, y + h) && guard++ < 40) y += 8;
        if (y + h > H - padY + 18) {                       // 下面放不下就往上找
          y = Math.min(y, H - padY - h);
          guard = 0;
          while (!free(zone, y, y + h) && guard++ < 40) y -= 8;
        }
        if (y < padY - 10 || !free(zone, y, y + h)) { note.remove(); addTick(); return; }
        note.style.left = `${x}px`;
        note.style.top = `${y}px`;
        taken[zone].push([y, y + h]);
        sx = left ? x + w + 2 : x - 2;
        sy = y + Math.min(h, 16) * 0.6;
      }

      // 手绘的小曲线：从批注指向被划线的原文，带一点抖动
      const mx = (sx + ex) / 2 + (rand() - 0.5) * 26;
      const my = (sy + ey) / 2 + (rand() - 0.5) * 26;
      const path = document.createElementNS(svgNS, "path");
      path.setAttribute("d", `M ${sx.toFixed(1)} ${sy.toFixed(1)} Q ${mx.toFixed(1)} ${my.toFixed(1)} ${ex.toFixed(1)} ${ey.toFixed(1)}`);
      path.setAttribute("class", "notes-line");
      path.style.animationDelay = `${i * 90 + 200}ms`;
      svg.appendChild(path);
    });
  }

  function showAnn(a, ev) {
    S.currentAnn = a;
    $("#annSnippet").textContent = `「${a.snippet}」`;
    $("#annNote").textContent = a.note;
    const pop = $("#annPop");
    pop.hidden = false;
    const x = Math.min(window.innerWidth - pop.offsetWidth - 12, Math.max(12, (ev.clientX || 200) - 20));
    const y = Math.min(window.innerHeight - pop.offsetHeight - 12, (ev.clientY || 200) + 14);
    pop.style.left = `${x}px`;
    pop.style.top = `${y}px`;
  }

  /** 把引用交给聊天页（同一个网页里，不跳转）：关掉阅读器，引用条就出现在输入框上面。 */
  function chatAbout(quote) {
    closeReader();
    document.dispatchEvent(new CustomEvent("phal:quote", { detail: quote }));
  }

  /* ───────── 选中文字 → 发给 Phal ───────── */
  let selTimer = null;
  function onSelection() {
    clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      const sel = getSelection();
      const btn = $("#shareBtn");
      if (!sel || sel.isCollapsed || !sel.rangeCount || !S.data) { btn.hidden = true; return; }
      const range = sel.getRangeAt(0);
      const flow = $("#flow");
      if (!flow.contains(range.startContainer) || !flow.contains(range.endContainer)) { btn.hidden = true; return; }
      const text = sel.toString().trim();
      if (text.length < 2) { btn.hidden = true; return; }
      const rect = range.getBoundingClientRect();
      btn.hidden = false;
      btn.style.left = `${Math.max(8, Math.min(window.innerWidth - 110, rect.right - 40))}px`;
      btn.style.top = `${Math.min(window.innerHeight - 50, rect.bottom + 8)}px`;
      S.pendingShare = { text, off: offsetOf(range.startContainer, range.startOffset) };
    }, 120);
  }

  function shareSelection() {
    const sh = S.pendingShare;
    if (!sh) return;
    let text = sh.text.replace(/\s*\n\s*/g, "\n");
    if (text.length > 600) text = `${text.slice(0, 600)}…`;
    chatAbout({
      role: "user",
      text,
      meta: { kind: "selection", book_id: S.book.id, title: S.book.title, chapter: S.chapter, offset: sh.off == null ? 0 : sh.off },
    });
  }

  /* ───────── 图片版（扫描 pdf）：一页一张图，左右翻 ───────── */
  async function showImagePage(n) {
    const total = S.toc.length;
    n = Math.max(0, Math.min(total - 1, n));
    S.chapter = n;
    S.data = { n, total, title: `第 ${n + 1} 页`, text: "", annotations: [] };
    S.spread = 0; S.spreads = 1;
    const img = $("#pageImg");
    img.hidden = false;
    $("#flow").hidden = true;
    $("#notesLayer").innerHTML = "";
    await new Promise((resolve) => {
      img.onload = resolve;
      img.onerror = resolve;
      img.src = `/api/books/${S.book.id}/pages/${n}`;
    });
    layoutImage();
    afterImageMove();
    // 预加载前后各一页，翻起来不卡
    [n + 1, n - 1].forEach((k) => { if (k >= 0 && k < total) { const p = new Image(); p.src = `/api/books/${S.book.id}/pages/${k}`; } });
  }

  function layoutImage() {
    const img = $("#pageImg");
    const vw = window.innerWidth, vh = window.innerHeight;
    const availW = Math.max(240, vw - (vw >= 820 ? 120 : 24)), availH = Math.max(300, vh - 62 - 70);
    const nw = img.naturalWidth || 3, nh = img.naturalHeight || 4;
    const k = Math.min(availW / nw, availH / nh);
    const W = Math.round(nw * k), H = Math.round(nh * k);
    S.cols = 1;
    const root = ROOT.style;
    root.setProperty("--W", `${W}px`);
    root.setProperty("--H", `${H}px`);
    $("#spread").classList.add("single", "img");
  }

  function afterImageMove() {
    $("#pageInfo").textContent = `第 ${S.chapter + 1} / ${S.data.total} 页`;
    const phal = $("#phalInfo");
    phal.textContent = "图片版：Phal 看不了字，不会读它";
    phal.className = "";
    $("#btnMarkHere").textContent = S.bookmarks.some((b) => b.chapter === S.chapter) ? "★" : "☆";
    renderInk();
    clearTimeout(S.saveTimer);
    const ch = S.chapter;
    S.saveTimer = setTimeout(() => {
      api(`/api/books/${S.book.id}/progress`, { method: "PUT", body: JSON.stringify({ chapter: ch, offset: 0 }) }).catch(() => {});
      S.book.my_chapter = ch;
      S.book.my_offset = 0;
    }, 700);
    S.lastOffset = 0;
  }

  /* ───────── 画笔：在书页上随手画 ───────── */
  const inkKey = () => (S.img ? `p${S.chapter}` : `t${S.chapter}-${S.spread}-${S.fs}-${S.cols}`);

  function strokePath(st, W, H) {
    const pts = st.p;
    if (!pts.length) return "";
    if (pts.length === 1) return `M ${pts[0][0] * W} ${pts[0][1] * H} l 0.01 0`;
    let d = `M ${pts[0][0] * W} ${pts[0][1] * H}`;
    for (let i = 1; i < pts.length - 1; i++) {
      const mx = ((pts[i][0] + pts[i + 1][0]) / 2) * W, my = ((pts[i][1] + pts[i + 1][1]) / 2) * H;
      d += ` Q ${pts[i][0] * W} ${pts[i][1] * H} ${mx} ${my}`;
    }
    const last = pts[pts.length - 1];
    return `${d} L ${last[0] * W} ${last[1] * H}`;
  }

  function drawStroke(svg, st, W, H) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", strokePath(st, W, H));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", st.c);
    path.setAttribute("stroke-width", st.w);
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    if (st.h) path.setAttribute("stroke-opacity", "0.38");
    svg.appendChild(path);
    return path;
  }

  function renderInk() {
    const svg = $("#inkLayer");
    if (!svg) return;
    svg.innerHTML = "";
    if (!S.data) return;
    const r = $("#spread").getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${r.width} ${r.height}`);
    (S.ink[inkKey()] || []).forEach((st) => drawStroke(svg, st, r.width, r.height));
  }

  let inkSaveTimer = null;
  function saveInk(key) {
    clearTimeout(inkSaveTimer);
    const book = S.book.id;
    inkSaveTimer = setTimeout(() => {
      api(`/api/books/${book}/ink`, { method: "PUT", body: JSON.stringify({ key, strokes: S.ink[key] || [] }) })
        .catch((err) => toast(`笔迹没存上：${err.message}`));
    }, 500);
  }

  function setPen(on) {
    S.pen.on = on;
    ROOT.classList.toggle("pen-on", on);
    $("#btnPen").classList.toggle("is-on", on);
    $("#penBar").hidden = !on;
    if (on) { getSelection().removeAllRanges(); $("#shareBtn").hidden = true; }
  }

  function bindPen() {
    $("#btnPen").addEventListener("click", () => { if (S.data) setPen(!S.pen.on); });
    $$all("#penBar .pen__c").forEach((b) => b.addEventListener("click", () => {
      S.pen.color = b.dataset.c;
      S.pen.hl = b.dataset.hl === "1";
      $$all("#penBar .pen__c").forEach((x) => x.classList.toggle("is-on", x === b));
    }));
    $$all("#penBar .pen__w").forEach((b) => b.addEventListener("click", () => {
      S.pen.width = Number(b.dataset.w);
      $$all("#penBar .pen__w").forEach((x) => x.classList.toggle("is-on", x === b));
    }));
    $("#penUndo").addEventListener("click", () => {
      const k = inkKey();
      if (!(S.ink[k] || []).length) return;
      S.ink[k].pop();
      if (!S.ink[k].length) delete S.ink[k];
      renderInk();
      saveInk(k);
    });
    $("#penClear").addEventListener("click", () => {
      const k = inkKey();
      if (!(S.ink[k] || []).length || !confirm("清掉这一页上的所有笔迹？")) return;
      delete S.ink[k];
      renderInk();
      saveInk(k);
    });

    const svg = $("#inkLayer");
    let cur = null, path = null, key = null;
    const pos = (e) => {
      const r = svg.getBoundingClientRect();
      return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
    };
    svg.addEventListener("pointerdown", (e) => {
      if (!S.pen.on || (e.pointerType === "mouse" && e.button !== 0)) return;
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      key = inkKey();
      cur = { c: S.pen.color, w: S.pen.hl ? S.pen.width * 4 : S.pen.width, p: [pos(e)] };
      if (S.pen.hl) cur.h = 1;
      const r = svg.getBoundingClientRect();
      path = drawStroke(svg, cur, r.width, r.height);
    });
    svg.addEventListener("pointermove", (e) => {
      if (!cur) return;
      const p = pos(e), last = cur.p[cur.p.length - 1];
      if (Math.abs(p[0] - last[0]) + Math.abs(p[1] - last[1]) < 0.0015) return;
      cur.p.push(p);
      const r = svg.getBoundingClientRect();
      path.setAttribute("d", strokePath(cur, r.width, r.height));
    });
    const end = () => {
      if (!cur) return;
      cur.p = cur.p.map(([x, y]) => [Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000]);
      (S.ink[key] = S.ink[key] || []).push(cur);
      saveInk(key);
      cur = null; path = null;
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
  }

  /* ───────── 书签 / 目录 抽屉 ───────── */
  function openDrawer(title, build) {
    $("#rdDrawerTitle").textContent = title;
    const body = $("#rdDrawerBody");
    body.innerHTML = "";
    build(body);
    $("#rdDrawer").hidden = false;
    $("#rdScrim").hidden = false;
  }
  function closeDrawer() { $("#rdDrawer").hidden = true; $("#rdScrim").hidden = true; }

  function showToc() {
    openDrawer("目录", (body) => {
      S.toc.forEach((t) => {
        const b = el("button", "ritem" + (t.n === S.chapter ? " is-here" : ""), t.title);
        b.type = "button";
        if (S.book.phal_status !== "unread" && S.book.phal_chapter === t.n) b.appendChild(el("small", null, "Phal 读到这"));
        b.addEventListener("click", () => goTo(t.n, 0));
        body.appendChild(b);
      });
    });
    const here = $("#drawerBody .is-here");
    if (here) here.scrollIntoView({ block: "center" });
  }

  function showMarks() {
    openDrawer("书签", (body) => {
      if (!S.bookmarks.length) { body.appendChild(el("div", "rempty", "还没有书签。点上面的 ☆ 给这一页加一个。")); return; }
      S.bookmarks.forEach((m) => {
        const row = el("button", "ritem");
        row.type = "button";
        const ct = (S.toc[m.chapter] || {}).title || `第 ${m.chapter + 1} 章`;
        row.append(el("span", null, `${ct}　${m.snippet || ""}`));
        const x = el("span", "x", "×");
        x.title = "删除这个书签";
        x.addEventListener("click", async (e) => {
          e.stopPropagation();
          await api(`/api/bookmarks/${m.id}`, { method: "DELETE" }).catch(() => {});
          S.bookmarks = S.bookmarks.filter((b) => b.id !== m.id);
          showMarks();
          if (S.img) afterImageMove(); else afterMove();
        });
        row.appendChild(x);
        row.addEventListener("click", () => goTo(m.chapter, m.offset));
        body.appendChild(row);
      });
    });
  }

  async function toggleBookmark() {
    if (!S.data) return;
    const here = S.bookmarks.filter((b) => b.chapter === S.chapter && (S.img || spreadOfOffset(b.offset) === S.spread));
    try {
      if (here.length) {
        for (const b of here) await api(`/api/bookmarks/${b.id}`, { method: "DELETE" });
        S.bookmarks = S.bookmarks.filter((b) => !here.includes(b));
        toast("书签取消了");
      } else {
        const off = S.img ? 0 : currentOffset();
        const snippet = S.img ? `第 ${S.chapter + 1} 页` : S.data.text.substr(off, 24).replace(/\n/g, " ");
        const r = await api(`/api/books/${S.book.id}/bookmarks`, { method: "POST", body: JSON.stringify({ chapter: S.chapter, offset: off, snippet }) });
        S.bookmarks.push(r.bookmark);
        toast("已加书签");
      }
    } catch (err) { toast(`书签失败：${err.message}`); }
    if (S.img) afterImageMove(); else afterMove();
  }

  /* ───────── 搜索（默认搜全书，也可以只搜本章）；命中处在书页上高亮，可以一处一处往下看 ───────── */
  const HL_OK = !!(window.CSS && CSS.highlights && typeof Highlight !== "undefined");

  /** 章节文本里 [off, off+len) 对应的 DOM 范围。段落的 data-o 是这一行在章节里的偏移，命中不会跨段。 */
  function rangeFor(off, len) {
    const ps = $("#flow").querySelectorAll("p[data-o]");
    if (!ps.length) return null;
    let lo = 0, hi = ps.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (Number(ps[mid].dataset.o) <= off) lo = mid; else hi = mid - 1;
    }
    const p = ps[lo], start = off - Number(p.dataset.o), end = start + len;
    const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    const r = document.createRange();
    let pos = 0, begun = false, last = null;
    while (w.nextNode()) {
      const n = w.currentNode, L = n.length;
      last = n;
      if (!begun && start < pos + L) { r.setStart(n, start - pos); begun = true; }
      if (begun && end <= pos + L) { r.setEnd(n, end - pos); return r; }
      pos += L;
    }
    if (!begun) return null;
    r.setEnd(last, last.length);
    return r;
  }

  function applyHighlights() {
    if (!HL_OK) return;
    CSS.highlights.delete("rd-hit");
    CSS.highlights.delete("rd-cur");
    const s = S.search;
    if (!s || !S.data || S.img) return;
    const all = [], cur = [];
    s.hits.forEach((h, i) => {
      if (h.chapter !== S.chapter) return;
      const r = rangeFor(h.offset, h.length);
      if (r) (i === s.cur ? cur : all).push(r);
    });
    if (all.length) CSS.highlights.set("rd-hit", new Highlight(...all));
    if (cur.length) CSS.highlights.set("rd-cur", new Highlight(...cur));
  }

  function updateSearchInfo() {
    const box = $("#searchInfo"), s = S.search;
    box.innerHTML = "";
    box.hidden = !s || !s.hits.length;
    if (box.hidden) return;
    const at = s.cur >= 0 ? `第 ${s.cur + 1} / ${s.hits.length}${s.truncated ? "+" : ""} 处` : `共 ${s.total} 处`;
    box.appendChild(el("span", "searchinfo__t", `搜「${s.q.length > 10 ? `${s.q.slice(0, 10)}…` : s.q}」${at}`));
    [["‹", "上一处", -1], ["›", "下一处", 1]].forEach(([label, title, d]) => {
      const b = el("button", "searchinfo__b", label);
      b.type = "button";
      b.title = title;
      b.addEventListener("click", () => stepHit(d));
      box.appendChild(b);
    });
    const x = el("button", "searchinfo__b", "×");
    x.type = "button";
    x.title = "清除搜索";
    x.addEventListener("click", clearSearch);
    box.appendChild(x);
  }

  function clearSearch() {
    S.search = null;
    applyHighlights();
    updateSearchInfo();
    const list = $("#rdSearchList");
    if (list) list.innerHTML = "";
  }

  async function gotoHit(i) {
    const s = S.search;
    if (!s || !s.hits[i]) return;
    s.cur = i;
    const h = s.hits[i];
    await goTo(h.chapter, h.offset);          // 会关掉抽屉；跳到命中所在的那一页
    applyHighlights();
    updateSearchInfo();
  }
  function stepHit(d) {
    const s = S.search;
    if (!s || !s.hits.length) return;
    const n = s.hits.length;
    gotoHit(s.cur < 0 ? (d > 0 ? 0 : n - 1) : (s.cur + d + n) % n);
  }

  function renderSearchResults() {
    const list = $("#rdSearchList");
    if (!list) return;
    list.innerHTML = "";
    const s = S.search;
    if (!s) return;
    if (!s.hits.length) { list.appendChild(el("div", "rempty", `${s.scope === "chapter" ? "这一章" : "整本书"}里没有找到「${s.q}」。`)); return; }
    list.appendChild(el("div", "rsearch__sum", `${s.scope === "chapter" ? "本章" : "全书"}共 ${s.total} 处${s.scope === "book" ? `，在 ${s.chapters} 章里` : ""}${s.truncated ? `（只列出前 ${s.hits.length} 处）` : ""}`));
    let lastCh = -1;
    s.hits.forEach((h, i) => {
      if (h.chapter !== lastCh) {
        lastCh = h.chapter;
        list.appendChild(el("div", "rsearch__ch", h.chapter_title || `第 ${h.chapter + 1} 章`));
      }
      const row = el("button", "ritem rhit" + (i === s.cur ? " is-here" : ""));
      row.type = "button";
      const t = el("span", "rhit__t");
      t.append(document.createTextNode(h.pre), el("mark", null, h.hit), document.createTextNode(h.post));
      row.appendChild(t);
      row.addEventListener("click", () => gotoHit(i));
      list.appendChild(row);
    });
  }

  async function runSearch(q, scope) {
    q = q.trim();
    if (!q) { clearSearch(); return; }
    const list = $("#rdSearchList");
    if (list) { list.innerHTML = ""; list.appendChild(el("div", "rempty", "搜索中…")); }
    try {
      const r = await api(`/api/books/${S.book.id}/search?q=${encodeURIComponent(q)}${scope === "chapter" ? `&chapter=${S.chapter}` : ""}`);
      S.search = { q: r.query, scope, hits: r.hits, total: r.total, chapters: r.chapters, truncated: r.truncated, cur: -1 };
    } catch (err) {
      if (list) { list.innerHTML = ""; list.appendChild(el("div", "rempty", err.message)); }
      return;
    }
    renderSearchResults();
    updateSearchInfo();
    applyHighlights();
  }

  function showSearch() {
    if (!S.book) return;
    if (S.img) { toast("这是图片版（扫描 pdf），没有文字，搜不了"); return; }
    let scope = (S.search && S.search.scope) || S.searchScope || "book";
    let input = null, timer = null;
    openDrawer("搜索", (body) => {
      const bar = el("div", "rsearch__bar");
      input = el("input", "rsearch__in");
      input.type = "search";
      input.placeholder = "搜这本书里的文字";
      input.value = (S.search && S.search.q) || "";
      const go = el("button", "rsearch__go", "搜索");
      go.type = "button";
      bar.append(input, go);
      const seg = el("div", "rsearch__seg");
      const mk = (key, label) => {
        const b = el("button", "rsearch__opt" + (scope === key ? " is-on" : ""), label);
        b.type = "button";
        b.addEventListener("click", () => {
          scope = key;
          S.searchScope = key;
          seg.querySelectorAll(".rsearch__opt").forEach((n) => n.classList.toggle("is-on", n === b));
          if (input.value.trim()) runSearch(input.value, scope);
        });
        return b;
      };
      seg.append(mk("book", "全书"), mk("chapter", "只搜本章"));
      const list = el("div", "rsearch__list");
      list.id = "rdSearchList";
      body.append(bar, seg, list);
      let composing = false;
      input.addEventListener("compositionstart", () => { composing = true; });
      input.addEventListener("compositionend", () => { composing = false; });
      input.addEventListener("input", () => { clearTimeout(timer); if (!composing) timer = setTimeout(() => runSearch(input.value, scope), 450); });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !composing) { e.preventDefault(); clearTimeout(timer); runSearch(input.value, scope); }
        else if (e.key === "Escape") { e.stopPropagation(); closeDrawer(); }
      });
      go.addEventListener("click", () => { clearTimeout(timer); runSearch(input.value, scope); });
      renderSearchResults();
    });
    setTimeout(() => { if (input) { input.focus(); input.select(); } }, 30);
  }

  /* ───────── 事件 ───────── */
  function bind() {
    $("#fileInput").addEventListener("change", (e) => { if (e.target.files.length) uploadFiles([...e.target.files]); e.target.value = ""; });
    $("#btnShelf").addEventListener("click", showShelf);
    $("#btnToc").addEventListener("click", showToc);
    $("#btnMarks").addEventListener("click", showMarks);
    $("#btnSearch").addEventListener("click", showSearch);
    $("#btnMarkHere").addEventListener("click", toggleBookmark);
    $("#rdDrawerClose").addEventListener("click", closeDrawer);
    $("#rdScrim").addEventListener("click", closeDrawer);
    const font = (d) => { S.fs = Math.max(13, Math.min(30, S.fs + d)); LS.set("reader.fs", S.fs); applyPrefs(); S.lastOffset = currentOffset(); buildFlow(); relayout(S.lastOffset); };
    $("#btnFontDown").addEventListener("click", () => font(-1));
    $("#btnFontUp").addEventListener("click", () => font(1));
    $("#btnNotes").addEventListener("click", () => { S.notes = !S.notes; LS.set("reader.notes", S.notes ? "1" : "0"); applyPrefs(); renderHandNotes(); });
    $("#btnNight").addEventListener("click", () => { S.night = !S.night; LS.set("reader.night", S.night ? "1" : "0"); applyPrefs(); });
    bindPen();
    $("#tapL").addEventListener("click", prev);
    $("#tapR").addEventListener("click", next);

    document.addEventListener("keydown", (e) => {
      if (!ROOT.hidden && !$("#readView").hidden && (e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "f") { e.preventDefault(); showSearch(); return; }
      if (ROOT.hidden || $("#readView").hidden || e.target.closest("input, textarea")) return;
      if (e.key === "ArrowRight" || e.key === "PageDown" || (e.key === " " && !e.shiftKey)) { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft" || e.key === "PageUp" || (e.key === " " && e.shiftKey)) { e.preventDefault(); prev(); }
      else if (e.key === "Escape") { closeDrawer(); $("#annPop").hidden = true; $("#shareBtn").hidden = true; if (S.pen.on) setPen(false); }
    });

    $("#flow").addEventListener("click", (e) => {
      const m = e.target.closest && e.target.closest("mark.ann");
      if (!m || !S.notes) return;
      const a = S.anns.find((x) => String(x.id) === m.dataset.id);
      if (a && getSelection().isCollapsed) showAnn(a, e);
    });
    document.addEventListener("selectionchange", () => { if (!ROOT.hidden) onSelection(); });
    $("#shareBtn").addEventListener("mousedown", (e) => e.preventDefault());
    $("#shareBtn").addEventListener("click", shareSelection);
    $("#annClose").addEventListener("click", () => { $("#annPop").hidden = true; });
    $("#annChat").addEventListener("click", () => {
      const a = S.currentAnn;
      if (!a) return;
      chatAbout({
        role: "assistant",
        text: `「${a.snippet}」\n她在页边写的：${a.note}`,
        meta: { kind: "annotation", book_id: S.book.id, title: S.book.title, chapter: S.chapter, offset: a.offset, annotation_id: a.id },
      });
    });
    document.addEventListener("mousedown", (e) => {
      if (!e.target.closest("#annPop, mark.ann, .hnote")) $("#annPop").hidden = true;
    });

    let rt = null;
    window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (S.data) relayout(S.lastOffset); }, 150); });
  }

  /* ───────── 打开 / 关闭（嵌在聊天页里） ───────── */
  function openReader(params) {
    params = params || {};
    ROOT.hidden = false;
    document.body.style.overflow = "hidden";
    const bookId = Number(params.book);
    if (bookId) {
      const at = params.ch != null && params.ch !== ""
        ? { chapter: Number(params.ch), offset: Number(params.off || 0) }
        : params.at;
      openBook(bookId, at);
    } else if (S.book && !params.shelf) {
      // 上次读到一半退出的，回来接着读
      $("#shelfView").hidden = true;
      $("#readView").hidden = false;
      $("#rTools").hidden = false;
      history.replaceState(null, "", `/reader?book=${S.book.id}`);
      if (S.data) relayout(S.lastOffset);
    } else {
      showShelf();
    }
  }

  function closeReader() {
    if (ROOT.hidden) return;
    setPen(false);
    closeDrawer();
    $("#annPop").hidden = true;
    $("#shareBtn").hidden = true;
    ROOT.hidden = true;
    document.body.style.overflow = "";
    history.replaceState(null, "", "/");
  }

  $("#rdBack").addEventListener("click", closeReader);
  document.addEventListener("phal:open-reader", (e) => openReader(e.detail));
  window.PhalReader = { open: openReader, close: closeReader };

  bind();
  applyPrefs();
  if (location.pathname.replace(/\/$/, "") === "/reader") {
    const q = new URLSearchParams(location.search);
    openReader({ book: q.get("book"), ch: q.get("ch"), off: q.get("off"), at: q.get("at") });
  }
})();
