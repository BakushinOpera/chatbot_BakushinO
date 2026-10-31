/* QQ 页：连接状态、所有 QQ 设置、群友印象、模拟群聊。主界面书架按钮旁边的「QQ」按钮打开它。
 * 设置存在 QQ 自己的数据库里（不进统一设置页），界面按后端 /api/qq/config 给的 schema 生成，改完自动保存。
 * 这里不显示任何真实的 QQ 聊天记录：只有连接状态、设置、她对群友的印象，以及假的模拟群聊。
 */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const ROOT = $("#qqRoot");
  if (!ROOT) return;

  const S = { schema: null, values: null, fields: {}, tab: "conn", status: null, timers: {}, sim: null, simSel: null, replyTo: null, simOpts: { instant: true, force: false, state: "", busy: "" } };

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
  const jcopy = (v) => JSON.parse(JSON.stringify(v));

  let stateTimer = null;
  function setState(text, bad) {
    const n = $("#qqState");
    n.textContent = text;
    n.classList.toggle("is-bad", !!bad);
    clearTimeout(stateTimer);
    if (text && !bad && text !== "正在保存…") stateTimer = setTimeout(() => { n.textContent = ""; }, 2200);
  }

  /* ───────── 状态（按钮上的小圆点 + 连接页的状态卡）───────── */
  const DOT = { connected: "is-ok", connecting: "is-wait", error: "is-bad", disabled: "is-off", stopped: "is-off" };
  async function refreshStatus() {
    try { S.status = await api("/api/qq/status"); } catch (_) { S.status = null; }
    const dot = $("#btnQQ .qqdot");
    if (dot) {
      dot.className = `qqdot ${S.status ? DOT[S.status.state] || "is-off" : "is-off"}`;
      $("#btnQQ").title = S.status ? `QQ：${S.status.label}${S.status.state === "connected" && S.status.nickname ? `（${S.status.nickname}）` : ""}` : "QQ";
    }
    if (!ROOT.hidden && S.tab === "conn") paintStatusCard();
  }

  /* ───────── 设置：读 / 存 ───────── */
  async function loadConfig() {
    const r = await api("/api/qq/config");
    S.schema = r.schema;
    S.values = r.values;
    S.fields = {};
    r.schema.fields.forEach((f) => { f.value = r.values[f.key]; S.fields[f.key] = f; });
  }

  async function save(f, value) {
    setState("正在保存…");
    try {
      const r = await api("/api/qq/config", { method: "PUT", body: JSON.stringify({ values: { [f.key]: value } }) });
      S.values = r.values;
      Object.keys(r.values).forEach((k) => { if (S.fields[k]) S.fields[k].value = r.values[k]; });
      setState("已保存");
      if (["enabled", "host", "port", "token"].includes(f.key)) setTimeout(refreshStatus, 600);
      return true;
    } catch (e) {
      setState(`保存失败：${e.message}`, true);
      return false;
    }
  }
  function saveLater(f, value, delay = 700) {
    clearTimeout(S.timers[f.key]);
    setState("正在保存…");
    S.timers[f.key] = setTimeout(() => save(f, value), delay);
  }

  /* ───────── 控件 ───────── */
  function numberCtl(f) {
    const wrap = el("div", "sitem__ctl");
    const inp = el("input", "input sitem__num");
    inp.type = "number";
    if (f.min != null) inp.min = f.min;
    if (f.max != null) inp.max = f.max;
    if (f.step != null) inp.step = f.step;
    inp.value = f.value;
    inp.addEventListener("input", () => { if (inp.value !== "") saveLater(f, Number(inp.value)); });
    wrap.appendChild(inp);
    if (f.unit) wrap.appendChild(el("span", "sitem__unit", f.unit));
    return wrap;
  }
  function boolCtl(f) {
    const lab = el("label", "switch");
    const inp = document.createElement("input");
    inp.type = "checkbox";
    inp.checked = !!f.value;
    inp.addEventListener("change", () => save(f, inp.checked));
    lab.append(inp, el("span", "switch__box"));
    return lab;
  }
  function textCtl(f) {
    const inp = el("input", "input");
    inp.type = "text";
    inp.value = f.value || "";
    inp.spellcheck = false;
    inp.addEventListener("input", () => saveLater(f, inp.value));
    return inp;
  }
  function secretCtl(f) {
    const wrap = el("div", "sitem__ctl sitem__ctl--col");
    const inp = el("input", "input");
    inp.type = "password";
    inp.autocomplete = "off";
    const state = el("div", "sitem__count");
    const paint = () => {
      const set = !!f.value;
      inp.placeholder = set ? "已保存（留空则不改动）" : "没设（NapCat 那边也没设 token 就留空）";
      state.textContent = set ? "已保存" : "未设置";
    };
    paint();
    inp.addEventListener("change", async () => {
      if (!inp.value.trim()) return;
      if (await save(f, inp.value.trim())) { inp.value = ""; paint(); }
    });
    const clear = el("button", "linkish", "清除");
    clear.type = "button";
    clear.addEventListener("click", async () => { if (f.value && confirm("清除 token？") && (await save(f, ""))) paint(); });
    const line = el("div", "sitem__ctl");
    line.append(state, clear);
    wrap.append(inp, line);
    return wrap;
  }
  function longTextCtl(f) {
    const wrap = el("div", "sitem__long");
    const ta = el("textarea", "textarea");
    ta.value = f.value || "";
    ta.spellcheck = false;
    const count = el("div", "sitem__count", `${ta.value.length} 字`);
    const fit = () => { ta.style.height = "auto"; ta.style.height = `${Math.min(520, Math.max(120, ta.scrollHeight + 4))}px`; };
    ta.addEventListener("input", () => { count.textContent = `${ta.value.length} 字`; fit(); saveLater(f, ta.value, 900); });
    wrap.append(ta, count);
    setTimeout(fit, 0);
    return wrap;
  }
  function listCtl(f) {
    const wrap = el("div", "sitem__long");
    const ta = el("textarea", "textarea textarea--list");
    ta.rows = Math.min(10, Math.max(3, (f.value || []).length + 1));
    ta.value = (f.value || []).join("\n");
    ta.placeholder = "一行一个";
    ta.spellcheck = false;
    ta.addEventListener("input", () => saveLater(f, ta.value.split("\n").map((s) => s.trim()).filter(Boolean), 900));
    wrap.append(ta, el("div", "sitem__count", "一行一个"));
    return wrap;
  }
  function selectCtl(f) {
    const sel = el("select", "input");
    (f.choices || []).forEach((c) => { const o = el("option", null, c.label); o.value = c.value; sel.appendChild(o); });
    sel.value = f.value;
    sel.addEventListener("change", () => save(f, sel.value));
    return sel;
  }
  function cellInput(col, value, onChange) {
    if (col.kind === "bool") {
      const inp = document.createElement("input");
      inp.type = "checkbox";
      inp.checked = !!value;
      inp.addEventListener("change", () => onChange(inp.checked));
      return inp;
    }
    if (col.kind === "select") {
      const sel = el("select", "input stbl__in");
      (col.choices || []).forEach((c) => { const o = el("option", null, c.label); o.value = c.value; sel.appendChild(o); });
      sel.value = value;
      sel.addEventListener("change", () => onChange(sel.value));
      return sel;
    }
    const inp = el("input", "input stbl__in");
    inp.type = col.kind === "text" ? "text" : "number";
    if (col.kind !== "text") { inp.min = col.min; inp.max = col.max; inp.step = col.step; }
    inp.value = value;
    inp.addEventListener("input", () => { if (inp.value !== "") onChange(col.kind === "text" ? inp.value : Number(inp.value)); });
    return inp;
  }
  function tableCtl(f) {
    const wrap = el("div", "sitem__table");
    const tbl = el("table", "stbl");
    const head = el("tr");
    head.appendChild(el("th", null, ""));
    f.columns.forEach((c) => head.appendChild(el("th", null, c.label)));
    tbl.appendChild(head);
    const val = jcopy(f.value);
    f.row_keys.forEach((rk) => {
      const tr = el("tr");
      tr.appendChild(el("th", null, rk.label));
      f.columns.forEach((c) => {
        const td = el("td");
        td.appendChild(cellInput(c, (val[rk.key] || {})[c.key], (v) => { val[rk.key] = Object.assign(val[rk.key] || {}, { [c.key]: v }); saveLater(f, jcopy(val), 900); }));
        tr.appendChild(td);
      });
      tbl.appendChild(tr);
    });
    wrap.appendChild(tbl);
    return wrap;
  }
  function rowsCtl(f) {
    const wrap = el("div", "sitem__table");
    let rows = jcopy(f.value || []);
    const paint = () => {
      wrap.innerHTML = "";
      const tbl = el("table", "stbl stbl--rows");
      const head = el("tr");
      f.columns.forEach((c) => head.appendChild(el("th", null, c.label)));
      head.appendChild(el("th", null, ""));
      tbl.appendChild(head);
      rows.forEach((r, i) => {
        const tr = el("tr");
        f.columns.forEach((c) => {
          const td = el("td");
          td.appendChild(cellInput(c, r[c.key], (v) => { r[c.key] = v; saveLater(f, jcopy(rows), 900); }));
          tr.appendChild(td);
        });
        const del = el("button", "linkish", "删除");
        del.type = "button";
        del.addEventListener("click", () => { rows.splice(i, 1); paint(); save(f, jcopy(rows)); });
        const td = el("td");
        td.appendChild(del);
        tr.appendChild(td);
        tbl.appendChild(tr);
      });
      wrap.appendChild(tbl);
      const add = el("button", "btn btn--ghost btn--mini", "＋ 添加一行");
      add.type = "button";
      add.addEventListener("click", () => {
        const blank = {};
        f.columns.forEach((c) => { blank[c.key] = c.kind === "select" ? c.choices[0].value : ""; });
        rows.push(blank);
        paint();
      });
      wrap.appendChild(add);
    };
    paint();
    return wrap;
  }
  const CONTROLS = { bool: boolCtl, int: numberCtl, float: numberCtl, text: textCtl, secret: secretCtl, longtext: longTextCtl, list: listCtl, select: selectCtl, table: tableCtl, rows: rowsCtl };

  function itemRow(f) {
    const row = el("div", `sitem sitem--${f.kind}`);
    const head = el("div", "sitem__head");
    head.appendChild(el("span", "sitem__label", f.label));
    row.appendChild(head);
    if (f.hint) row.appendChild(el("div", "sitem__hint", f.hint));
    row.appendChild(CONTROLS[f.kind](f));
    return row;
  }
  function fieldsOf(tab, box) {
    let section = null;
    let card = null;
    S.schema.fields.filter((f) => f.tab === tab).forEach((f) => {
      if (f.section && f.section !== section) {
        section = f.section;
        card = null;
        box.appendChild(el("h3", "qq-sec", section));
      }
      if (!card) { card = el("div", "qq-card"); box.appendChild(card); }
      card.appendChild(itemRow(f));
    });
  }

  /* ───────── 连接页 ───────── */
  const HELP = `在 NapCat 里打开「网络配置」，新建一个「WebSocket 服务器」（不是客户端）：
  host：127.0.0.1　port：3001（和这里的端口一样）　消息格式：array　token：自己设一个（和这里的 token 一样）
然后回到这里打开「启用 QQ 接入」。NapCat 要先登录你给她用的那个 QQ 号。`;

  function paintStatusCard() {
    const box = $("#qqStatusCard");
    if (!box) return;
    const s = S.status;
    box.innerHTML = "";
    const top = el("div", "qq-stat__top");
    const dot = el("i", `qqdot qqdot--big ${s ? DOT[s.state] || "is-off" : "is-off"}`);
    top.append(dot, el("b", null, s ? s.label : "后端没响应"));
    box.appendChild(top);
    if (!s) return;
    const lines = [];
    if (s.state === "connected") lines.push(`已连上 ${s.host}:${s.port}` + (s.nickname ? `，登录的号：${s.nickname}（${s.self_id}）` : ""));
    if (s.state === "error") lines.push(`${s.error}　${Math.ceil(s.retry_in)} 秒后重试`);
    if (s.state === "disabled") lines.push("在下面打开「启用 QQ 接入」才会去连 NapCat。");
    if (!s.owner_set) lines.push("还没填主人 QQ 号（「名单」页）：填之前不会处理任何私聊。");
    if (!s.groups) lines.push("群白名单是空的（「名单」页）：不会处理任何群消息。");
    const c = s.counters || {};
    lines.push(`这次开机以来：收到主人私聊 ${c.in_private} 条、白名单群消息 ${c.in_group} 条、忽略 ${c.ignored} 条；发出回复 ${c.replies} 次、选择沉默 ${c.silent} 次、出错 ${c.errors} 次。`);
    if (s.muted && s.muted.length) lines.push(`静默中：${s.muted.join("、")}`);
    if (s.last_error) lines.push(`最近一次出错：${s.last_error}`);
    lines.forEach((t) => box.appendChild(el("div", "qq-stat__line", t)));
    const btn = el("button", "btn btn--ghost btn--mini", "立即重连");
    btn.type = "button";
    btn.addEventListener("click", async () => { try { S.status = await api("/api/qq/reconnect", { method: "POST" }); paintStatusCard(); setTimeout(refreshStatus, 1200); } catch (e) { setState(e.message, true); } });
    box.appendChild(btn);
  }

  function connTab(box) {
    const card = el("div", "qq-card qq-stat");
    card.id = "qqStatusCard";
    box.appendChild(card);
    paintStatusCard();
    fieldsOf("conn", box);
    const help = el("details", "qq-help");
    help.appendChild(el("summary", null, "NapCat 那边怎么配"));
    help.appendChild(el("pre", null, HELP));
    box.appendChild(help);
    box.appendChild(el("p", "rm-hint", "QQ 只在桌面版开着的时候工作；关掉桌面版就断开。QQ 里的聊天、群友印象都存在 QQ 自己的数据库（data/qq/qq.db）里，对本地应用的数据只读，不会写进她的记忆、状态或聊天记录。"));
  }

  function personaTab(box) {
    fieldsOf("persona", box);
    const btn = el("button", "btn btn--ghost btn--mini", "预览：群聊里她看到的人设");
    btn.type = "button";
    const pre = el("pre", "qq-preview");
    pre.hidden = true;
    btn.addEventListener("click", async () => {
      if (!pre.hidden) { pre.hidden = true; return; }
      try { pre.textContent = (await api("/api/qq/preview/persona")).group; pre.hidden = false; } catch (e) { setState(e.message, true); }
    });
    box.append(btn, pre);
  }

  /* ───────── 群友印象 ───────── */
  async function imprTab(box) {
    box.appendChild(el("p", "rm-hint", "她对每个群友的印象：怎么称呼、聊过什么、对她好不好、熟悉程度（0~100，随来往慢慢增加）。回复某人时会带上这个人的印象；你可以改、可以删。这里只有印象，不是聊天内容。"));
    const holder = el("div");
    box.appendChild(holder);
    const paint = async () => {
      holder.innerHTML = "";
      let members = [];
      try { members = (await api("/api/qq/members")).members; } catch (e) { holder.appendChild(el("div", "rm-hint", e.message)); return; }
      if (!members.length) { holder.appendChild(el("div", "rm-hint", "还没有记下任何人。群里有人说话（或者在「模拟群聊」里试一试）之后会出现在这里。")); return; }
      const byChat = {};
      members.forEach((m) => { (byChat[m.chat] = byChat[m.chat] || []).push(m); });
      Object.keys(byChat).forEach((chat) => {
        const head = el("div", "qq-impr__head");
        head.appendChild(el("h3", "qq-sec", byChat[chat][0].chat_label));
        const clr = el("button", "linkish", "清空这个群的印象");
        clr.type = "button";
        clr.addEventListener("click", async () => { if (confirm(`清空「${byChat[chat][0].chat_label}」里所有人的印象？`)) { await api(`/api/qq/members?chat=${encodeURIComponent(chat)}`, { method: "DELETE" }); paint(); } });
        head.appendChild(clr);
        holder.appendChild(head);
        const grid = el("div", "qq-members");
        byChat[chat].forEach((m) => grid.appendChild(memberCard(m, paint)));
        holder.appendChild(grid);
      });
    };
    await paint();
  }
  function memberCard(m, repaint) {
    const c = el("div", "qq-card qq-member");
    const top = el("div", "qq-member__top");
    top.appendChild(el("b", null, m.nickname || "（没记下昵称）"));
    top.appendChild(el("span", "qq-member__id", m.user_id));
    top.appendChild(el("span", `qq-pill qq-pill--${m.label}`, m.label));
    const del = el("button", "linkish", "删除");
    del.type = "button";
    del.addEventListener("click", async () => { if (confirm(`删掉她对「${m.nickname || m.user_id}」的印象？`)) { await api(`/api/qq/members?chat=${encodeURIComponent(m.chat)}&user_id=${encodeURIComponent(m.user_id)}`, { method: "DELETE" }); repaint(); } });
    top.appendChild(del);
    c.appendChild(top);
    const bar = el("div", "qq-fam");
    const fill = el("i");
    fill.style.width = `${Math.round(m.familiarity)}%`;
    bar.appendChild(fill);
    c.appendChild(bar);
    const patch = async (body) => { try { await api("/api/qq/members", { method: "PATCH", body: JSON.stringify({ chat: m.chat, user_id: m.user_id, ...body }) }); setState("已保存"); } catch (e) { setState(e.message, true); } };
    const row = (label, node) => { const r = el("div", "qq-member__row"); r.append(el("label", null, label), node); c.appendChild(r); };
    const call = el("input", "input");
    call.maxLength = 12;
    call.value = m.call_name || "";
    call.placeholder = "她怎么称呼这个人";
    call.addEventListener("change", () => patch({ call_name: call.value.trim() }));
    row("称呼", call);
    const fam = el("input", "input sitem__num");
    fam.type = "number"; fam.min = 0; fam.max = 100; fam.step = 1;
    fam.value = Math.round(m.familiarity);
    fam.addEventListener("change", () => { fill.style.width = `${Math.max(0, Math.min(100, Number(fam.value)))}%`; patch({ familiarity: Number(fam.value) }); });
    row("熟悉度", fam);
    const imp = el("textarea", "textarea");
    imp.rows = 3;
    imp.maxLength = 120;
    imp.value = m.impression || "";
    imp.placeholder = "还没有印象（来往几次之后她会自己写）";
    imp.addEventListener("change", () => patch({ impression: imp.value.trim() }));
    row("印象", imp);
    c.appendChild(el("div", "qq-member__meta", `说过 ${m.msg_count} 句话 · 和她来往 ${m.interactions} 次 · 最后一次出现 ${m.last_seen ? new Date(m.last_seen * 1000).toLocaleString() : "—"}`));
    return c;
  }

  /* ───────── 模拟群聊 ───────── */
  const OUTCOME = {
    replied: "回复了", silent: "选择沉默（模型输出了「沉默」动作，或者什么都没说）", no_candidates: "没有消息过闸门（没掷过），所以没调用模型，也没回",
    muted_one_char: "静默中被 @：只回了一个字", muted_by_command: "主人说了「安静」口令：进入静默", proactive: "主动说了一句", send_failed: "发送失败",
    nothing: "没有要处理的消息",
  };
  function outcomeText(o) {
    if (!o) return "";
    if (OUTCOME[o]) return OUTCOME[o];
    if (o.startsWith("blocked:")) return `被拦住了：${o.slice(8)}`;
    if (o.startsWith("no:")) return `这次没有主动开口：${o.slice(3)}`;
    if (o.startsWith("error:")) return `出错：${o.slice(6)}`;
    return o;
  }

  async function simLoad() {
    S.sim = await api("/api/qq/sim");
  }
  async function simTab(box) {
    box.appendChild(el("p", "rm-hint", "假装自己是几个群友（可以有一个「主人」），在一个模拟群里说话，看她会不会回、怎么回。走的是和真群聊完全一样的代码（闸门、窗口、上限、静默、信任分级……），只是发送是假的，不连 QQ。会真的调用模型，消耗 token。"));
    try { await simLoad(); } catch (e) { box.appendChild(el("div", "rm-hint", e.message)); return; }
    const grid = el("div", "qq-sim");
    const left = el("div", "qq-sim__left");
    const right = el("div", "qq-sim__right");
    grid.append(left, right);
    box.appendChild(grid);
    paintSim(left, right);
    clearInterval(S.simPoll);
    S.simPoll = setInterval(async () => {
      if (ROOT.hidden || S.tab !== "sim" || S.simBusy || S.simOpts.instant) return;
      try { const before = S.sim.messages.length; await simLoad(); if (S.sim.messages.length !== before) paintSim(left, right); } catch (_) { /* 忽略 */ }
    }, 3000);
  }

  function paintSim(left, right) {
    left.innerHTML = "";
    right.innerHTML = "";
    const sim = S.sim;
    if (!S.simSel || !sim.members.find((m) => m.user_id === S.simSel)) S.simSel = (sim.members[0] || {}).user_id;

    // 群友
    const mem = el("div", "qq-card");
    mem.appendChild(el("div", "qq-card__title", "模拟群里的人（点一下选中，就是在以他的身份说话）"));
    const chips = el("div", "qq-chips");
    const saveMembers = async () => { await api("/api/qq/sim/members", { method: "PUT", body: JSON.stringify({ members: sim.members }) }); };
    sim.members.forEach((m, i) => {
      const chip = el("span", `qq-chip${m.user_id === S.simSel ? " is-on" : ""}${m.is_owner ? " is-owner" : ""}`);
      chip.appendChild(el("span", null, `${m.is_owner ? "★ " : ""}${m.name}`));
      chip.title = `QQ ${m.user_id}${m.is_owner ? "（主人）" : ""}`;
      chip.addEventListener("click", () => { S.simSel = m.user_id; paintSim(left, right); });
      const x = el("button", null, "×");
      x.type = "button";
      x.addEventListener("click", async (e) => { e.stopPropagation(); sim.members.splice(i, 1); await saveMembers(); paintSim(left, right); });
      chip.appendChild(x);
      chips.appendChild(chip);
    });
    mem.appendChild(chips);
    const add = el("div", "qq-add");
    const nm = el("input", "input"); nm.placeholder = "名字";
    const own = el("label", "qq-check"); const oc = document.createElement("input"); oc.type = "checkbox"; own.append(oc, document.createTextNode(" 他是主人"));
    const ab = el("button", "btn btn--ghost btn--mini", "＋ 添加");
    ab.type = "button";
    ab.addEventListener("click", async () => {
      if (!nm.value.trim()) return;
      sim.members.push({ user_id: String(30000 + Math.floor(Math.random() * 60000)), name: nm.value.trim(), is_owner: oc.checked });
      await saveMembers();
      paintSim(left, right);
    });
    add.append(nm, own, ab);
    mem.appendChild(add);
    left.appendChild(mem);

    // 消息
    const log = el("div", "qq-log");
    if (!sim.messages.length) log.appendChild(el("div", "qq-log__empty", "还没有消息。选一个人，说点什么吧。"));
    sim.messages.forEach((m) => {
      const b = el("div", `qq-msg${m.is_self ? " is-self" : ""}${m.is_owner ? " is-owner" : ""}`);
      const who = el("div", "qq-msg__who", `${m.is_self ? "Phal" : (m.is_owner ? "★ " : "") + m.name}`);
      b.appendChild(who);
      if (m.quote) b.appendChild(el("div", "qq-msg__quote", `回复：${m.quote}`));
      b.appendChild(el("div", "qq-msg__text", m.text));
      const rp = el("button", "linkish", "回复这条");
      rp.type = "button";
      rp.addEventListener("click", () => { S.replyTo = m.id; paintSim(left, right); });
      b.appendChild(rp);
      log.appendChild(b);
    });
    left.appendChild(log);
    setTimeout(() => { log.scrollTop = log.scrollHeight; }, 0);

    // 输入
    const comp = el("div", "qq-card");
    if (S.replyTo) {
      const rm = sim.messages.find((x) => x.id === S.replyTo);
      const chip = el("div", "qq-replyto", `回复：${rm ? rm.text.slice(0, 30) : ""}　`);
      const cx = el("button", "linkish", "取消");
      cx.type = "button";
      cx.addEventListener("click", () => { S.replyTo = null; paintSim(left, right); });
      chip.appendChild(cx);
      comp.appendChild(chip);
    }
    const line = el("div", "qq-compose");
    const ta = el("textarea", "textarea");
    ta.rows = 2;
    ta.placeholder = "说点什么（Enter 发送，Shift+Enter 换行）";
    const send = el("button", "btn", "发送");
    send.type = "button";
    line.append(ta, send);
    comp.appendChild(line);
    const opts = el("div", "qq-opts");
    const mk = (label, node) => { const w = el("label", "qq-opt"); w.append(document.createTextNode(label + " "), node); return w; };
    const atBox = document.createElement("input"); atBox.type = "checkbox";
    const imgBox = document.createElement("input"); imgBox.type = "checkbox";
    const wait = el("select", "input"); [["1", "马上处理（跳过 20~90 秒的等待）"], ["0", "按真实时间等待窗口"]].forEach(([v, l]) => { const o = el("option", null, l); o.value = v; wait.appendChild(o); });
    wait.value = S.simOpts.instant ? "1" : "0";
    wait.addEventListener("change", () => { S.simOpts.instant = wait.value === "1"; });
    const force = document.createElement("input"); force.type = "checkbox"; force.checked = S.simOpts.force;
    force.addEventListener("change", () => { S.simOpts.force = force.checked; });
    const stSel = el("select", "input");
    const o0 = el("option", null, `用她现在真实的状态（${sim.real.state_label}）`); o0.value = ""; stSel.appendChild(o0);
    sim.states.forEach((s) => { const o = el("option", null, `假装：${s.label}`); o.value = s.key; stSel.appendChild(o); });
    stSel.value = S.simOpts.state;
    stSel.addEventListener("change", () => { S.simOpts.state = stSel.value; });
    const busy = el("select", "input");
    [["", `按真实情况（${sim.real.busy ? "你刚在桌面聊过" : "没有"}）`], ["1", "假装：你刚在桌面里和她聊过"], ["0", "假装：没有"]].forEach(([v, l]) => { const o = el("option", null, l); o.value = v; busy.appendChild(o); });
    busy.value = S.simOpts.busy;
    busy.addEventListener("change", () => { S.simOpts.busy = busy.value; });
    opts.append(mk("@她", atBox), mk("带一张图片 / 表情", imgBox), mk("等待", wait), mk("跳过掷骰子", force), mk("状态", stSel), mk("忙碌", busy));
    comp.appendChild(opts);
    const tools = el("div", "qq-tools");
    const mkBtn = (label, fn) => { const b = el("button", "btn btn--ghost btn--mini", label); b.type = "button"; b.addEventListener("click", fn); return b; };
    tools.append(
      mkBtn("让她主动说一句", async () => { S.simBusy = true; try { const r = await api("/api/qq/sim/proactive", { method: "POST", body: JSON.stringify({ user_id: "0", name: "", state: S.simOpts.state || null, busy: S.simOpts.busy === "" ? null : S.simOpts.busy === "1" }) }); sim.messages = r.messages; sim.trace = r.trace; paintSim(left, right); } catch (e) { setState(e.message, true); } S.simBusy = false; }),
      mkBtn("解除静默", async () => { await api("/api/qq/sim/unmute", { method: "POST" }); await simLoad(); paintSim(left, right); }),
      mkBtn("重置模拟群", async () => { if (!confirm("清空模拟群的消息和她对模拟群友的印象？")) return; await api("/api/qq/sim/reset", { method: "POST" }); S.replyTo = null; await simLoad(); paintSim(left, right); }),
    );
    if (sim.muted) tools.appendChild(el("span", "qq-pill qq-pill--bad", "静默中"));
    comp.appendChild(tools);
    left.appendChild(comp);

    const doSend = async () => {
      const m = sim.members.find((x) => x.user_id === S.simSel);
      if (!m || (!ta.value.trim() && !imgBox.checked)) return;
      send.disabled = true;
      S.simBusy = true;
      send.textContent = "她在想…";
      try {
        const r = await api("/api/qq/sim/send", { method: "POST", body: JSON.stringify({
          user_id: m.user_id, name: m.name, text: ta.value, is_owner: !!m.is_owner, at_her: atBox.checked, reply_to: S.replyTo, visual: imgBox.checked,
          instant: S.simOpts.instant, force: S.simOpts.force, state: S.simOpts.state || null, busy: S.simOpts.busy === "" ? null : S.simOpts.busy === "1" }) });
        sim.messages = r.messages;
        sim.muted = r.muted;
        sim.trace = r.trace;
        S.replyTo = null;
      } catch (e) { setState(e.message, true); }
      S.simBusy = false;
      paintSim(left, right);
    };
    send.addEventListener("click", doSend);
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); doSend(); } });
    setTimeout(() => ta.focus(), 0);

    paintTrace(right, sim);
  }

  const pct = (x) => `${(x * 100).toFixed(1)}%`;
  function paintTrace(box, sim) {
    const t = sim.trace;
    box.appendChild(el("div", "qq-card__title", "她的判断过程"));
    if (!t) { box.appendChild(el("div", "rm-hint", "发一句话看看。这里会显示：她当时的状态、每条消息的回复概率是怎么算出来的、掷骰子的结果、她看到了什么、回了什么。")); return; }
    const card = el("div", "qq-card");
    const kv = (k, v) => { const r = el("div", "qq-kv"); r.append(el("span", null, k), el("b", null, v)); card.appendChild(r); };
    kv("结果", outcomeText(t.outcome));
    if (t.state) kv("她的状态", `${t.state_label || t.state}${t.mood ? `　心情：${t.mood}` : ""}${t.busy ? "　（正忙着和你聊天）" : ""}`);
    if (t.sleeping) kv("睡觉时间", "是（夜里不发言）");
    if (t.muted) kv("静默", "是");
    if (t.caps) kv("发言上限", `10 分钟内 ${t.caps.speaks_10min} 次、1 小时内 ${t.caps.speaks_hour} 次${t.caps.ok ? "" : `　→ ${t.caps.why}`}`);
    if (t.detail !== undefined) kv("给她的版本", t.detail ? "详细版（回的是主人）" : "模糊版（回的有别人）");
    if (t.forced_through) kv("被拦住", `${t.forced_through}，但主人 @ 她的话照回`);
    if (t.counted !== undefined) kv("计入频率", t.counted ? "是（占 10 分钟 / 1 小时的上限，也会触发冷却）" : "否（只回了主人 @ 她的话）");
    if (t.short) kv("回复长度", "状态不方便：回也只回一两个字");
    if (t.ooc) kv("出戏", "主人发了【OOC】：直通，不切分、不去感叹号");
    box.appendChild(card);

    if (t.messages && t.messages.length) {
      const tbl = el("table", "stbl qq-tbl");
      const head = el("tr");
      ["消息", "档位", "基础", "状态", "忙碌", "心情", "冷却", "概率", "骰子", ""].forEach((h) => head.appendChild(el("th", null, h)));
      tbl.appendChild(head);
      t.messages.forEach((m) => {
        const tr = el("tr");
        tr.appendChild(el("td", "qq-tbl__text", `${m.name}：${m.text}`));
        const f = m.factors;
        [m.cls_label, m.base, f.state, f.busy, f.mood, f.cooldown.toFixed(2)].forEach((v) => tr.appendChild(el("td", null, typeof v === "number" ? String(+v.toFixed(2)) : v)));
        tr.appendChild(el("td", null, pct(m.p)));
        tr.appendChild(el("td", null, m.roll.toFixed(3)));
        tr.appendChild(el("td", m.passed ? "qq-ok" : "qq-no", m.passed ? (t.targets && t.targets.includes(m.id) ? "✓ 要回" : "✓ 过了") : "✗"));
        tbl.appendChild(tr);
      });
      box.appendChild(tbl);
      box.appendChild(el("div", "sitem__count", "概率 = 基础 × 状态 × 忙碌 × 心情 × 冷却；骰子 < 概率才过闸门。"));
    }
    if (t.parts && t.parts.length) {
      const p = el("div", "qq-card");
      p.appendChild(el("div", "qq-card__title", "她发出去的（假的，没有真的发）"));
      t.parts.forEach((x) => p.appendChild(el("div", "qq-part", `${x.reply_to ? "↩ 引用　" : ""}${x.text}　（隔 ${x.delay} 秒）`)));
      box.appendChild(p);
    }
    if (t.chars_in) box.appendChild(el("div", "sitem__count", `这次调用：上下文约 ${t.chars_in} 字，回复 ${t.chars_out} 字${t.retries ? `，禁词命中重新生成了 ${t.retries} 次` : ""}。`));
    if (t.send_error) box.appendChild(el("div", "qq-no", `发送出错：${t.send_error}`));
    if (t.prompt) {
      const d = el("details", "qq-help");
      d.appendChild(el("summary", null, "她看到的完整上下文（检查有没有不该看到的东西）"));
      t.prompt.forEach((m) => { d.appendChild(el("div", "qq-role", m.role)); d.appendChild(el("pre", null, m.content)); });
      if (t.raw) { d.appendChild(el("div", "qq-role", "模型的原始输出")); d.appendChild(el("pre", null, t.raw)); }
      box.appendChild(d);
    }
  }

  /* ───────── 页面骨架 ───────── */
  function renderNav() {
    const nav = $("#qqNav");
    nav.innerHTML = "";
    S.schema.tabs.forEach((t) => {
      const b = el("button", `setnav__a${t.key === S.tab ? " is-on" : ""}`, t.label);
      b.type = "button";
      b.addEventListener("click", () => { S.tab = t.key; renderNav(); renderBody(); });
      nav.appendChild(b);
    });
  }
  async function renderBody() {
    const body = $("#qqBody");
    body.innerHTML = "";
    clearInterval(S.simPoll);
    const box = el("div", "qq-wrap");
    body.appendChild(box);
    const title = S.schema.tabs.find((t) => t.key === S.tab);
    box.appendChild(el("h2", "qq-title", title ? title.label : ""));
    if (S.tab === "conn") connTab(box);
    else if (S.tab === "persona") personaTab(box);
    else if (S.tab === "impr") await imprTab(box);
    else if (S.tab === "sim") await simTab(box);
    else fieldsOf(S.tab, box);
  }

  async function open() {
    ROOT.hidden = false;
    try {
      await loadConfig();
      await refreshStatus();
      renderNav();
      await renderBody();
    } catch (e) {
      setState(`读取失败：${e.message}`, true);
    }
  }
  function close() { ROOT.hidden = true; clearInterval(S.simPoll); }

  $("#qqBack").addEventListener("click", close);
  $("#btnQQ").addEventListener("click", open);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !ROOT.hidden && !(e.target.closest && e.target.closest("input, textarea, select"))) close(); });
  refreshStatus();
  setInterval(() => { if (document.visibilityState === "visible") refreshStatus(); }, 20000);

  window.PhalQQ = { open, close };
})();
