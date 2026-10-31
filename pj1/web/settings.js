/* 统一设置页：所有设置项和人设集中在这里，按分区折叠展开。
 * 数据来自 /api/config（后端的设置注册表）：每项有名字、一句说明、默认值，改完自动保存，可以恢复默认。
 */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const ROOT = $("#setRoot");
  if (!ROOT) return;

  const S = { groups: [], open: new Set(["persona"]), timers: {}, pending: {}, saving: 0 };
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

  /* ───────── 状态提示 ───────── */
  let stateTimer = null;
  function setState(text, bad) {
    const n = $("#setState");
    n.textContent = text;
    n.classList.toggle("is-bad", !!bad);
    clearTimeout(stateTimer);
    if (text && !bad && text !== "正在保存…") stateTimer = setTimeout(() => { n.textContent = ""; }, 2200);
  }

  /* ───────── 保存 / 恢复默认 ───────── */
  function announce(settings) {
    document.dispatchEvent(new CustomEvent("phal:settings-changed", { detail: settings }));
  }

  async function save(item, value, row) {
    setState("正在保存…");
    try {
      const r = await api("/api/config", { method: "POST", body: JSON.stringify({ values: { [item.key]: value } }) });
      const err = r.errors && r.errors[item.key];
      showError(row, err || "");
      if (err) { setState("有一项没存上", true); return false; }
      if (item.kind !== "secret") {
        item.value = value;
        item.modified = JSON.stringify(value) !== JSON.stringify(item.default);
      } else {
        item.secret_set = value !== null && (value !== "" || item.secret_set);
      }
      refreshRow(item, row);
      setState("已保存");
      announce(r.settings);
      return true;
    } catch (e) {
      setState(`保存失败：${e.message}`, true);
      return false;
    }
  }

  function saveLater(item, value, row, delay = 700) {
    clearTimeout(S.timers[item.key]);
    setState("正在保存…");
    const run = () => { delete S.pending[item.key]; return save(item, value, row); };
    S.pending[item.key] = run;
    S.timers[item.key] = setTimeout(run, delay);
  }

  async function resetKeys(keys, rerender) {
    try {
      const r = await api("/api/config/reset", { method: "POST", body: JSON.stringify({ keys }) });
      announce(r.settings);
      await load();
      setState("已恢复默认");
      if (rerender) render();
    } catch (e) {
      setState(`恢复失败：${e.message}`, true);
    }
  }

  function showError(row, msg) {
    let n = row.querySelector(".sitem__err");
    if (!msg) { if (n) n.remove(); return; }
    if (!n) { n = el("div", "sitem__err"); row.appendChild(n); }
    n.textContent = msg;
  }

  function refreshRow(item, row) {
    const btn = row.querySelector(".sitem__reset");
    if (btn) btn.hidden = !item.modified;
  }

  /* ───────── 控件 ───────── */
  function numberControl(item, row) {
    const wrap = el("div", "sitem__ctl");
    const inp = el("input", "input sitem__num");
    inp.type = "number";
    if (item.min != null) inp.min = item.min;
    if (item.max != null) inp.max = item.max;
    if (item.step != null) inp.step = item.step;
    inp.value = item.value;
    inp.addEventListener("input", () => {
      if (inp.value === "") return;
      saveLater(item, Number(inp.value), row);
    });
    wrap.appendChild(inp);
    if (item.unit) wrap.appendChild(el("span", "sitem__unit", item.unit));
    return { node: wrap, set: (v) => { inp.value = v; } };
  }

  function boolControl(item, row) {
    const lab = el("label", "switch");
    const inp = document.createElement("input");
    inp.type = "checkbox";
    inp.checked = !!item.value;
    inp.addEventListener("change", () => save(item, inp.checked, row));
    lab.append(inp, el("span", "switch__box"));
    return { node: lab, set: (v) => { inp.checked = !!v; } };
  }

  function textControl(item, row) {
    const inp = el("input", "input");
    inp.type = "text";
    inp.value = item.value;
    inp.spellcheck = false;
    inp.addEventListener("input", () => saveLater(item, inp.value, row));
    return { node: inp, set: (v) => { inp.value = v; } };
  }

  function longTextControl(item, row) {
    const wrap = el("div", "sitem__long");
    const ta = el("textarea", "textarea");
    ta.value = item.value || "";
    ta.spellcheck = false;
    const count = el("div", "sitem__count", `${ta.value.length} 字`);
    const fit = () => { ta.style.height = "auto"; ta.style.height = `${Math.min(520, Math.max(120, ta.scrollHeight + 4))}px`; };
    ta.addEventListener("input", () => { count.textContent = `${ta.value.length} 字`; fit(); saveLater(item, ta.value, row, 900); });
    wrap.append(ta, count);
    setTimeout(fit, 0);
    return { node: wrap, set: (v) => { ta.value = v || ""; count.textContent = `${ta.value.length} 字`; fit(); }, onOpen: fit };
  }

  function secretControl(item, row) {
    const wrap = el("div", "sitem__ctl sitem__ctl--col");
    const inp = el("input", "input");
    inp.type = "password";
    inp.autocomplete = "off";
    const state = el("div", "sitem__count");
    const paint = () => {
      inp.placeholder = item.secret_set ? "已保存（留空则不改动）" : "未配置（离线演示模式）";
      state.textContent = item.secret_set ? "已保存" : "未配置";
    };
    paint();
    inp.addEventListener("change", async () => {
      if (!inp.value.trim()) return;
      if (await save(item, inp.value.trim(), row)) { inp.value = ""; paint(); }
    });
    const clear = el("button", "linkish", "清除");
    clear.type = "button";
    clear.addEventListener("click", async () => {
      if (!item.secret_set || !confirm("清除 API Key？清除后进入离线演示模式。")) return;
      if (await save(item, null, row)) paint();
    });
    const line = el("div", "sitem__ctl");
    line.append(state, clear);
    wrap.append(inp, line);
    return { node: wrap, set: () => paint() };
  }

  function listControl(item, row) {
    const wrap = el("div", "sitem__list");
    const chips = el("div", "chips");
    let list = (item.value || []).slice();
    const paint = () => {
      chips.innerHTML = "";
      if (!list.length) chips.appendChild(el("span", "chips__empty", "还没有"));
      list.forEach((w, i) => {
        const c = el("span", "chip");
        c.appendChild(el("span", null, w));
        const x = el("button", null, "×");
        x.type = "button";
        x.title = "移除";
        x.addEventListener("click", () => { list.splice(i, 1); paint(); save(item, list.slice(), row); });
        c.appendChild(x);
        chips.appendChild(c);
      });
    };
    paint();
    const line = el("div", "field--inline");
    const inp = el("input", "input");
    inp.type = "text";
    inp.placeholder = "输入后回车添加，也可以一次粘一串";
    const add = el("button", "btn btn--ghost", "添加");
    add.type = "button";
    const commit = () => {
      const parts = inp.value.split(/[\n\r,，、;；]+/).map((s) => s.trim()).filter(Boolean);
      if (!parts.length) return;
      let added = 0;
      parts.forEach((w) => { if (!list.includes(w)) { list.push(w); added += 1; } });
      inp.value = "";
      paint();
      if (added) save(item, list.slice(), row);
    };
    add.addEventListener("click", commit);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } });
    line.append(inp, add);
    wrap.append(chips, line);
    return { node: wrap, set: (v) => { list = (v || []).slice(); paint(); } };
  }

  function tableControl(item, row) {
    const wrap = el("div", "sitem__table");
    const tbl = el("table", "stbl");
    const head = el("tr");
    head.appendChild(el("th", null, ""));
    item.columns.forEach((c) => head.appendChild(el("th", null, c.label)));
    tbl.appendChild(head);
    let rows = item.value.map((r) => ({ ...r }));
    const inputs = [];
    rows.forEach((r, i) => {
      const tr = el("tr");
      tr.appendChild(el("th", null, (item.row_labels && item.row_labels[i]) || `${i + 1} 月`));
      item.columns.forEach((c) => {
        const td = el("td");
        const inp = el("input", "input stbl__in");
        inp.type = "number";
        inp.min = c.min; inp.max = c.max; inp.step = c.step;
        inp.value = r[c.key];
        inp.addEventListener("input", () => {
          if (inp.value === "") return;
          rows[i][c.key] = Number(inp.value);
          saveLater(item, rows.map((x) => ({ ...x })), row, 900);
        });
        inputs.push([i, c.key, inp]);
        td.appendChild(inp);
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    });
    wrap.appendChild(tbl);
    return { node: wrap, set: (v) => { rows = v.map((r) => ({ ...r })); inputs.forEach(([i, k, inp]) => { inp.value = rows[i][k]; }); } };
  }

  function selectControl(item, row) {
    const sel = el("select", "input");
    (item.choices || []).forEach((c) => { const o = el("option", null, c.label); o.value = c.value; sel.appendChild(o); });
    sel.value = item.value;
    sel.addEventListener("change", () => save(item, sel.value, row));
    return { node: sel, set: (v) => { sel.value = v; } };
  }

  function timeControl(item, row) {
    const wrap = el("div", "sitem__ctl");
    const inp = el("input", "input sitem__num");
    inp.type = "time";
    inp.value = item.value;
    inp.addEventListener("change", () => { if (inp.value) saveLater(item, inp.value, row, 300); });
    wrap.appendChild(inp);
    return { node: wrap, set: (v) => { inp.value = v; } };
  }

  const CONTROLS = { select: selectControl, time: timeControl, table: tableControl, int: numberControl, float: numberControl, bool: boolControl, text: textControl, longtext: longTextControl, secret: secretControl, list: listControl };

  function itemRow(item, compact) {
    const row = el("div", `sitem sitem--${item.kind}${compact ? " sitem--compact" : ""}`);
    row.dataset.key = item.key;
    if (compact && item.hint) row.title = item.hint;
    const head = el("div", "sitem__head");
    head.appendChild(el("span", "sitem__label", item.label));
    const rs = el("button", "linkish sitem__reset", "恢复默认");
    rs.type = "button";
    rs.hidden = !item.modified;
    rs.addEventListener("click", async () => {
      if (!confirm(`把「${item.label}」恢复成默认值？`)) return;
      await resetKeys([item.key], true);
    });
    head.appendChild(rs);
    row.appendChild(head);
    if (item.hint && !compact) row.appendChild(el("div", "sitem__hint", item.hint));
    const ctl = CONTROLS[item.kind](item, row);
    row.appendChild(ctl.node);
    row._ctl = ctl;
    return row;
  }

  /* ───────── 节日（这一块有自己的编辑界面） ───────── */
  const fest = { list: [], editing: null };

  async function loadFest() {
    try { fest.list = (await api("/api/festivals")).festivals; } catch (_) { fest.list = []; }
  }

  function festPanel(box) {
    box.innerHTML = "";
    box.appendChild(el("div", "sitem__hint", "节日当天打开网页，她会主动祝你节日快乐，当天心情也不会低落。每个节日每年只祝福一次。"));
    const list = el("div", "fest2__list");
    if (!fest.list.length) list.appendChild(el("div", "sitem__hint", "还没有节日。"));
    fest.list.forEach((f) => {
      const row = el("div", "fest2__row");
      const main = el("div", "fest2__main");
      main.appendChild(el("span", "fest2__name", f.name));
      main.appendChild(el("span", "fest2__when", `${f.date}${f.yearly ? " · 每年" : ""}`));
      if (f.note) main.appendChild(el("div", "fest2__note", f.note));
      const ops = el("div", "fest2__ops");
      const edit = el("button", "linkish", "编辑");
      edit.type = "button";
      edit.addEventListener("click", () => { fest.editing = f; festPanel(box); });
      const del = el("button", "linkish", "删除");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm(`删掉「${f.name}」？`)) return;
        try { await api(`/api/festivals/${f.id}`, { method: "DELETE" }); await loadFest(); festPanel(box); document.dispatchEvent(new CustomEvent("phal:festivals-changed")); }
        catch (e) { setState(e.message, true); }
      });
      ops.append(edit, del);
      row.append(main, ops);
      list.appendChild(row);
    });
    box.appendChild(list);

    const f = fest.editing;
    const form = el("form", "fest2__form");
    const mk = (type, ph, val) => { const i = el("input", "input"); i.type = type; if (ph) i.placeholder = ph; if (val != null) i.value = val; return i; };
    const name = mk("text", "节日名称，比如：生日", f ? f.name : "");
    name.maxLength = 30;
    const today = new Date().toISOString().slice(0, 10);
    const date = mk("date", "", f ? (f.date.length === 10 ? f.date : `${new Date().getFullYear()}-${f.date}`) : today);
    const yl = el("label", "fest2__yearly");
    const yearly = document.createElement("input");
    yearly.type = "checkbox";
    yearly.checked = f ? f.yearly : true;
    yl.append(yearly, document.createTextNode(" 每年重复"));
    const note = mk("text", "一句备注（可选）", f ? f.note : "");
    note.maxLength = 60;
    const btns = el("div", "fest2__btns");
    if (f) {
      const c = el("button", "btn btn--ghost", "取消");
      c.type = "button";
      c.addEventListener("click", () => { fest.editing = null; festPanel(box); });
      btns.appendChild(c);
    }
    const ok = el("button", "btn", f ? "保存" : "添加");
    ok.type = "submit";
    btns.appendChild(ok);
    form.append(name, date, yl, note, btns);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { name: name.value.trim(), date: date.value, yearly: yearly.checked, note: note.value.trim() };
      if (!body.name || !body.date) { setState("名称和日期都要填", true); return; }
      try {
        if (f) await api(`/api/festivals/${f.id}`, { method: "PATCH", body: JSON.stringify(body) });
        else await api("/api/festivals", { method: "POST", body: JSON.stringify(body) });
        fest.editing = null;
        await loadFest();
        festPanel(box);
        setState("已保存");
        document.dispatchEvent(new CustomEvent("phal:festivals-changed"));
      } catch (err) { setState(err.message, true); }
    });
    box.appendChild(form);
  }

  /* ───────── 天气：当前状态 ───────── */
  async function weatherStatus(box) {
    const line = el("div", "wstat");
    box.appendChild(line);
    const paint = (w) => {
      const fmt = (x) => (x ? `${x.label}，${x.temp}°C（${x.source === "online" ? "联网" : "模拟"}）` : "还没有数据");
      line.innerHTML = "";
      line.appendChild(el("span", null, `现在是${w.season}季　她这边：${fmt(w.her)}　你那边：${fmt(w.user)}`));
      const b = el("button", "linkish", "立即更新");
      b.type = "button";
      b.addEventListener("click", async () => {
        b.textContent = "更新中…";
        try { paint(await api("/api/weather/refresh", { method: "POST" })); } catch (e) { setState(e.message, true); }
      });
      line.appendChild(b);
    };
    try { paint(await api("/api/weather")); } catch (_) { line.textContent = "天气状态暂时取不到"; }
  }

  /* ───────── 人物档案 ───────── */
  const ppl = { list: [], adding: false, busy: false };

  async function loadPeople() {
    try { ppl.list = (await api("/api/people")).people; } catch (_) { ppl.list = []; }
  }

  function personCard(p, rerender) {
    const card = el("div", "pcard" + (p.draft ? " is-draft" : ""));
    const top = el("div", "pcard__top");
    const name = el("input", "input pcard__name");
    name.value = p.name;
    name.maxLength = 20;
    const rel = el("input", "input pcard__rel");
    rel.value = p.relation;
    rel.placeholder = "和她的关系";
    rel.maxLength = 30;
    top.append(name, rel);
    const per = el("textarea", "textarea pcard__ta");
    per.rows = 2;
    per.placeholder = "性格一句话";
    per.value = p.personality;
    const how = el("textarea", "textarea pcard__ta");
    how.rows = 2;
    how.placeholder = "相处方式";
    how.value = p.how;
    card.append(top, el("div", "pcard__lab", "性格"), per, el("div", "pcard__lab", "相处方式"), how);
    if (p.recent && p.recent.length) {
      const rc = el("div", "pcard__recent");
      rc.appendChild(el("div", "pcard__lab", "最近发生过的事"));
      p.recent.slice(-5).forEach((r) => rc.appendChild(el("div", "pcard__ev", `${(r.date || "").slice(5)}　${r.text}`)));
      card.appendChild(rc);
    }
    const save = async () => {
      setState("正在保存…");
      try {
        await api(`/api/people/${p.id}`, { method: "PATCH", body: JSON.stringify({ name: name.value, relation: rel.value, personality: per.value, how: how.value }) });
        setState("已保存");
      } catch (e) { setState(e.message, true); }
    };
    [name, rel, per, how].forEach((n) => n.addEventListener("change", save));
    const ops = el("div", "pcard__ops");
    if (p.draft) {
      const ok = el("button", "btn", "采用");
      ok.type = "button";
      ok.addEventListener("click", async () => {
        await save();
        try { await api(`/api/people/${p.id}/approve`, { method: "POST" }); await loadPeople(); rerender(); setState("已定稿，她以后会提到这个人"); }
        catch (e) { setState(e.message, true); }
      });
      ops.appendChild(ok);
    }
    const del = el("button", "linkish", p.draft ? "丢弃" : "删除");
    del.type = "button";
    del.addEventListener("click", async () => {
      if (!confirm(p.draft ? `丢弃草稿「${p.name}」？` : `删掉「${p.name}」？她以后就不会再提到这个人了。`)) return;
      try { await api(`/api/people/${p.id}`, { method: "DELETE" }); await loadPeople(); rerender(); } catch (e) { setState(e.message, true); }
    });
    ops.appendChild(del);
    card.appendChild(ops);
    return card;
  }

  function peoplePanel(box) {
    box.innerHTML = "";
    const rerender = () => peoplePanel(box);
    box.appendChild(el("div", "sitem__hint", "她在事件和聊天里提到家人、朋友时，只会用这里的人。路人不用建档。事件发生后会自动在对应人物的「最近发生过的事」里追加一条。"));
    const bar = el("div", "pbar");
    const add = el("button", "btn btn--ghost", "＋ 添加人物");
    add.type = "button";
    add.addEventListener("click", () => { ppl.adding = !ppl.adding; rerender(); });
    const gen = el("button", "btn", ppl.busy ? "正在生成……" : "让 Phal 生成朋友草稿");
    gen.type = "button";
    gen.disabled = ppl.busy;
    gen.addEventListener("click", async () => {
      ppl.busy = true;
      rerender();
      try {
        const r = await api("/api/people/draft-friends", { method: "POST", body: JSON.stringify({}) });
        ppl.list = r.people;
        setState(`生成了 ${r.drafts.length} 个草稿，审阅后点「采用」`);
      } catch (e) { setState(e.message, true); }
      ppl.busy = false;
      rerender();
    });
    bar.append(add, gen);
    box.appendChild(bar);

    if (ppl.adding) {
      const form = el("form", "pform");
      const n = el("input", "input"); n.placeholder = "名字"; n.maxLength = 20;
      const r = el("input", "input"); r.placeholder = "和她的关系，比如：同桌";
      const k = el("select", "input");
      [["friend", "朋友"], ["family", "家人"]].forEach(([v, t]) => { const o = el("option", null, t); o.value = v; k.appendChild(o); });
      const ok = el("button", "btn", "添加"); ok.type = "submit";
      form.append(n, r, k, ok);
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        try { await api("/api/people", { method: "POST", body: JSON.stringify({ name: n.value, relation: r.value, kind: k.value }) }); ppl.adding = false; await loadPeople(); rerender(); }
        catch (err) { setState(err.message, true); }
      });
      box.appendChild(form);
    }

    const groups = [["家人", ppl.list.filter((p) => !p.draft && p.kind === "family")],
                    ["朋友", ppl.list.filter((p) => !p.draft && p.kind === "friend")],
                    ["草稿（等你审阅，还没有生效）", ppl.list.filter((p) => p.draft)]];
    groups.forEach(([title, list]) => {
      if (!list.length) return;
      box.appendChild(el("h3", "setgrp__sub", title));
      const grid = el("div", "pgrid");
      list.forEach((p) => grid.appendChild(personCard(p, rerender)));
      box.appendChild(grid);
    });
    if (!ppl.list.length) box.appendChild(el("div", "sitem__hint", "还没有人物。"));
  }

  /* ───────── 世界设定文件 ───────── */
  const wld = { tree: [], cat: null, file: null, text: "", dirty: false };

  async function loadWorld() {
    try { wld.tree = (await api("/api/world/files")).categories; } catch (_) { wld.tree = []; }
    if (!wld.tree.find((c) => c.name === wld.cat)) wld.cat = wld.tree.length ? wld.tree[0].name : null;
  }

  function worldPanel(box) {
    box.innerHTML = "";
    const rerender = () => worldPanel(box);
    box.appendChild(el("div", "sitem__hint", "世界设定：总纲（文件名以 _ 开头或带「总纲」）常驻上下文；其它文件是条目，文件名就是关键词，你的消息里出现关键词才会带进上下文。改完保存，下一条消息就生效。"));
    const cats = el("div", "wtabs");
    wld.tree.forEach((c) => {
      const b = el("button", "wtab" + (c.name === wld.cat ? " is-on" : ""), c.name);
      b.type = "button";
      b.addEventListener("click", () => { wld.cat = c.name; wld.file = null; rerender(); });
      cats.appendChild(b);
    });
    const nc = el("button", "wtab wtab--add", "＋ 新分类");
    nc.type = "button";
    nc.addEventListener("click", async () => {
      const name = prompt("新分类的名字：");
      if (!name) return;
      try { wld.tree = (await api("/api/world/category", { method: "POST", body: JSON.stringify({ name }) })).categories; wld.cat = name.trim(); rerender(); }
      catch (e) { setState(e.message, true); }
    });
    cats.appendChild(nc);
    box.appendChild(cats);
    const cat = wld.tree.find((c) => c.name === wld.cat);
    if (!cat) return;

    const main = el("div", "wmain");
    const list = el("div", "wfiles");
    cat.files.forEach((f) => {
      const b = el("button", "wfile" + (f.name === wld.file ? " is-on" : ""));
      b.type = "button";
      b.append(el("span", null, f.name), el("em", null, f.overview ? "总纲" : `${f.chars} 字`));
      b.addEventListener("click", async () => {
        try { wld.text = (await api(`/api/world/file?category=${encodeURIComponent(cat.name)}&name=${encodeURIComponent(f.name)}`)).content; wld.file = f.name; rerender(); }
        catch (e) { setState(e.message, true); }
      });
      list.appendChild(b);
    });
    const nf = el("button", "wfile wfile--add", "＋ 新文件");
    nf.type = "button";
    nf.addEventListener("click", async () => {
      const name = prompt("文件名（比如 _总纲.md、某国.md）：");
      if (!name) return;
      try {
        wld.tree = (await api("/api/world/file", { method: "PUT", body: JSON.stringify({ category: cat.name, name, content: "" }) })).categories;
        wld.file = /\.(md|txt)$/i.test(name) ? name.trim() : `${name.trim()}.md`;
        wld.text = "";
        rerender();
      } catch (e) { setState(e.message, true); }
    });
    list.appendChild(nf);
    main.appendChild(list);

    const ed = el("div", "wedit");
    if (wld.file) {
      ed.appendChild(el("div", "pcard__lab", `${cat.name} / ${wld.file}`));
      const ta = el("textarea", "textarea");
      ta.value = wld.text;
      ta.spellcheck = false;
      ta.rows = 14;
      const row = el("div", "pcard__ops");
      const save = el("button", "btn", "保存");
      save.type = "button";
      save.addEventListener("click", async () => {
        try { wld.tree = (await api("/api/world/file", { method: "PUT", body: JSON.stringify({ category: cat.name, name: wld.file, content: ta.value }) })).categories; wld.text = ta.value; setState("已保存，下一条消息就生效"); }
        catch (e) { setState(e.message, true); }
      });
      const del = el("button", "linkish", "删除这个文件");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm(`删掉「${wld.file}」？`)) return;
        try { wld.tree = (await api(`/api/world/file?category=${encodeURIComponent(cat.name)}&name=${encodeURIComponent(wld.file)}`, { method: "DELETE" })).categories; wld.file = null; rerender(); }
        catch (e) { setState(e.message, true); }
      });
      row.append(save, del);
      ed.append(ta, row);
    } else {
      ed.appendChild(el("div", "sitem__hint", "选一个文件来看或改，或者新建一个。"));
    }
    main.appendChild(ed);
    box.appendChild(main);
    placesPanel(box);
    notebookPanel(box);
  }

  /* ───────── 小本子的章节（app/notebook.py）：名字、准确度、顺序；数字类设置在下面「小本子」小节 ───────── */
  const nbk = { rows: [], accuracy: {}, dirty: false };

  async function loadNotebook() {
    try {
      const r = await api("/api/notebook");
      Object.assign(nbk, { rows: r.chapters.map((c) => ({ ...c })), accuracy: r.accuracy, dirty: false });
    } catch (_) { nbk.rows = []; }
  }

  function notebookPanel(box) {
    const wrap = el("div", "plc nbk");
    wrap.appendChild(el("h3", "setgrp__sub", "小本子的章节"));
    wrap.appendChild(el("div", "sitem__hint",
      "她的小本子（顶上的笔记本图标 / 房间书桌上的笔记本）分哪几章。「准确度」决定她写这一章时能不能有误解：尽量准确的章节写入时会用上面的世界设定和地点库校对，"
      + "听说的事允许夸张。「店铺」不能删（价格前后一致靠它）；章节里还有条目时也删不掉。"));
    if (!nbk.rows.length) { wrap.appendChild(el("div", "sitem__hint", "读取失败，刷新一下。")); box.appendChild(wrap); return; }
    const tbl = el("table", "stt__tbl");
    const head = el("tr");
    ["章节", "准确度", "条目", ""].forEach((t) => head.appendChild(el("th", null, t)));
    tbl.appendChild(head);
    const redraw = () => { box.innerHTML = ""; worldPanel(box); };
    const mark = () => { nbk.dirty = true; saveBtn.disabled = false; };
    nbk.rows.forEach((c, i) => {
      const tr = el("tr");
      const td = (node) => { const x = el("td"); x.appendChild(node); tr.appendChild(x); };
      const nm = el("input", "stt__in");
      nm.value = c.name; nm.maxLength = 12;
      nm.addEventListener("input", () => { c.name = nm.value; mark(); });
      td(nm);
      const acc = el("select", "stt__in");
      Object.entries(nbk.accuracy).forEach(([v, lb]) => { const o = el("option", null, lb); o.value = v; acc.appendChild(o); });
      acc.value = c.accuracy;
      acc.addEventListener("change", () => { c.accuracy = acc.value; mark(); });
      td(acc);
      td(el("span", null, c.key ? String(c.count || 0) : "新"));
      const ops = el("span");
      const up = el("button", "linkish", "↑");
      up.type = "button"; up.disabled = i === 0;
      up.addEventListener("click", () => { nbk.rows.splice(i - 1, 0, nbk.rows.splice(i, 1)[0]); nbk.dirty = true; redraw(); });
      const del = el("button", "linkish", "删");
      del.type = "button";
      del.addEventListener("click", () => {
        if (c.key === "shop") { setState("「店铺」不能删", true); return; }
        if (c.count) { setState(`「${c.name}」里还有 ${c.count} 条，先在小本子里把它们挪走或删掉`, true); return; }
        nbk.rows.splice(i, 1); nbk.dirty = true; redraw();
      });
      ops.append(up, del);
      td(ops);
      tbl.appendChild(tr);
    });
    wrap.appendChild(tbl);
    const row = el("div", "pcard__ops");
    const add = el("button", "linkish", "＋ 加一章");
    add.type = "button";
    add.addEventListener("click", () => { nbk.rows.push({ key: "", name: "新章节", accuracy: "accurate", count: 0 }); nbk.dirty = true; redraw(); });
    const saveBtn = el("button", "btn", "保存章节");
    saveBtn.type = "button";
    saveBtn.disabled = !nbk.dirty;
    saveBtn.addEventListener("click", async () => {
      try {
        const r = await api("/api/notebook/chapters", { method: "PUT", body: JSON.stringify({ chapters: nbk.rows }) });
        Object.assign(nbk, { rows: r.chapters.map((c) => ({ ...c })), dirty: false });
        setState("章节已保存");
        redraw();
      } catch (e) { setState(e.message, true); }
    });
    const reload = el("button", "linkish", "放弃没保存的改动");
    reload.type = "button";
    reload.addEventListener("click", async () => { await loadNotebook(); redraw(); });
    row.append(saveBtn, add, reload);
    wrap.appendChild(row);
    box.appendChild(wrap);
  }

  /* ───────── 地点库（作者层，app/places.py）：她出门去哪、离家多远、按哪个地点算价格；将来大世界地图的数据 ───────── */
  const plc = { rows: [], kinds: {}, prices: {}, hereLabel: "", dirty: false };

  async function loadPlaces() {
    try {
      const r = await api("/api/places");
      Object.assign(plc, { rows: r.places, kinds: r.kinds, prices: r.price_places, hereLabel: r.here_label, dirty: false });
    } catch (_) { plc.rows = []; }
  }

  function placeOrder() {
    // 按上下级排成树：[行号, 深度]
    const kids = new Map();
    plc.rows.forEach((p, i) => { const k = p.parent || ""; if (!kids.has(k)) kids.set(k, []); kids.get(k).push(i); });
    const out = [];
    const seen = new Set();
    const walk = (pid, d) => (kids.get(pid) || []).forEach((i) => {
      if (seen.has(i)) return;
      seen.add(i); out.push([i, d]);
      if (plc.rows[i].id) walk(plc.rows[i].id, d + 1);
    });
    walk("", 0);
    plc.rows.forEach((p, i) => { if (!seen.has(i)) out.push([i, 0]); });  // 上级还没保存 / 找不到的放最后
    return out;
  }

  function placesPanel(box) {
    const wrap = el("div", "plc");
    wrap.appendChild(el("h3", "setgrp__sub", "地点"));
    wrap.appendChild(el("div", "sitem__hint",
      "世界上有哪些地方、谁在谁里面、离家多远。她出门时由代码在这里挑目的地：「自己去的权重」越大越常去，0 = 不会自己去；"
      + "来回路程太长的地方，只有周末 / 和朋友一起时才会「特地出门」去，那一趟会拉长到够来回。「价格按」决定在那里买东西用价格带的哪个地点系数（不选就跟上级）。"
      + "「她不知道」的地方她不会去，将来地图上对她是迷雾。店不用自己加：她第一次去某家店时会自动登记（种类「店」，不会被当成目的地）。"
      + (plc.hereLabel ? `　她现在在：${plc.hereLabel}。` : "")));
    const tbl = el("table", "stt__tbl plc__tbl");
    const head = el("tr");
    ["名字", "种类", "上级", "离家（分钟）", "自己去的权重", "价格按", "她不知道", "备注", ""].forEach((t) => head.appendChild(el("th", null, t)));
    tbl.appendChild(head);
    const mark = () => { plc.dirty = true; saveBtn.disabled = false; };
    const sel = (opts, val, on) => {
      const s = el("select", "stt__in");
      opts.forEach(([v, lb]) => { const o = el("option", null, lb); o.value = v; s.appendChild(o); });
      s.value = val;
      s.addEventListener("change", () => { on(s.value); mark(); });
      return s;
    };
    const num = (val, step, on) => {
      const i = el("input", "stt__in stt__num");
      i.type = "number"; i.min = 0; i.step = step; i.value = val;
      i.addEventListener("input", () => { if (i.value !== "") { on(Number(i.value)); mark(); } });
      return i;
    };
    placeOrder().forEach(([i, depth]) => {
      const p = plc.rows[i];
      const tr = el("tr");
      const nm = el("input", "stt__in plc__name");
      nm.value = p.name;
      nm.style.marginLeft = `${depth * 14}px`;
      nm.title = p.id ? `id：${p.id}` : "保存后自动给 id";
      nm.addEventListener("input", () => { p.name = nm.value; mark(); });
      const td = (node) => { const c = el("td"); c.appendChild(node); tr.appendChild(c); };
      td(nm);
      td(sel(Object.entries(plc.kinds), p.kind, (v) => { p.kind = v; }));
      td(sel([["", "（无）"], ...plc.rows.filter((x) => x.id && x.id !== p.id).map((x) => [x.id, x.name])], p.parent || "", (v) => { p.parent = v; }));
      td(num(p.travel_min, 5, (v) => { p.travel_min = v; }));
      td(num(p.visit, 0.05, (v) => { p.visit = v; }));
      td(sel([["", "跟上级"], ...Object.entries(plc.prices)], p.price_place || "", (v) => { p.price_place = v; }));
      const sc = el("input");
      sc.type = "checkbox"; sc.checked = !!p.secret;
      sc.addEventListener("change", () => { p.secret = sc.checked; mark(); });
      td(sc);
      const nt = el("input", "stt__in plc__note");
      nt.value = p.note || "";
      nt.addEventListener("input", () => { p.note = nt.value; mark(); });
      td(nt);
      const del = el("button", "linkish", "删");
      del.type = "button";
      del.addEventListener("click", () => {
        if (plc.rows.some((x) => x.parent && x.parent === p.id)) { setState(`「${p.name}」下面还有地点，先删掉或挪走它们`, true); return; }
        plc.rows.splice(i, 1); plc.dirty = true; box.innerHTML = ""; worldPanel(box);
      });
      td(del);
      tbl.appendChild(tr);
    });
    const scroll = el("div", "stt__scroll");
    scroll.appendChild(tbl);
    wrap.appendChild(scroll);
    const ops = el("div", "pcard__ops");
    const add = el("button", "linkish", "＋ 加一个地点");
    add.type = "button";
    add.addEventListener("click", () => {
      const town = plc.rows.find((x) => x.id === "zuili");
      plc.rows.push({ id: "", name: "新地点", kind: "landmark", parent: town ? town.id : "", travel_min: 10, visit: 0, price_place: "", secret: false, map: null, note: "" });
      plc.dirty = true; box.innerHTML = ""; worldPanel(box);
    });
    const saveBtn = el("button", "btn", "保存地点");
    saveBtn.type = "button";
    saveBtn.disabled = !plc.dirty;
    saveBtn.addEventListener("click", async () => {
      try {
        const r = await api("/api/places", { method: "PUT", body: JSON.stringify({ places: plc.rows }) });
        Object.assign(plc, { rows: r.places, hereLabel: r.here_label, dirty: false });
        setState("地点已保存");
        box.innerHTML = ""; worldPanel(box);
      } catch (e) { setState(e.message, true); }
    });
    const reload = el("button", "linkish", "放弃没保存的改动");
    reload.type = "button";
    reload.addEventListener("click", async () => { await loadPlaces(); box.innerHTML = ""; worldPanel(box); });
    ops.append(saveBtn, add, reload);
    wrap.appendChild(ops);
    box.appendChild(wrap);
  }

  /* ───────── 状态与概率：一个状态一行的紧凑表格 ───────── */
  const STATE_FIELDS = [["w", "权重"], ["dmin", "最短"], ["dmax", "最长"], ["from", "几点起"], ["to", "几点止"], ["weather", "天气"], ["spends", "花钱"], ["outside", "在外面"]];
  const STATE_HINTS = {
    w: "出现权重：越大越容易轮到，0 = 不会出现", dmin: "最短持续（分钟）", dmax: "最长持续（分钟）", from: "最早几点出现", to: "最晚几点（24 = 不限）",
    weather: "哪种天气下才会出现", spends: "做这件事时可能触发消费", outside: "算在外面：回复更慢、更短",
  };

  const STATE_CFG = {
    patterns: [["dmin", /^state_dur_(.+)_min$/], ["dmax", /^state_dur_(.+)_max$/], ["w", /^state_w_(.+)$/], ["from", /^state_from_(.+)$/],
               ["to", /^state_to_(.+)$/], ["weather", /^state_weather_(.+)$/], ["spends", /^state_spends_(.+)$/], ["outside", /^state_outside_(.+)$/]],
    fields: STATE_FIELDS, hints: STATE_HINTS, head: "状态",
    intro: "权重 = 越大越容易轮到（0 = 不出现）　时间 = 持续分钟　几点起止 = 只在这段时间出现　鼠标停在表头 / 格子上有说明。改过的格子是黄色。",
  };
  const EVENT_CFG = {
    patterns: [["w", /^ev_w_(.+)$/], ["from", /^ev_from_(.+)$/], ["to", /^ev_to_(.+)$/], ["weather", /^ev_weather_(.+)$/], ["out", /^ev_out_(.+)$/]],
    fields: [["w", "权重"], ["from", "几点起"], ["to", "几点止"], ["weather", "天气"], ["out", "需出门"]],
    hints: { w: "越大越容易抽到这种事", from: "最早几点发生", to: "最晚几点（24 = 不限）", weather: "哪种天气下才会发生", out: "需要出门的事，睡觉 / 生病时不发生" },
    head: "事件类型", intro: "每种事件的权重和发生条件。到点不满足条件会顺延一次，还不行就作废。改过的格子是黄色。",
  };

  const MONEY_CFG = {
    patterns: [["w", /^spend_w_(.+)$/], ["room", /^spend_room_(.+)$/]],
    fields: [["w", "权重"], ["room", "放进房间"]],
    hints: { w: "越大越常随机买到，0 = 不会随机买（外卖、出去吃由饭点调度决定，没有权重）", room: "买了会留下来的东西自动放进她的房间" },
    head: "东西", intro: "每类东西随机买的权重，以及买了要不要放进房间。多少钱由她按上面的「价格带 × 修正系数」自己定。改过的格子是黄色。",
  };

  function parseKey(cfg, key) {
    for (const [f, re] of cfg.patterns) {
      const m = re.exec(key);
      if (m) return [m[1], f];
    }
    return null;
  }

  function compactTable(items, cfg) {
    const rows = new Map();
    const rest = [];
    items.forEach((it) => {
      const p = parseKey(cfg, it.key);
      if (!p) { rest.push(it); return; }
      if (!rows.has(p[0])) rows.set(p[0], { key: p[0], label: "", cells: {} });
      const r = rows.get(p[0]);
      r.cells[p[1]] = it;
      const m = /「(.+?)」/.exec(it.label);
      if (m) r.label = m[1];
    });
    const wrap = el("div", "stt");
    wrap.appendChild(el("div", "sitem__hint", cfg.intro));
    const tbl = el("table", "stt__tbl");
    const head = el("tr");
    head.appendChild(el("th", "stt__name", cfg.head));
    cfg.fields.forEach(([f, label]) => { const th = el("th", null, label); th.title = cfg.hints[f]; head.appendChild(th); });
    head.appendChild(el("th", null, ""));
    tbl.appendChild(head);
    const stub = { querySelector: () => null, appendChild: () => {} };
    rows.forEach((r) => {
      const tr = el("tr");
      tr.appendChild(el("td", "stt__name", r.label || r.key));
      cfg.fields.forEach(([f]) => {
        const td = el("td");
        const it = r.cells[f];
        if (!it) { td.appendChild(el("span", "stt__na", "—")); tr.appendChild(td); return; }
        td.title = `${r.label}：${cfg.hints[f]}`;
        let inp;
        if (it.kind === "bool") {
          inp = document.createElement("input");
          inp.type = "checkbox";
          inp.checked = !!it.value;
          inp.addEventListener("change", async () => { td.classList.toggle("is-mod", inp.checked !== it.default); await save(it, inp.checked, stub); });
        } else if (it.kind === "select") {
          inp = el("select", "stt__in");
          (it.choices || []).forEach((c) => { const o = el("option", null, c.label); o.value = c.value; inp.appendChild(o); });
          inp.value = it.value;
          inp.addEventListener("change", async () => { td.classList.toggle("is-mod", inp.value !== it.default); await save(it, inp.value, stub); });
        } else {
          inp = el("input", "stt__in stt__num");
          inp.type = "number";
          inp.min = it.min; inp.max = it.max; inp.step = it.step;
          inp.value = it.value;
          inp.addEventListener("input", () => {
            if (inp.value === "") return;
            td.classList.toggle("is-mod", Number(inp.value) !== it.default);
            inp.classList.remove("is-bad");
            saveLater(it, Number(inp.value), stub);
          });
        }
        td.classList.toggle("is-mod", !!it.modified);
        td.appendChild(inp);
        tr.appendChild(td);
      });
      const rs = el("td");
      if (Object.values(r.cells).some((c) => c.modified)) {
        const b = el("button", "linkish", "默认");
        b.type = "button";
        b.title = "把这个状态的所有设置恢复默认";
        b.addEventListener("click", async () => { if (confirm(`把「${r.label}」恢复成默认设置？`)) await resetKeys(Object.values(r.cells).map((c) => c.key), true); });
        rs.appendChild(b);
      }
      tr.appendChild(rs);
      tbl.appendChild(tr);
    });
    const scroll = el("div", "stt__scroll");
    scroll.appendChild(tbl);
    wrap.appendChild(scroll);
    return { node: wrap, rest };
  }

  /* ───────── 事件：今天的计划 + 最近的事件 ───────── */
  async function eventsPanel(box) {
    box.innerHTML = "";
    let d;
    try { d = await api("/api/events"); } catch (_) { return; }
    const STATE = { pending: "待发生", fired: "已发生", void: "作废" };
    const plan = el("div", "evp");
    const ptitle = el("div", "evp__title", `今天的计划${d.sick_days ? `（今天判定生病 ${d.sick_days} 天）` : ""}`);
    const whyb = el("button", "btn btn--ghost btn--sm", "为什么");
    whyb.type = "button";
    whyb.title = "今天的事是怎么抽出来的（几件、什么类型、概率）";
    whyb.addEventListener("click", () => window.PhalWhy && window.PhalWhy.open("event"));
    ptitle.appendChild(whyb);
    plan.appendChild(ptitle);
    plan.appendChild(el("div", "evp__line", d.plan.length ? d.plan.map((e) => `${e.time} ${e.label}（${STATE[e.state] || e.state}）`).join("　") : "今天没有安排事件。"));
    box.appendChild(plan);
    if (d.recent.length) {
      const rec = el("div", "evp");
      rec.appendChild(el("div", "evp__title", "最近发生的事（包括她没告诉你的）"));
      d.recent.forEach((e) => {
        const line = el("div", "evp__line");
        line.appendChild(document.createTextNode(`${e.day.slice(5)} ${e.time}　${e.text}　`));
        line.appendChild(el("em", e.shared ? "evp__tag is-shared" : "evp__tag", e.shared ? "告诉你了" : "没告诉你"));
        rec.appendChild(line);
      });
      box.appendChild(rec);
    }
  }

  /* ───────── 零花钱：当前余额 ───────── */
  async function moneyStatus(box) {
    const line = el("div", "wstat");
    box.appendChild(line);
    try {
      const d = await api("/api/money");
      const f = (b) => (b / 10).toFixed(1);
      line.appendChild(el("span", null, `余额 ${f(d.balance)} 钱（${d.tier_label}）　本月已花 ${f(d.summary.spent)} 钱　${d.next_grant.slice(5, 7).replace(/^0/, "")} 月 ${d.next_grant.slice(8).replace(/^0/, "")} 日发下一笔`));
      const b = el("button", "linkish", "打开账本");
      b.type = "button";
      b.addEventListener("click", () => { close(); if (window.PhalMoney) window.PhalMoney.open(); });
      line.appendChild(b);
    } catch (_) { line.textContent = "零花钱状态暂时取不到"; }
  }

  /* ───────── 连发和等待：按当前参数算出来的实际比例（改参数时实时更新） ───────── */
  function burstRatios(box, g) {
    const wrap = el("div", "brat");
    wrap.appendChild(el("div", "sitem__hint", "我发完一条、还在接着打字的时候，她会怎么做（按下面的参数算）："));
    const tbl = el("table", "stt__tbl brat__tbl");
    wrap.appendChild(tbl);
    box.appendChild(wrap);
    const val = (key) => {
      const inp = box.querySelector(`[data-key="${key}"] input[type="number"]`) || box.querySelector(`[data-key="${key}"] input`);
      const it = g.items.find((x) => x.key === key);
      const v = inp && inp.value !== "" ? Number(inp.value) : Number(it ? it.value : 0);
      return Number.isFinite(v) ? v : 0;
    };
    const pct = (x) => `${Math.round(x * 100)}%`;
    const draw = () => {
      const rows = [["idle", "闲着（喝茶、发呆、刷视频……）"], ["normal", "一般"], ["busy", "忙（出门、打游戏、追剧、写东西……）"]];
      let h = "<tr><th>她现在</th><th>先回一句</th><th>等我打完一起回</th><th>没注意到、直接回</th></tr>";
      rows.forEach(([t, label]) => {
        const k = val(`burst_k_${t}`);
        const notice = Math.min(1, Math.max(0, val("burst_p_notice") * k)), inter = Math.min(1, Math.max(0, val("burst_p_interject") * k));
        h += `<tr><td class="stt__name">${label}</td><td>${pct(notice * inter)}</td><td>${pct(notice * (1 - inter))}</td><td>${pct(1 - notice)}</td></tr>`;
      });
      tbl.innerHTML = h;
    };
    setTimeout(draw, 0);
    box.addEventListener("input", () => setTimeout(draw, 0));
  }

  /* ───────── 说过的打算：当前的和历史 ───────── */
  function intentPanel(box) {
    const wrap = el("div", "itl");
    const top = el("div", "itl__top");
    top.appendChild(el("div", "sitem__hint", "她在聊天里说过的打算、答应你的事：内容、强度、时间范围、结果、原因（最近 60 条）。"));
    const refresh = el("button", "btn btn--ghost btn--sm", "刷新");
    refresh.type = "button";
    top.appendChild(refresh);
    wrap.appendChild(top);
    const tbl = el("table", "stt__tbl itl__tbl");
    wrap.appendChild(tbl);
    box.appendChild(wrap);
    const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const draw = async () => {
      try {
        const r = await api("/api/intents");
        if (!r.intents.length) { tbl.innerHTML = '<tr><td class="sitem__hint">还没有。</td></tr>'; return; }
        let h = "<tr><th>说的</th><th>哪类</th><th>强度</th><th>时间</th><th>结果</th><th>原因</th></tr>";
        r.intents.forEach((it) => {
          const res = it.status_label + (it.status === "pending" && it.decision ? (it.decision === "keep" ? "（会照做）" : "（不会照做）") : "")
            + (it.status === "broken" && it.mentioned ? "，跟你提过" : "");
          h += `<tr class="itl--${esc(it.status)}"><td class="stt__name">${esc(it.text)}</td><td>${esc(it.kind_label)}</td><td>${esc(it.strength_label)}</td>`
            + `<td>${esc(it.range)}</td><td>${esc(res)}</td><td>${esc(it.reason)}</td></tr>`;
        });
        tbl.innerHTML = h;
      } catch (_) { tbl.innerHTML = '<tr><td class="sitem__hint">暂时取不到</td></tr>'; }
    };
    refresh.addEventListener("click", draw);
    draw();
  }

  /* 运行开销：最近几轮对话各调了几次模型、花了多久、多少 token；今天后台（她自己过日子）的开销 */
  function usagePanel(box) {
    const wrap = el("div", "itl");
    const top = el("div", "itl__top");
    top.appendChild(el("div", "sitem__hint", "每轮对话调了几次模型、一共多久、输入 / 输出多少 token（回复后的记忆提取也算在这一轮里）。"));
    const refresh = el("button", "btn btn--ghost btn--sm", "刷新");
    refresh.type = "button";
    top.appendChild(refresh);
    wrap.appendChild(top);
    const tbl = el("table", "stt__tbl itl__tbl");
    const foot = el("div", "sitem__hint");
    wrap.append(tbl, foot);
    box.appendChild(wrap);
    const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const sec = (ms) => (ms / 1000).toFixed(1) + " 秒";
    const by = (b) => Object.entries(b || {}).map(([k, v]) => `${k} ${v.calls}`).join("、");
    const draw = async () => {
      try {
        const r = await api("/api/usage");
        if (!r.enabled) { tbl.innerHTML = '<tr><td class="sitem__hint">统计关着（上面的开关）。</td></tr>'; foot.textContent = ""; return; }
        if (!r.rounds.length) tbl.innerHTML = '<tr><td class="sitem__hint">还没有记录。</td></tr>';
        else {
          let h = "<tr><th>时间</th><th>调用</th><th>耗时</th><th>输入</th><th>输出</th><th>都是什么</th></tr>";
          r.rounds.forEach((x) => {
            h += `<tr><td class="stt__name">${esc(x.at)}</td><td>${x.calls}${x.failed ? `（失败 ${x.failed}）` : ""}</td><td>${sec(x.ms)}</td>`
              + `<td>${x.prompt}${x.cached ? `（缓存 ${x.cached}）` : ""}</td><td>${x.completion}</td><td>${esc(by(x.by))}</td></tr>`;
          });
          tbl.innerHTML = h;
        }
        const t = r.today, b = r.background_today;
        foot.textContent = `今天一共：${t.calls} 次调用、${sec(t.ms)}、输入 ${t.prompt} / 输出 ${t.completion} token；`
          + `其中后台（她自己过日子）${b.calls} 次、输入 ${b.prompt} / 输出 ${b.completion}` + (b.calls ? `（${by(b.by)}）` : "") + "。";
      } catch (_) { tbl.innerHTML = '<tr><td class="sitem__hint">暂时取不到</td></tr>'; }
    };
    refresh.addEventListener("click", draw);
    draw();
  }

  /* ───────── 页面 ───────── */
  // 这几个分区排得紧凑：数字 / 开关 / 时间一行放几项，说明放到鼠标悬停里；长文本、列表、表格仍占整行
  const TIGHT_GROUPS = new Set(["weather", "routine", "meals", "reading", "mood", "room", "burst", "intent"]);
  const WIDE_KINDS = new Set(["list", "longtext", "text", "secret", "table"]);

  async function load() {
    S.groups = (await api("/api/config")).groups;
  }

  function render() {
    const nav = $("#setNav");
    const body = $("#setBody");
    nav.innerHTML = "";
    body.innerHTML = "";
    S.groups.forEach((g) => {
      const a = el("button", "setnav__a", g.label);
      a.type = "button";
      a.addEventListener("click", () => {
        S.open.add(g.id);
        LS.set("settings.open", JSON.stringify([...S.open]));
        render();
        const target = $(`#grp-${g.id}`);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      nav.appendChild(a);

      const sec = el("section", "setgrp" + (S.open.has(g.id) ? " is-open" : ""));
      sec.id = `grp-${g.id}`;
      const head = el("button", "setgrp__head");
      head.type = "button";
      head.append(el("span", "setgrp__title", g.label), el("span", "setgrp__caret", "›"));
      head.addEventListener("click", () => {
        if (S.open.has(g.id)) S.open.delete(g.id); else S.open.add(g.id);
        LS.set("settings.open", JSON.stringify([...S.open]));
        sec.classList.toggle("is-open", S.open.has(g.id));
        sec.querySelectorAll("textarea").forEach((t) => { t.style.height = "auto"; t.style.height = `${Math.min(520, Math.max(120, t.scrollHeight + 4))}px`; });
      });
      sec.appendChild(head);

      const inner = el("div", "setgrp__body");
      if (g.id === "weather") weatherStatus(inner);
      if (g.id === "burst") burstRatios(inner, g);
      if (g.id === "intent") intentPanel(inner);
      if (g.id === "model") usagePanel(inner);
      if (g.panel === "people") { const box = el("div", "ppl"); peoplePanel(box); inner.appendChild(box); }
      if (g.panel === "world") { const box = el("div", "wld"); worldPanel(box); inner.appendChild(box); }
      if (g.id === "festivals") {
        const box = el("div", "fest2");
        festPanel(box);
        inner.appendChild(box);
      } else {
        let lastSection = null;
        let shown = g.items;
        if (g.id === "events") {
          const pbox = el("div", "evbox");
          eventsPanel(pbox);
          inner.appendChild(pbox);
          const et = compactTable(g.items, EVENT_CFG);
          inner.appendChild(et.node);
          shown = et.rest;
          if (shown.length) inner.appendChild(el("h3", "setgrp__sub", "其它"));
        }
        if (g.id === "states") {
          const st = compactTable(g.items, STATE_CFG);
          inner.appendChild(st.node);
          shown = st.rest;
          if (shown.length) inner.appendChild(el("h3", "setgrp__sub", "其它"));
        }
        let priceTable = null;
        if (g.id === "money") {
          moneyStatus(inner);
          const mt = compactTable(g.items, MONEY_CFG);
          priceTable = mt.node;
          shown = mt.rest;
        }
        const tight = TIGHT_GROUPS.has(g.id);
        let grid = null;
        shown.forEach((item) => {
          if (item.section && item.section !== lastSection) {
            inner.appendChild(el("h3", "setgrp__sub", item.section));
            lastSection = item.section;
            grid = null;
          }
          if (item.kind === "table") {                         // 表格（每月气候表、分档表）：默认收起，点开再看
            const det = el("details", "sfold");
            det.appendChild(el("summary", "sfold__sum", item.label));
            det.appendChild(itemRow(item, false));
            inner.appendChild(det);
            grid = null;
            return;
          }
          if (tight && !WIDE_KINDS.has(item.kind)) {           // 紧凑：一行放几项（说明放到鼠标悬停里）
            if (!grid) { grid = el("div", "sgrid"); inner.appendChild(grid); }
            grid.appendChild(itemRow(item, true));
            return;
          }
          grid = null;
          inner.appendChild(itemRow(item, tight || g.id === "states" || g.id === "events" || g.id === "money"));
        });
        if (priceTable) {
          inner.appendChild(el("h3", "setgrp__sub", "买什么"));
          inner.appendChild(priceTable);
        }
        if (g.items.some((it) => it.kind !== "secret") && g.id !== "states" && g.id !== "events") {
          const foot = el("div", "setgrp__foot");
          const reset = el("button", "linkish", "恢复本分区的默认值");
          reset.type = "button";
          reset.addEventListener("click", async () => {
            if (!confirm(`把「${g.label}」里所有设置恢复成默认值？${g.id === "persona" ? "\n（包括人设正文，会被换成默认人设。）" : ""}`)) return;
            const r = await api("/api/config/reset", { method: "POST", body: JSON.stringify({ group: g.id }) });
            announce(r.settings);
            await load();
            render();
            setState("已恢复默认");
          });
          foot.appendChild(reset);
          inner.appendChild(foot);
        }
      }
      sec.appendChild(inner);
      body.appendChild(sec);
    });
  }

  /* ───────── 打开 / 关闭 ───────── */
  async function open(groupId) {
    try { S.open = new Set(JSON.parse(LS.get("settings.open", '["persona"]'))); } catch (_) { S.open = new Set(["persona"]); }
    if (groupId) S.open.add(groupId);
    ROOT.hidden = false;
    document.body.style.overflow = "hidden";
    setState("");
    try {
      await Promise.all([load(), loadFest(), loadPeople(), loadWorld(), loadPlaces(), loadNotebook()]);
      render();
      if (groupId) { const t = $(`#grp-${groupId}`); if (t) t.scrollIntoView({ block: "start" }); }
    } catch (e) {
      $("#setBody").textContent = `设置加载失败：${e.message}`;
    }
  }

  function close() {
    if (ROOT.hidden) return;
    Object.values(S.timers).forEach(clearTimeout);   // 还没触发的自动保存：现在就存，别丢
    Object.values(S.pending).forEach((run) => run());
    ROOT.hidden = true;
    document.body.style.overflow = "";
  }

  $("#setBack").addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !ROOT.hidden) close(); });
  window.PhalSettings = { open, close };
})();
