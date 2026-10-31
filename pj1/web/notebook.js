/* 她的小本子：她自己认知中的世界（去过的地方、常去的店、吃过的东西、价格印象、听来的事、你告诉她的）。
 * 数据来自 /api/notebook（app/notebook.py）。你可以新建、改、删、锁定；你改过的条目自动锁定，改过的片段换颜色显示。
 * 她不会觉得你看她的小本子有什么奇怪的（和账本一样）。顶上的图标和房间书桌上的笔记本都能打开这里。
 */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const ROOT = $("#notebookRoot");
  if (!ROOT) return;

  const S = { data: null, chapter: "", q: "", loading: false, pendingOnly: false };
  let pollTimer = null;

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
  const btn = (cls, text, title, on) => {
    const b = el("button", cls, text);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", on);
    return b;
  };
  const LOCK_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.8a4 4 0 0 1 8 0v2.7"/></svg>';
  const dayText = (t) => {
    const d = new Date(t * 1000), now = new Date();
    return `${d.getFullYear() !== now.getFullYear() ? `${d.getFullYear()} 年 ` : ""}${d.getMonth() + 1} 月 ${d.getDate()} 日`;
  };
  const timeText = (t) => { const d = new Date(t * 1000); return `${dayText(t)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
  const chapterName = (k) => ((S.data && S.data.chapters.find((c) => c.key === k)) || {}).name || "（已删的章节）";
  const money = (v) => `${Number(v).toFixed(1).replace(/\.0$/, "")} ${(S.data && S.data.unit) || "块"}`;

  let stateTimer = null;
  function setState(text, bad) {
    const n = $("#nbState");
    n.textContent = text;
    n.classList.toggle("is-bad", !!bad);
    clearTimeout(stateTimer);
    if (text && !bad && !text.endsWith("…")) stateTimer = setTimeout(() => { n.textContent = ""; }, 2400);
  }

  async function load() {
    S.loading = true;
    try { S.data = await api("/api/notebook"); render(); }
    catch (e) { setState(e.message, true); }
    finally { S.loading = false; }
  }
  function take(r, msg) { S.data = r; render(); if (msg) setState(msg); }
  async function post(path, body, msg) {
    try { take(await api(path, { method: "POST", body: JSON.stringify(body || {}) }), msg); }
    catch (x) { setState(x.message, true); }
  }

  /* ───────── 顶上的提示条：初始抄写 / 待审 / 每周整理标出来的 ───────── */
  function banners() {
    const d = S.data, c = d.copy, box = el("div", "nb-banners");
    clearTimeout(pollTimer);
    const bar = (text, ...btns) => { const b = el("div", "nb-banner"); b.appendChild(el("span", null, text)); btns.forEach((x) => b.appendChild(x)); box.appendChild(b); return b; };
    if (c.state === "copying") {
      bar(`正在从世界设定初始抄写…（${c.progress || "0/" + c.sources}）抄好的条目先「待审」，你看过才启用。`);
      pollTimer = setTimeout(() => { if (!ROOT.hidden) load(); }, 3000);
    } else if (c.state === "pending" || c.pending) {
      bar(`初始抄写了 ${c.pending} 条，等你审：她用自己的话抄的，看看有没有抄进她不该知道的、口吻对不对。审完（全部通过 / 逐条通过或删掉）以后，聊天才改成检索小本子。`,
        btn("btn btn--mini", "全部通过", null, () => { if (confirm(`${c.pending} 条全部通过？`)) post("/api/notebook/approve", {}, "都通过了，聊天现在检索小本子"); }),
        btn("linkish", S.pendingOnly ? "看全部" : "只看待审的", null, () => { S.pendingOnly = !S.pendingOnly; render(); }));
    } else if (!d.chat_ready) {
      bar(c.sources
        ? `还没从世界设定初始抄写：让她用自己的话把 ${c.sources} 份资料抄进本子（每份一次模型调用，「## 她不知道」的部分不抄）。抄完你审过，聊天才改用小本子（在那之前聊天照旧直接读世界设定）。`
        : "世界设定里还没有可以抄的文件。聊天现在照旧直接读世界设定；不需要抄写的话可以直接切到小本子。",
        ...(c.sources ? [btn("btn btn--mini", "开始抄写", null, () => post("/api/notebook/copy", {}, "开始抄了"))] : []),
        btn("linkish", "不抄，直接用小本子", "聊天改成只检索小本子，世界设定的条目不再直接进上下文（总纲照旧常驻）", () => {
          if (confirm("不抄写，直接让聊天只检索小本子？")) post("/api/notebook/copy/skip", {}, "聊天现在检索小本子");
        }));
    }
    if (c.error) bar(`抄写的时候：${c.error}`);
    (d.flags || []).forEach((f) => {
      bar(`每周整理时标出来的：「${f.title}」——${f.reason}`,
        btn("linkish", "删掉", null, () => { if (confirm(`删掉「${f.title}」？`)) post(`/api/notebook/flag/${f.id}`, { delete: true }, "删掉了"); }),
        btn("linkish", "留着", null, () => post(`/api/notebook/flag/${f.id}`, { delete: false }, "留着")));
    });
    return box;
  }

  /* ───────── 正文：你改过的片段换颜色 ───────── */
  function bodyNode(e) {
    const box = el("div", "nb-entry__body");
    const t = e.body || "";
    let at = 0;
    (e.user_spans || []).forEach(([s, f]) => {
      s = Math.max(at, Math.min(t.length, s)); f = Math.max(s, Math.min(t.length, f));
      if (s > at) box.appendChild(document.createTextNode(t.slice(at, s)));
      if (f > s) { const m = el("span", "nb-mine", t.slice(s, f)); m.title = "你改过的"; box.appendChild(m); }
      at = f;
    });
    if (at < t.length) box.appendChild(document.createTextNode(t.slice(at)));
    return box;
  }

  function matches(e, q) {
    if (!q) return true;
    const items = Object.keys((e.info && e.info.items) || {}).join(" ");
    const hay = [e.title, e.body, (e.keywords || []).join(" "), (e.addenda || []).map((a) => a.text).join(" "), items, e.place_label].join("\n").toLowerCase();
    return q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
  }

  function entryNode(e) {
    const art = el("article", "nb-entry" + (e.locked ? " is-locked" : "") + (e.status === "pending" ? " is-pending" : ""));
    const head = el("header", "nb-entry__head");
    head.appendChild(el("h3", "nb-entry__title", e.title));
    if (!S.chapter) head.appendChild(el("span", "nb-tag nb-tag--ch", chapterName(e.chapter)));
    head.appendChild(el("span", "nb-tag", S.data.sources[e.source] || e.source));
    head.appendChild(el("span", `nb-tag nb-tag--${e.trust}`, S.data.trusts[e.trust] || e.trust));
    if (e.status === "pending") head.appendChild(el("span", "nb-tag nb-tag--pending", "待审"));
    if (e.locked) { const l = el("span", "nb-lock"); l.innerHTML = LOCK_SVG; l.title = "锁定了：她不能改原文，只能在末尾追加"; head.appendChild(l); }
    head.appendChild(el("span", "nb-entry__time", dayText(e.updated_at)));
    const ops = el("span", "nb-entry__ops");
    if (e.status === "pending") ops.appendChild(btn("nb-op nb-op--ok", "通过", "这一条审过了，启用", () => post("/api/notebook/approve", { ids: [e.id] }, "通过了")));
    ops.append(
      btn("nb-op", "改", "编辑这一条", () => openEdit(e)),
      btn("nb-op", e.locked ? "解锁" : "锁定", e.locked ? "解锁：她又可以改这一条了" : "锁定：她不能改原文，只能在末尾追加", () => toggleLock(e)),
      btn("nb-op", "历史", "修改历史，可以找回", () => openHistory(e)),
      btn("nb-op nb-op--danger", "删", "删掉这一条（历史里还留着快照）", () => remove(e)),
    );
    head.appendChild(ops);
    art.appendChild(head);

    const meta = [];
    if (e.place_label) meta.push(`在${e.place_label}`);
    if ((e.keywords || []).length) meta.push(`关键词：${e.keywords.join("、")}`);
    if (meta.length) art.appendChild(el("div", "nb-entry__meta", meta.join(" · ")));
    const items = Object.entries((e.info && e.info.items) || {});
    if (items.length) {
      const box = el("div", "nb-items");
      items.sort((a, b) => (b[1].last_at || 0) - (a[1].last_at || 0)).forEach(([name, it]) => {
        const c = el("span", "nb-item");
        c.append(el("b", null, name), document.createTextNode(` ${money(it.price)}`));
        if ((it.times || 1) > 1) c.appendChild(el("small", null, ` ×${it.times}`));
        c.title = `第一次 ${timeText(it.first_at || e.created_at)}，最近 ${timeText(it.last_at || e.updated_at)}。再买价格会在这个数的 ±10% 里`;
        box.appendChild(c);
      });
      art.appendChild(box);
    }
    if (e.body) art.appendChild(bodyNode(e));
    else if (!items.length && !(e.addenda || []).length) art.appendChild(el("div", "nb-entry__empty", "（还没写什么）"));
    else if (!e.body && items.length) art.appendChild(el("div", "nb-entry__empty", "（她还没写印象：这一条是买东西时自动记下的价格）"));
    (e.addenda || []).forEach((a) => {
      const d = el("div", "nb-add" + (a.kind === "opinion" ? " is-opinion" : ""));
      d.appendChild(el("span", "nb-add__mark", `${a.kind === "opinion" ? "她的看法" : "追加"} · ${dayText(a.at)}`));
      d.appendChild(document.createTextNode(a.text));
      art.appendChild(d);
    });
    return art;
  }

  function render() {
    const d = S.data;
    const main = $("#nbMain");
    main.innerHTML = "";
    if (!d) return;
    const book = el("div", "nb-book");
    book.appendChild(banners());
    const tabs = el("nav", "nb-tabs");
    const all = d.entries.filter((e) => matches(e, S.q) && (!S.pendingOnly || e.status === "pending"));
    const tab = (key, name, n) => {
      const b = btn("nb-tab" + (S.chapter === key ? " is-on" : ""), null, null, () => { S.chapter = key; render(); });
      b.append(el("span", null, name), el("small", null, String(n)));
      if (key) b.dataset.ch = String(d.chapters.findIndex((c) => c.key === key) % 7);
      tabs.appendChild(b);
    };
    tab("", "全部", all.length);
    d.chapters.forEach((c) => tab(c.key, c.name, all.filter((e) => e.chapter === c.key).length));
    const orphan = all.filter((e) => !d.chapters.some((c) => c.key === e.chapter));
    book.appendChild(tabs);

    const paper = el("div", "nb-paper");
    const list = S.chapter ? all.filter((e) => e.chapter === S.chapter) : all;
    if (S.chapter) {
      const c = d.chapters.find((x) => x.key === S.chapter);
      if (c) paper.appendChild(el("div", "nb-paper__head", `${c.name} · ${(d.accuracy[c.accuracy] || "").replace(/（.*）/, "")}`));
    }
    if (!list.length) {
      paper.appendChild(el("p", "nb-empty", S.q ? "没找到。" : (S.chapter === "shop"
        ? "这一章还空着。她买东西、出去吃饭时会自动记下店名和价格（同一家店同一样东西，价格前后一致）。"
        : "这一章还空着。之后她会把去过的地方、吃过的东西、听来的事慢慢记进来；你也可以自己加。")));
    }
    list.forEach((e) => paper.appendChild(entryNode(e)));
    if (!S.chapter && orphan.length) paper.appendChild(el("p", "nb-empty", `有 ${orphan.length} 条所在的章节已经删了，改一下它们的章节就能在页签里看到。`));
    book.appendChild(paper);
    main.appendChild(book);
  }

  /* ───────── 锁定 / 删除 ───────── */
  async function toggleLock(e) {
    try { take(await api(`/api/notebook/entry/${e.id}/lock`, { method: "POST", body: JSON.stringify({ locked: !e.locked }) }), e.locked ? "解锁了" : "锁定了"); }
    catch (x) { setState(x.message, true); }
  }
  async function remove(e) {
    if (!confirm(`删掉「${e.title}」？`)) return;
    try { take(await api(`/api/notebook/entry/${e.id}`, { method: "DELETE" }), "删掉了"); }
    catch (x) { setState(x.message, true); }
  }

  /* ───────── 弹窗 ───────── */
  function openPop(title) {
    $("#nbPopTitle").textContent = title;
    const body = $("#nbPopBody");
    body.innerHTML = "";
    $("#nbPop").hidden = false;
    return body;
  }
  function closePop() { $("#nbPop").hidden = true; }

  function select(opts, val) {
    const s = el("select", "input");
    opts.forEach(([v, lb]) => { const o = el("option", null, lb); o.value = v; s.appendChild(o); });
    s.value = val;
    return s;
  }

  function openEdit(e) {
    const d = S.data;
    const isNew = !e;
    e = e || { chapter: S.chapter || d.chapters[0].key, title: "", keywords: [], body: "", addenda: [], info: {}, trust: "confirmed", source: "told", place: "", locked: true };
    const body = openPop(isNew ? "新建一条" : `改「${e.title}」`);
    const form = el("div", "rm-form nb-form");
    const ch = select(d.chapters.map((c) => [c.key, c.name]), e.chapter);
    const title = el("input", "input");
    title.value = e.title; title.maxLength = 40; title.placeholder = "同一个地方 / 同一家店只能有一条";
    const kws = el("input", "input");
    kws.value = (e.keywords || []).join("、"); kws.placeholder = "用顿号或逗号隔开；标题本身自动算一个";
    const place = select([["", "（不填）"], ...d.places.map((p) => [p.id, p.label])], e.place || "");
    const trust = select(Object.entries(d.trusts), e.trust);
    const source = select(Object.entries(d.sources), e.source);
    const text = el("textarea", "input nb-form__text");
    text.value = e.body || ""; text.rows = 7; text.placeholder = "她的口吻";
    const lock = el("input");
    lock.type = "checkbox"; lock.checked = isNew ? true : !!e.locked;
    let lockTouched = false;                          // 没动这个勾：交给后端（你改了内容就自动锁定）
    lock.addEventListener("change", () => { lockTouched = true; });
    const lockLab = el("label", "nb-form__check");
    lockLab.append(lock, document.createTextNode(" 锁定（她不能改原文，只能在末尾追加）。不动这个勾的话，你改了内容就会自动锁定"));
    form.append(el("label", null, "章节"), ch, el("label", null, "标题"), title, el("label", null, "关键词"), kws, el("label", null, "地点"), place,
      el("label", null, "可信度"), trust, el("label", null, "来源"), source, el("label", null, "正文"), text, el("span"), lockLab);
    body.appendChild(form);

    // 店铺：东西和价格（价格的一致性靠这里）
    const items = Object.entries((e.info && e.info.items) || {}).map(([name, it]) => ({ name, ...it }));
    const itemBox = el("div", "nb-form__items");
    const drawItems = () => {
      itemBox.innerHTML = "";
      if (ch.value !== "shop" && !items.length) return;
      itemBox.appendChild(el("div", "rm-hint", "买过的东西和第一次的价格：她再来买同一样，价格会在这个数的 ±10% 里。"));
      items.forEach((it, i) => {
        const row = el("div", "nb-form__item");
        const n = el("input", "input"); n.value = it.name; n.placeholder = "东西";
        n.addEventListener("input", () => { it.name = n.value; });
        const p = el("input", "input"); p.type = "number"; p.min = 0; p.step = 0.1; p.value = it.price ?? "";
        p.addEventListener("input", () => { it.price = Number(p.value); });
        row.append(n, p, el("span", null, (S.data && S.data.unit) || "块"), btn("linkish", "删", null, () => { items.splice(i, 1); drawItems(); }));
        itemBox.appendChild(row);
      });
      itemBox.appendChild(btn("linkish", "＋ 加一样东西", null, () => { items.push({ name: "", price: 0, times: 1 }); drawItems(); }));
    };
    ch.addEventListener("change", drawItems);
    drawItems();
    body.appendChild(itemBox);

    // 追加的段落：只能删
    const adds = (e.addenda || []).slice();
    const addBox = el("div", "nb-form__adds");
    const drawAdds = () => {
      addBox.innerHTML = "";
      if (!adds.length) return;
      addBox.appendChild(el("div", "rm-hint", "她在末尾追加的："));
      adds.forEach((a, i) => {
        const r = el("div", "nb-add" + (a.kind === "opinion" ? " is-opinion" : ""));
        r.append(el("span", "nb-add__mark", `${a.kind === "opinion" ? "她的看法" : "追加"} · ${dayText(a.at)}`), document.createTextNode(a.text), btn("linkish", "删", null, () => { adds.splice(i, 1); drawAdds(); }));
        addBox.appendChild(r);
      });
    };
    drawAdds();
    body.appendChild(addBox);

    const opsRow = el("div", "rm-form__ops");
    const ok = btn("btn btn--mini", isNew ? "建" : "保存", null, async () => {
      const info = Object.assign({}, e.info || {});
      if (items.length || info.items) {
        const now = Date.now() / 1000;
        info.items = {};
        items.filter((it) => it.name.trim()).forEach((it) => {
          info.items[it.name.trim()] = { price: Number(it.price) || 0, band: it.band || "", times: it.times || 1, first_at: it.first_at || now, last_at: it.last_at || now };
        });
      }
      const payload = { chapter: ch.value, title: title.value, keywords: kws.value, place: place.value, trust: trust.value, source: source.value,
        body: text.value, addenda: adds, info };
      if (isNew || lockTouched) payload.locked = lock.checked;
      ok.disabled = true;
      try {
        const r = isNew ? await api("/api/notebook/entry", { method: "POST", body: JSON.stringify(payload) })
          : await api(`/api/notebook/entry/${e.id}`, { method: "PUT", body: JSON.stringify(payload) });
        closePop();
        take(r, isNew ? "建好了" : "保存了");
      } catch (x) { ok.disabled = false; setState(x.message, true); }
    });
    opsRow.append(ok, btn("btn btn--ghost btn--mini", "取消", null, closePop));
    body.appendChild(opsRow);
    setTimeout(() => (isNew ? title : text).focus(), 30);
  }

  async function openHistory(e) {
    const body = openPop(`「${e.title}」的修改历史`);
    body.appendChild(el("p", "rm-hint", "读取中…"));
    let hist;
    try { hist = (await api(`/api/notebook/entry/${e.id}/history`)).history; }
    catch (x) { body.innerHTML = ""; body.appendChild(el("p", "rm-hint", x.message)); return; }
    body.innerHTML = "";
    if (!hist.length) { body.appendChild(el("p", "rm-hint", "还没有改过。")); return; }
    body.appendChild(el("p", "rm-hint", "每一项是改之前的样子。找回之后这一条会锁定（找回本身也会留一份历史，可以再找回去）。"));
    hist.forEach((h) => {
      const s = h.snapshot;
      const r = el("div", "nb-hist");
      const top = el("div", "nb-hist__top");
      top.append(el("b", null, timeText(h.at)), el("span", null, `${h.reason || ""}之前`));
      top.appendChild(btn("linkish", "找回成这样", null, async () => {
        if (!confirm("把这一条改回这个样子？")) return;
        try { take(await api(`/api/notebook/entry/${e.id}/restore`, { method: "POST", body: JSON.stringify({ history_id: h.id }) }), "找回了"); closePop(); }
        catch (x) { setState(x.message, true); }
      }));
      r.appendChild(top);
      if (s.title !== e.title) r.appendChild(el("div", "nb-hist__meta", `标题：${s.title}`));
      r.appendChild(el("div", "nb-hist__body", s.body || "（正文是空的）"));
      (s.addenda || []).forEach((a) => r.appendChild(el("div", "nb-hist__meta", `追加：${a.text}`)));
      body.appendChild(r);
    });
  }

  function open() { ROOT.hidden = false; load(); }
  function close() { closePop(); clearTimeout(pollTimer); ROOT.hidden = true; }

  $("#nbBack").addEventListener("click", close);
  $("#nbNew").addEventListener("click", () => { if (S.data) openEdit(null); });
  $("#nbSearch").addEventListener("input", (ev) => { S.q = ev.target.value.trim(); render(); });
  $("#nbPopClose").addEventListener("click", closePop);
  $("#nbPop").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) closePop(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape" || ROOT.hidden) return;
    ev.stopPropagation();
    if (!$("#nbPop").hidden) closePop(); else close();
  }, true);

  window.PhalNotebook = { open, close, reload: load };
})();
