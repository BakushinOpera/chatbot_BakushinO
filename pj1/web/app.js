/* ==========================================================================
   纯原生 JS：流式对话、消息引用、人设设置、历史检索、长期记忆
   ========================================================================== */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const WEEK = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];
  const ROLE_LABEL = { user: "你", assistant: "AI", system: "系统" };

  const state = {
    settings: null,
    now: null,
    clockOffset: 0,
    messages: [],
    quote: null,
    selected: null,
    lastDay: null,
    sending: false,
    defaultPersona: "",
  };

  const el = {
    stream: $("#stream"),
    input: $("#input"),
    send: $("#send"),
    hint: $("#hint"),
    clock: $("#clock"),
    personaName: $("#personaName"),
    quoteBar: $("#quoteBar"),
    quoteWho: $("#quoteWho"),
    quoteText: $("#quoteText"),
    quoteClose: $("#quoteClose"),
    drawer: $("#drawer"),
    scrim: $("#scrim"),
    rail: $("#rail"),
    railScrim: $("#railScrim"),
    toast: $("#toast"),
    statsLine: $("#statsLine"),
    searchHits: $("#searchHits"),
    memoryList: $("#memoryList"),
  };

  /* ───────────────────────── 工具 ───────────────────────── */

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { detail: text }; }
    if (!res.ok) throw new Error((data && data.detail) || `请求失败 (${res.status})`);
    return data;
  }

  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.toast.classList.remove("is-on"), 2200);
  }

  /* ───────────────────────── 确认弹窗 ───────────────────────── */

  function confirmDialog({ title, text, okText = "确定", cancelText = "取消" }) {
    return new Promise((resolve) => {
      const scrim = $("#modalScrim");
      const okBtn = $("#modalOk");
      const cancelBtn = $("#modalCancel");
      $("#modalTitle").textContent = title;
      $("#modalText").textContent = text;
      okBtn.textContent = okText;
      cancelBtn.textContent = cancelText;

      const close = (value) => {
        scrim.classList.remove("is-open");
        scrim.setAttribute("aria-hidden", "true");
        okBtn.removeEventListener("click", onOk);
        cancelBtn.removeEventListener("click", onCancel);
        scrim.removeEventListener("click", onScrim);
        document.removeEventListener("keydown", onKey);
        resolve(value);
      };
      const onOk = () => close(true);
      const onCancel = () => close(false);
      const onScrim = (e) => { if (e.target === scrim) close(false); };
      const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(false); } };

      okBtn.addEventListener("click", onOk);
      cancelBtn.addEventListener("click", onCancel);
      scrim.addEventListener("click", onScrim);
      document.addEventListener("keydown", onKey, true);

      scrim.classList.add("is-open");
      scrim.setAttribute("aria-hidden", "false");
      okBtn.focus();
    });
  }

  const timeOf = (stamp) => (stamp || "").slice(11, 16);
  const dayOf = (stamp) => (stamp || "").slice(0, 10);

  function dayLabel(key) {
    const today = state.now ? state.now.date : new Date().toISOString().slice(0, 10);
    const d = new Date(`${key}T00:00:00`);
    const t = new Date(`${today}T00:00:00`);
    const diff = Math.round((t - d) / 86400000);
    const wd = WEEK[d.getDay()];
    if (diff === 0) return `今天 · ${wd}`;
    if (diff === 1) return `昨天 · ${wd}`;
    if (diff === 2) return `前天 · ${wd}`;
    return `${d.getMonth() + 1} 月 ${d.getDate()} 日 · ${wd}`;
  }

  /* ───────────────────────── 顶部时钟 ───────────────────────── */

  function tickClock() {
    if (!state.now) return;
    const d = new Date(Date.now() + state.clockOffset);
    const pad = (n) => String(n).padStart(2, "0");
    const wd = WEEK[d.getDay()];
    el.clock.textContent = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${wd} · ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /* ───────────────────────── 渲染消息 ───────────────────────── */

  function ensureDaySep(key) {
    if (state.lastDay === key) return;
    state.lastDay = key;
    const sep = document.createElement("div");
    sep.className = "daysep";
    sep.textContent = dayLabel(key);
    el.stream.appendChild(sep);
  }

  function parseMeta(raw) {
    if (!raw) return null;
    if (typeof raw === "object") return raw;
    try { return JSON.parse(raw); } catch (_) { return null; }
  }

  const SOURCE_LABEL = { reading: "来自阅读中", read_done: "读完之后", create: "来自创作", wish: "想看书了", create_ask: "想写东西了", goodnight: "睡前", doc_edit: "看了你的修改", weather: "天气", meal: "饭点", event: "刚发生的事", money: "刚买的东西", letter: "写了封信" };

  /** Phal 主动发来的消息上方的小标记；能定位到书的，点一下去书页那个位置。 */
  function sourceTag(source, meta) {
    const text = SOURCE_LABEL[source] || "主动发来";
    const hasBook = meta && meta.book_id != null && meta.chapter != null;
    const hasDoc = meta && meta.doc_id != null;
    const tag = document.createElement(hasBook || hasDoc ? "a" : "span");
    tag.className = "msg__src";
    tag.textContent = meta && meta.title ? `${text} · 《${meta.title}》` : text;
    if (hasDoc) {
      tag.href = "#";
      tag.title = "打开这份文档";
      tag.addEventListener("click", (e) => { e.preventDefault(); document.dispatchEvent(new CustomEvent("phal:open-doc", { detail: { id: meta.doc_id } })); });
    } else if (hasBook) {
      tag.href = `/reader?book=${meta.book_id}&ch=${meta.chapter}&off=${meta.offset || 0}`;
      tag.title = "去书页上看这一处";
    }
    return tag;
  }

  function renderQuoteInto(bubble, quoteText, quoteRole, meta) {
    const box = document.createElement("div");
    box.className = "bubble__quote";
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = meta && meta.title ? `《${meta.title}》：` : `${ROLE_LABEL[quoteRole] || "引用"}：`;
    box.appendChild(who);
    box.appendChild(document.createTextNode(quoteText.length > 220 ? quoteText.slice(0, 220) + "…" : quoteText));
    bubble.appendChild(box);
  }

  function buildMessageEl(msg) {
    const wrap = document.createElement("div");
    wrap.className = `msg msg--${msg.role === "user" ? "user" : "ai"}`;
    wrap.dataset.role = msg.role;
    if (msg.id != null) wrap.dataset.id = String(msg.id);

    const meta = parseMeta(msg.meta);
    if (msg.source) wrap.appendChild(sourceTag(msg.source, meta));

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (msg.quote_text) renderQuoteInto(bubble, msg.quote_text, msg.quote_role, meta);

    const text = document.createElement("div");
    text.className = "bubble__text";
    text.textContent = msg.content || "";
    bubble.appendChild(text);
    wrap.appendChild(bubble);

    if (!msg.pending) {
      const meta = document.createElement("div");
      meta.className = "msg__meta";
      meta.textContent = timeOf(msg.created_at);
      wrap.appendChild(meta);
    }

    bubble.addEventListener("click", (e) => {
      if (e.target.closest(".actions")) return;
      toggleSelect(wrap, msg);
    });
    return wrap;
  }

  function appendMessage(msg) {
    const key = dayOf(msg.created_at);
    if (key) ensureDaySep(key);
    const node = buildMessageEl(msg);
    el.stream.appendChild(node);
    if (msg.role === "assistant") markBannedWords(node, msg.content || "");
    return node;
  }

  function renderAll() {
    el.stream.innerHTML = "";
    state.lastDay = null;
    state.selected = null;
    if (!state.messages.length) {
      const blank = document.createElement("div");
      blank.className = "blank";
      const b = document.createElement("b");
      b.textContent = state.settings ? state.settings.persona_name : "AI";
      blank.appendChild(b);
      blank.appendChild(document.createTextNode("说第一句话吧。"));
      el.stream.appendChild(blank);
      return;
    }
    state.messages.forEach(appendMessage);
    scrollToBottom(true);
  }

  function scrollToBottom(force = false) {
    const near = window.innerHeight + window.scrollY >= document.body.scrollHeight - 220;
    if (force || near) window.scrollTo({ top: document.body.scrollHeight, behavior: force ? "auto" : "smooth" });
  }

  /* ───────────────────────── 引用 ───────────────────────── */

  function toggleSelect(node, msg) {
    if (state.selected === msg.id && node.querySelector(".actions")) {
      clearSelection();
      return;
    }
    clearSelection();
    state.selected = msg.id;
    node.classList.add("is-selected");

    const bar = document.createElement("div");
    bar.className = "actions";
    const q = document.createElement("button");
    q.type = "button";
    q.textContent = "引用";
    q.addEventListener("click", (e) => { e.stopPropagation(); setQuote(msg); clearSelection(); });
    const c = document.createElement("button");
    c.type = "button";
    c.textContent = "复制";
    c.addEventListener("click", (e) => {
      e.stopPropagation();
      navigator.clipboard?.writeText(msg.content).then(() => toast("已复制"), () => toast("复制失败"));
      clearSelection();
    });
    bar.append(q, c);

    if (msg.id != null) {
      const d = document.createElement("button");
      d.type = "button";
      d.className = "danger";
      d.textContent = "删除";
      d.addEventListener("click", (e) => {
        e.stopPropagation();
        clearSelection();
        deleteMessage(msg, node);
      });
      bar.appendChild(d);
    }

    // AI 的回复可以直接改：改完同步到本地库，Phal 会把你改的当作标准口吻去学；我自己的消息：她还没回之前可以改（她按改后的回）
    const unanswered = msg.role === "user" && msg.id != null && !state.messages.some((m) => m.role === "assistant" && m.id > msg.id);
    if ((msg.role === "assistant" || unanswered) && msg.id != null) {
      const ed = document.createElement("button");
      ed.type = "button";
      ed.textContent = "编辑";
      ed.addEventListener("click", (e) => {
        e.stopPropagation();
        clearSelection();
        editMessage(msg, node);
      });
      bar.insertBefore(ed, bar.querySelector(".danger"));
    }

    // 最后一条 AI 回复可以「重说」（删掉重生成）
    if (msg.role === "assistant" && msg.id != null && !node.nextElementSibling) {
      const r = document.createElement("button");
      r.type = "button";
      r.textContent = "重说";
      r.addEventListener("click", (e) => {
        e.stopPropagation();
        clearSelection();
        regenerate();
      });
      bar.appendChild(r);
    }
    node.appendChild(bar);
  }

  /* ───────────────────────── 编辑 AI 的回复 ───────────────────────── */

  function editMessage(msg, node) {
    const textEl = node.querySelector(".bubble__text");
    if (!textEl || node.classList.contains("is-editing")) return;
    node.classList.add("is-editing");

    const ta = document.createElement("textarea");
    ta.className = "bubble__edit";
    ta.value = msg.content || "";
    const bar = document.createElement("div");
    bar.className = "bubble__editbar";
    const hint = document.createElement("span");
    hint.className = "bubble__edithint";
    hint.textContent = "改完它会学你的口吻";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "is-primary";
    save.textContent = "保存";
    bar.append(hint, cancel, save);

    const fit = () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 360)}px`; };
    const close = () => {
      ta.remove();
      bar.remove();
      textEl.hidden = false;
      node.classList.remove("is-editing");
    };
    cancel.addEventListener("click", (e) => { e.stopPropagation(); close(); });
    ta.addEventListener("input", fit);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save.click(); }
    });
    save.addEventListener("click", async (e) => {
      e.stopPropagation();
      const content = ta.value.trim();
      if (!content) { toast("内容不能为空"); return; }
      if (content === (msg.content || "").trim()) { close(); return; }
      save.disabled = true;
      try {
        const data = await api(`/api/messages/${msg.id}`, { method: "PATCH", body: JSON.stringify({ content }) });
        msg.content = data.message.content;
        textEl.textContent = msg.content;
        const known = state.messages.find((m) => m.id === msg.id);
        if (known) known.content = msg.content;
        markBannedWords(node, msg.content);
        close();
        toast("已改好，它会学你这样说话");
      } catch (err) {
        save.disabled = false;
        toast(`保存失败：${err.message}`);
      }
    });

    textEl.hidden = true;
    textEl.after(ta, bar);
    fit();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }

  /* ───────────────────────── 删除 ───────────────────────── */

  function pruneSeparators() {
    const kids = Array.from(el.stream.children);
    kids.forEach((node, i) => {
      if (!node.classList.contains("daysep")) return;
      let hasMessage = false;
      for (let j = i + 1; j < kids.length; j++) {
        if (kids[j].classList.contains("daysep")) break;
        if (kids[j].classList.contains("msg")) { hasMessage = true; break; }
      }
      if (!hasMessage) node.remove();
    });
  }

  async function deleteMessage(msg, node) {
    const ok = await confirmDialog({
      title: "删除这条消息？",
      text: "网页上的这条会消失，本地数据库里对应的记录（聊天窗口 + 历史归档）也会一起删掉，无法恢复。",
      okText: "删除",
    });
    if (!ok) return;
    try {
      const data = await api(`/api/messages/${msg.id}`, { method: "DELETE" });
      node.remove();
      state.messages = state.messages.filter((m) => m.id !== msg.id);
      pruneSeparators();
      renderStats(data.stats);
      toast("已删除");
      if (!state.messages.length) renderAll();
    } catch (err) {
      toast(`删除失败：${err.message}`);
    }
  }

  async function reloadMessages() {
    try {
      const data = await api("/api/messages");
      state.messages = data.messages || [];
      renderAll();
    } catch (_) { /* 忽略 */ }
  }

  async function clearHistory(scope) {
    const isAll = scope === "all";
    const ok = await confirmDialog({
      title: isAll ? "清空全部聊天记录？" : "删除当前这段对话？",
      text: isAll
        ? "当前聊天窗口和历史归档里的所有消息都会被永久删除，无法恢复。\n长期记忆不会动（要清的话，另外点「清空长期记忆」）。"
        : "这段对话在聊天窗口和历史归档里的记录都会被永久删除，无法恢复。",
      okText: "删除",
    });
    if (!ok) return;
    try {
      const data = await api("/api/history/clear", {
        method: "POST",
        body: JSON.stringify({ scope }),
      });
      state.messages = [];
      renderAll();
      renderStats(data.stats);
      await loadMemories();
      toast("已删除");
    } catch (err) {
      toast(`删除失败：${err.message}`);
    }
  }

  /* ───────────────────────── 用词列表 ───────────────────────── */

  function wordList(kind) {
    const s = state.settings || {};
    if (kind === "banned") {
      if (!Array.isArray(s.banned_words)) s.banned_words = [];
      return s.banned_words;
    }
    if (!Array.isArray(s.common_words)) s.common_words = [];
    return s.common_words;
  }

  function bannedHits(text) {
    const list = wordList("banned");
    if (!list.length) return [];
    const lower = (text || "").toLowerCase();
    return list.filter((w) => w && lower.includes(String(w).toLowerCase()));
  }

  /** 在踩了禁词的消息下面挂一行提示 + 「重说」。 */
  function markBannedWords(node, text) {
    const old = node.querySelector(".msg__flag");
    if (old) old.remove();
    node.classList.remove("msg--flagged");

    const hits = bannedHits(text);
    if (!hits.length) return;
    node.classList.add("msg--flagged");

    const flag = document.createElement("div");
    flag.className = "msg__flag";
    const label = document.createElement("span");
    label.textContent = `⚠ 踩到禁词：${hits.join("、")}`;
    const redo = document.createElement("button");
    redo.type = "button";
    redo.textContent = "重说";
    redo.addEventListener("click", (e) => { e.stopPropagation(); regenerate(); });
    flag.append(label, redo);
    node.appendChild(flag);
  }

  /** 禁词列表变了之后，把已有消息的标记重算一遍。 */
  function refreshBannedFlags() {
    el.stream.querySelectorAll(".msg--ai").forEach((node) => {
      const text = node.querySelector(".bubble__text");
      markBannedWords(node, text ? text.textContent : "");
    });
  }

  /* ───────────────────────── 日历 ───────────────────────── */

  const cal = { offset: 0, data: null, fresh: new Set(), monthOpen: false, ym: null, month: null };   // fresh：刚盖上章的任务，播一次盖章动画

  function openRail() {
    el.rail.classList.add("is-open");
    if (el.railScrim) el.railScrim.classList.add("is-open");
    el.rail.setAttribute("aria-hidden", "false");
  }

  function closeRail() {
    el.rail.classList.remove("is-open");
    if (el.railScrim) el.railScrim.classList.remove("is-open");
    el.rail.setAttribute("aria-hidden", "true");
  }

  function toggleRail() {
    if (el.rail.classList.contains("is-open")) closeRail(); else openRail();
  }

  async function loadCalendar(offset) {
    const next = Number.isFinite(offset) ? offset : cal.offset;
    try {
      const data = await api(`/api/calendar?offset=${next}`);
      cal.offset = next;
      cal.data = data;
      renderCalendar();
      if (data.mood_missing && data.mood_missing.length) fillMoods(data.mood_missing);
    } catch (err) {
      console.error("calendar", err);
    }
  }

  async function fillMoods(days) {
    try {
      const res = await api("/api/calendar/moods", {
        method: "POST",
        body: JSON.stringify({ days }),
      });
      if (!cal.data || !res.moods) return;
      cal.data.days.forEach((d) => { if (res.moods[d.day]) d.mood = res.moods[d.day]; });
      renderCalendar();
    } catch (_) {
      /* 心情生成失败不影响日历本身 */
    }
  }

  const STAMP_TEXT = { done: "完成", skipped: "不干" };

  function dayEl(day) {
    const wrap = document.createElement("div");
    wrap.className = "day"
      + (day.is_today ? " is-today" : "")
      + (day.is_future ? " is-future" : "")
      + (day.is_rest ? " is-rest" : "");

    const top = document.createElement("div");
    top.className = "day__top";
    const label = document.createElement("span");
    label.className = "day__label";
    label.textContent = day.label;
    if (day.is_rest) {
      const rest = document.createElement("span");
      rest.className = "day__rest";
      rest.textContent = "休息日";
      label.appendChild(rest);
    }
    (day.festivals || []).forEach((f) => {
      const tag = document.createElement("span");
      tag.className = "day__fest";
      tag.textContent = f.name;
      tag.title = f.note ? `${f.name}：${f.note}` : f.name;
      label.appendChild(tag);
    });
    const date = document.createElement("span");
    date.className = "day__date";
    date.textContent = `${Number(day.day.slice(5, 7))}/${Number(day.day.slice(8, 10))} ${day.weekday.replace("星期", "周")}`;
    top.append(label, date);
    wrap.appendChild(top);

    const mood = document.createElement("div");
    mood.className = "day__mood";
    if (day.mood) {
      const word = document.createElement("span");
      word.className = "mood__word";
      word.textContent = day.mood.mood;
      mood.appendChild(word);
      if (day.mood.summary) {
        const tag = document.createElement("span");
        tag.className = "mood__tag";
        tag.textContent = "晚间总结";
        word.appendChild(tag);
      } else if (day.mood.live) {
        const tag = document.createElement("span");
        tag.className = "mood__tag is-live";
        tag.textContent = "此刻";
        word.appendChild(tag);
      }
      if (day.mood.line) {
        const line = document.createElement("span");
        line.className = "mood__line";
        line.textContent = day.mood.live ? day.mood.line : `「${day.mood.line}」`;
        mood.appendChild(line);
      }
      if (day.mood.recap) {
        const recap = document.createElement("span");
        recap.className = "mood__recap";
        recap.textContent = day.mood.recap;
        recap.hidden = true;
        mood.classList.add("has-recap");
        mood.title = "点一下看她这一天的小结";
        mood.addEventListener("click", () => { recap.hidden = !recap.hidden; });
        mood.appendChild(recap);
      }
    } else {
      const none = document.createElement("span");
      none.className = "mood--none";
      none.textContent = day.is_future ? "还没到" : "……";
      mood.appendChild(none);
    }
    wrap.appendChild(mood);

    const tasks = document.createElement("div");
    tasks.className = "day__tasks";
    (day.tasks || []).forEach((t) => tasks.appendChild(taskEl(t)));
    tasks.appendChild(addFormEl(day));
    wrap.appendChild(tasks);
    return wrap;
  }

  function taskEl(t) {
    const stamped = t.status === "done" || t.status === "skipped";
    const row = document.createElement("div");
    row.className = "task"
      + (t.status === "done" ? " is-done" : t.status === "skipped" ? " is-skipped" : "")
      + (t.optional && !stamped ? " is-optional" : "")
      + (t.priority === "main" ? " is-main" : "");

    const line = document.createElement("div");
    line.className = "task__row";

    const dot = document.createElement("button");
    dot.type = "button";
    dot.className = "task__dot";
    dot.title = t.status === "done" ? "改回没做" : "标记完成";
    dot.addEventListener("click", () => taskPatch(t.id, { status: t.status === "done" ? "open" : "done" }));

    const title = document.createElement("button");
    title.type = "button";
    title.className = "task__title";
    title.textContent = t.title;
    title.title = t.optional ? "休息日，这件事可选" : `拖满 ${t.remind_after_days} 天提醒`;
    title.addEventListener("click", () => toggleOps(row));

    line.append(dot, title);

    // 小标签：休息日「可选」 / 从更早拖过来的原日期 / 已拖几个工作日
    const tags = document.createElement("span");
    tags.className = "task__tags";
    if (t.optional && !stamped) {
      const opt = document.createElement("span");
      opt.className = "task__tag task__tag--opt";
      opt.textContent = "可选";
      tags.appendChild(opt);
    }
    if (t.priority === "main") {
      const mainTag = document.createElement("span");
      mainTag.className = "task__tag task__tag--main";
      mainTag.textContent = "主要";
      mainTag.title = "主要任务：周末也照常拖延";
      tags.appendChild(mainTag);
    }
    if (t.repeat_every > 0) {
      const rep = document.createElement("span");
      rep.className = "task__tag task__tag--rep";
      rep.textContent = t.repeat_every === 1 ? "↻ 每天" : `↻ 每${t.repeat_every}天`;
      rep.title = "重复任务：未来的日期会自动加上；落在休息日的那天可选";
      tags.appendChild(rep);
    }
    if (t.carried) {
      const from = document.createElement("span");
      from.className = "task__tag";
      from.textContent = `${Number(t.day.slice(5, 7))}/${Number(t.day.slice(8, 10))}`;
      from.title = `从 ${t.day} 拖过来的`;
      tags.appendChild(from);
    }
    if (!stamped && t.pending_days >= 1) {
      const late = document.createElement("span");
      late.className = "task__late";
      late.textContent = String(t.pending_days);
      late.title = `已拖延 ${t.pending_days} 天（只算工作日）`;
      late.setAttribute("aria-label", late.title);
      tags.insertBefore(late, tags.firstChild);
    }
    if (tags.childElementCount) line.appendChild(tags);

    const more = document.createElement("button");
    more.type = "button";
    more.className = "task__more";
    more.textContent = "⋯";
    more.title = "更多";
    more.addEventListener("click", () => toggleOps(row));
    line.appendChild(more);
    row.appendChild(line);

    // 盖章：完成 = 绿章，不干 = 红章。刚盖上的那一次会有「啪」的一下
    if (stamped) {
      const tone = t.status === "done" ? "green" : "red";
      const stamp = document.createElement("span");
      stamp.className = `stamp stamp--${tone}`;
      stamp.textContent = STAMP_TEXT[t.status];
      stamp.setAttribute("aria-label", `已盖章：${STAMP_TEXT[t.status]}`);
      if (cal.fresh.has(t.id)) {
        stamp.classList.add("is-new");
        row.classList.add("is-thump");
        cal.fresh.delete(t.id);
      }
      row.appendChild(stamp);
    }

    const ops = document.createElement("div");
    ops.className = "task__ops";
    ops.hidden = true;
    [
      ["完成", { status: "done" }, "ok"],
      ["不干", { status: "skipped" }, "no"],
      ["编辑", "edit", ""],
      ...(t.carried && t.status === "open" ? [["取消拖延", "cancel-delay", ""]] : []),
      ["改回没做", { status: "open" }, ""],
      ...(t.repeat_every > 0 ? [["停止重复", { repeat_every: 0 }, ""]] : []),
      ["删除", null, ""],
      ...(t.repeat_every > 0 ? [["删除此后全部", "series", ""]] : []),
    ].forEach(([text, patch, tone]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = text;
      if (tone) b.className = `op--${tone}`;
      b.addEventListener("click", () => {
        if (patch === "edit") openTaskEditor(t, row);
        else if (patch === "series") taskDelete(t.id, true);
        else if (patch === "cancel-delay") taskCancelDelay(t.id);
        else if (patch) taskPatch(t.id, patch);
        else taskDelete(t.id, false);
      });
      ops.appendChild(b);
    });
    row.appendChild(ops);
    return row;
  }

  /** 改一条已经设好的任务：内容、日期、提醒天数、重复间隔。 */
  function openTaskEditor(t, row) {
    if (row.querySelector(".task__edit")) return;
    const ops = row.querySelector(".task__ops");
    if (ops) ops.hidden = true;

    const form = document.createElement("div");
    form.className = "day__form task__edit";

    const title = document.createElement("input");
    title.className = "input input--tiny";
    title.value = t.title;
    title.placeholder = "要做的事…";

    const mkNum = (value, min, max, placeholder) => {
      const n = document.createElement("input");
      n.type = "number";
      n.className = "num--tiny";
      n.value = value;
      n.min = String(min);
      n.max = String(max);
      if (placeholder) n.placeholder = placeholder;
      return n;
    };
    const span = (text) => { const x = document.createElement("span"); x.textContent = text; return x; };

    const dayIn = document.createElement("input");
    dayIn.type = "date";
    dayIn.className = "input input--tiny task__date";
    dayIn.value = t.day;

    const remind = mkNum(t.remind_after_days, 0, 365);
    const repeat = mkNum(t.repeat_every > 0 ? t.repeat_every : "", 1, 365, "—");

    const r1 = document.createElement("div");
    r1.className = "day__form-row";
    r1.append(span("日期"), dayIn);
    const r2 = document.createElement("div");
    r2.className = "day__form-row";
    r2.append(span("拖满"), remind, span("天提醒　每"), repeat, span("天重复"));

    const r3 = document.createElement("div");
    r3.className = "day__form-row";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn--ghost btn--mini";
    cancel.textContent = "取消";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "btn btn--mini";
    save.textContent = "保存";
    const priSel = document.createElement("select");
    priSel.className = "input input--tiny task__pri";
    [["minor", "次要（周末可选）"], ["main", "主要（周末也拖延）"]].forEach(([v, text]) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = text;
      priSel.appendChild(o);
    });
    priSel.value = t.priority === "main" ? "main" : "minor";
    const r4 = document.createElement("div");
    r4.className = "day__form-row";
    r4.append(span("主次"), priSel);
    r3.append(cancel, save);
    const autoBox = document.createElement("input");
    autoBox.type = "checkbox";
    autoBox.checked = !!t.auto_adjust;
    const r5 = document.createElement("label");
    r5.className = "day__form-row";
    r5.title = "比如每两天一次的锻炼，拖了一天才做完，下一次就从做完那天起再隔两天";
    r5.append(autoBox, span(" 自动调整：拖延完成后，从完成那天重新起算"));
    if (t.repeat_every > 0) {
      const note = document.createElement("div");
      note.className = "hintline";
      note.textContent = "这是重复任务：改内容 / 提醒天数会连这一天起还没做的一起改；改间隔会重新生成之后的日期。";
      form.append(title, r1, r2, r5, r4, note, r3);
    } else {
      form.append(title, r1, r2, r4, r3);
    }
    row.appendChild(form);
    title.focus();

    cancel.addEventListener("click", () => form.remove());
    const submit = async () => {
      const name = title.value.trim();
      if (!name) { toast("任务内容不能为空"); return; }
      const patch = {};
      if (name !== t.title) patch.title = name;
      if (dayIn.value && dayIn.value !== t.day) patch.day = dayIn.value;
      const rem = Number(remind.value === "" ? t.remind_after_days : remind.value);
      if (rem !== t.remind_after_days) patch.remind_after_days = rem;
      const rep = Number(repeat.value || 0);
      if (rep !== (t.repeat_every || 0)) patch.repeat_every = rep;
      if (priSel.value !== (t.priority === "main" ? "main" : "minor")) patch.priority = priSel.value;
      if (autoBox.checked !== !!t.auto_adjust && (t.repeat_every > 0 || rep > 0)) patch.auto_adjust = autoBox.checked;
      else if (rep > 0 && rep !== (t.repeat_every || 0) && autoBox.checked) patch.auto_adjust = true;
      if (!Object.keys(patch).length) { form.remove(); return; }
      await taskPatch(t.id, patch);
    };
    save.addEventListener("click", submit);
    title.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
      if (e.key === "Escape") { e.stopPropagation(); form.remove(); }
    });
  }

  function toggleOps(row) {
    const ops = row.querySelector(".task__ops");
    if (!ops) return;
    const willShow = ops.hidden;
    $$(".task__ops", el.rail).forEach((n) => { n.hidden = true; });
    ops.hidden = !willShow;
  }

  function addFormEl(day) {
    const wrap = document.createElement("div");

    const add = document.createElement("button");
    add.type = "button";
    add.className = "day__add";
    add.textContent = "+ 加一件事";

    const form = document.createElement("div");
    form.className = "day__form";
    form.hidden = true;

    const input = document.createElement("input");
    input.className = "input input--tiny";
    input.placeholder = "要做的事…";

    const row = document.createElement("div");
    row.className = "day__form-row";
    const pre = document.createElement("span");
    pre.textContent = "拖满";
    const num = document.createElement("input");
    num.type = "number";
    num.className = "num--tiny";
    num.value = "3";
    num.min = "0";
    num.max = "365";
    const post = document.createElement("span");
    post.textContent = "天提醒";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "btn btn--mini";
    ok.textContent = "加";
    row.append(pre, num, post, ok);

    // 重复：每 N 天一次，留空 = 不重复
    const repRow = document.createElement("div");
    repRow.className = "day__form-row";
    const repPre = document.createElement("span");
    repPre.textContent = "每";
    const rep = document.createElement("input");
    rep.type = "number";
    rep.className = "num--tiny";
    rep.placeholder = "—";
    rep.min = "1";
    rep.max = "365";
    const repPost = document.createElement("span");
    repPost.textContent = "天重复（留空不重复，休息日自动可选）";
    repRow.append(repPre, rep, repPost);

    // 自动调整：重复任务拖延后才完成，下一次从完成那天重新起算
    const autoRow = document.createElement("label");
    autoRow.className = "day__form-row";
    autoRow.title = "比如每两天一次的锻炼，拖了一天才做完，下一次就从做完那天起再隔两天";
    const autoBox = document.createElement("input");
    autoBox.type = "checkbox";
    autoRow.append(autoBox, span0(" 自动调整：拖延完成后，从完成那天重新起算"));

    // 主次：主要任务周末也拖延；次要任务周末变可选、不再拖延
    const priRow = document.createElement("div");
    priRow.className = "day__form-row";
    const priSel = document.createElement("select");
    priSel.className = "input input--tiny task__pri";
    [["minor", "次要（周末可选）"], ["main", "主要（周末也拖延）"]].forEach(([v, text]) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = text;
      priSel.appendChild(o);
    });
    priRow.append(span0("主次"), priSel);
    form.append(input, row, repRow, autoRow, priRow);

    const submit = () => {
      const title = input.value.trim();
      if (!title) return;
      taskCreate(day.day, title, Number(num.value || 3), Number(rep.value || 0), priSel.value, autoBox.checked);
      input.value = "";
      rep.value = "";
      autoBox.checked = false;
      priSel.value = "minor";
      form.hidden = true;
    };
    ok.addEventListener("click", submit);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); submit(); }
    });
    add.addEventListener("click", () => {
      form.hidden = !form.hidden;
      if (!form.hidden) input.focus();
    });

    wrap.append(add, form);
    return wrap;
  }

  /* ───── 月历：点「今天」展开，点某一天跳过去 ───── */

  const MONTH_HEAD = ["一", "二", "三", "四", "五", "六", "日"];

  async function loadMonth(ym) {
    try {
      const data = await api(`/api/calendar/month${ym ? `?ym=${ym}` : ""}`);
      cal.ym = data.ym;
      cal.month = data;
      renderMonth();
    } catch (err) {
      toast(`月历加载失败：${err.message}`);
    }
  }

  function shiftMonth(delta) {
    if (!cal.month) return;
    let y = cal.month.year;
    let m = cal.month.month + delta;
    if (m < 1) { m = 12; y -= 1; }
    if (m > 12) { m = 1; y += 1; }
    loadMonth(`${y}-${String(m).padStart(2, "0")}`);
  }

  function renderMonth() {
    const box = $("#calMonth");
    const data = cal.month;
    box.hidden = !cal.monthOpen;
    $("#calWhere").classList.toggle("is-open", cal.monthOpen);
    if (!cal.monthOpen || !data) return;
    box.innerHTML = "";

    const head = document.createElement("div");
    head.className = "month__head";
    const mk = (text, title, fn, cls = "month__nav") => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = cls;
      b.textContent = text;
      b.title = title;
      b.addEventListener("click", fn);
      return b;
    };
    const label = document.createElement("span");
    label.className = "month__title";
    label.textContent = `${data.year} 年 ${data.month} 月`;
    head.append(
      mk("‹", "上个月", () => shiftMonth(-1)),
      label,
      mk("›", "下个月", () => shiftMonth(1)),
      mk("回到今天", "回到今天", () => { cal.monthOpen = false; renderMonth(); loadCalendar(0); }, "month__today"),
    );
    box.appendChild(head);

    const grid = document.createElement("div");
    grid.className = "month__grid";
    MONTH_HEAD.forEach((w, i) => {
      const h = document.createElement("span");
      h.className = "month__wd" + (i >= 5 ? " is-rest" : "");
      h.textContent = w;
      grid.appendChild(h);
    });
    for (let i = 0; i < data.lead; i++) grid.appendChild(document.createElement("span"));
    const center = cal.data ? cal.data.center : data.today;
    data.days.forEach((d) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "month__day"
        + (d.festivals && d.festivals.length ? " has-fest" : "")
        + (d.is_rest ? " is-rest" : "")
        + (d.is_today ? " is-today" : "")
        + (d.day === center ? " is-center" : "");
      const n = document.createElement("span");
      n.textContent = String(d.n);
      b.appendChild(n);
      if (d.festivals && d.festivals.length) b.title = d.festivals.join("、");
      if (d.total > 0) {
        const dot = document.createElement("i");
        dot.className = "month__dot" + (d.open > 0 ? (d.offset < 0 ? " is-late" : " is-open") : " is-done");
        b.appendChild(dot);
        b.title = `${d.total} 件事，${d.open} 件没做`;
      }
      b.addEventListener("click", () => {
        cal.monthOpen = false;
        renderMonth();
        loadCalendar(d.offset);
      });
      grid.appendChild(b);
    });
    box.appendChild(grid);
  }

  function toggleMonth() {
    cal.monthOpen = !cal.monthOpen;
    if (cal.monthOpen) {
      const c = cal.data && cal.data.center;
      loadMonth(c ? c.slice(0, 7) : undefined);
    } else {
      renderMonth();
    }
  }

  function renderCalendar() {
    if (!cal.data) return;
    const box = $("#calDays");
    box.innerHTML = "";
    const where = $("#calWhere");
    const c = cal.data.center || "";
    where.textContent = cal.offset === 0 ? "今天"
      : `${Number(c.slice(5, 7))} 月 ${Number(c.slice(8, 10))} 日`;
    where.classList.toggle("is-away", cal.offset !== 0);
    cal.data.days.forEach((d) => box.appendChild(dayEl(d)));
  }

  /** 把服务端生成的 AI 消息接到聊天里（日历触发的、提醒触发的都走这里）。 */
  function appendPhalMessages(messages) {
    if (!messages || !messages.length) return;
    const blank = $(".blank");
    if (blank) blank.remove();
    messages.forEach((m) => {
      appendMessage(m);
      state.messages.push(m);
    });
    scrollToBottom(true);
  }

  async function taskPatch(id, patch) {
    if (patch.status === "done" || patch.status === "skipped") cal.fresh.add(id);
    try {
      const res = await api(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
      await loadCalendar(cal.offset);
      appendPhalMessages(res.messages);
    } catch (err) {
      cal.fresh.delete(id);
      toast(`改任务失败：${err.message}`);
    }
  }

  /** 取消拖延：任务挪回今天、拖延天数清零；重复任务本身不受影响。 */
  async function taskCancelDelay(id) {
    try {
      await api(`/api/tasks/${id}/cancel-delay`, { method: "POST" });
      await loadCalendar(cal.offset);
      toast("已取消拖延");
    } catch (err) {
      toast(`取消拖延失败：${err.message}`);
    }
  }

  const span0 = (text) => { const x = document.createElement("span"); x.textContent = text; return x; };

  async function taskCreate(day, title, remind, repeat = 0, priority = "minor", autoAdjust = false) {
    try {
      const res = await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ title, day, remind_after_days: remind, repeat_every: repeat, priority, auto_adjust: autoAdjust }),
      });
      await loadCalendar(cal.offset);
      appendPhalMessages(res.messages);
    } catch (err) {
      toast(`加任务失败：${err.message}`);
    }
  }

  async function taskDelete(id, series = false) {
    const ok = await confirmDialog({
      title: series ? "删掉这件事和之后所有的重复？" : "删掉这件事？",
      text: series
        ? "这一天和之后还没做的重复任务都会删掉，并且不再重复。已经盖了章的保留。"
        : "删了就没了，日历里不再显示。（重复任务只删这一天）",
      okText: "删除",
    });
    if (!ok) return;
    try {
      await api(`/api/tasks/${id}${series ? "?series=true" : ""}`, { method: "DELETE" });
      await loadCalendar(cal.offset);
    } catch (err) {
      toast(`删除失败：${err.message}`);
    }
  }

  /** 启动时让后端检查有没有拖太久的任务，有就让 Phal 主动说一句。 */
  async function checkReminders() {
    try {
      const res = await api("/api/calendar/remind", { method: "POST" });
      if (res.messages && res.messages.length) {
        appendPhalMessages(res.messages);
        toast("Phal 有件事想提醒你");
        notifyPhal(res.messages);
      }
    } catch (_) {
      /* 提醒失败不影响使用 */
    }
  }

  /* ───────────────────────── 关闭服务 ───────────────────────── */

  let farewellShown = false;

  function showFarewell() {
    if (farewellShown) return;
    farewellShown = true;
    const box = document.createElement("div");
    box.className = "farewell";
    const b = document.createElement("b");
    b.textContent = "服务已关闭";
    const p = document.createElement("p");
    p.textContent = "后端进程已经退出，这个页面可以直接关掉了。\n下次双击桌面上的「AI 聊天」就能重新启动。";
    box.append(b, p);
    document.body.appendChild(box);
  }

  async function shutdownService() {
    const ok = await confirmDialog({
      title: "关闭服务？",
      text: "后端进程会退出，这个页面也会关掉。\n下次双击桌面上的「AI 聊天」就能重新启动。",
      okText: "关闭服务",
    });
    if (!ok) return;

    try {
      await api("/api/shutdown", { method: "POST" });
    } catch (_) {
      // 进程可能已经退了，忽略
    }
    window.close(); // 多数浏览器只允许关掉脚本自己开的标签页
    setTimeout(showFarewell, 400);
    setTimeout(showFarewell, 1800);
  }

  function clearSelection() {
    $$(".msg.is-selected").forEach((n) => n.classList.remove("is-selected"));
    $$(".actions").forEach((n) => n.remove());
    state.selected = null;
  }

  function setQuote(msg, meta) {
    state.quote = { id: msg.id != null ? msg.id : null, role: msg.role, content: msg.content, meta: meta || null };
    el.quoteWho.textContent = meta && meta.title ? `引用《${meta.title}》` : `引用 ${ROLE_LABEL[msg.role] || ""}`;
    el.quoteText.textContent = msg.content;
    el.quoteBar.hidden = false;
    el.input.focus();
  }

  function clearQuote() {
    state.quote = null;
    el.quoteBar.hidden = true;
    el.quoteText.textContent = "";
  }

  /* ───────────────────────── 发送 ───────────────────────── */

  function autosize() {
    el.input.style.height = "auto";
    el.input.style.height = `${Math.min(el.input.scrollHeight, 168)}px`;
  }

  function updateSendState() {
    el.send.disabled = !el.input.value.trim();          // 连发：她回复的时候也能发
  }

  function showMeta(meta) {
    if (!meta) return;
    el.hint.textContent =
      `已注入 ${meta.date} ${meta.weekday} · 短期上下文 ${meta.recent_count} 条 · 长期记忆 ${meta.memory_count} 条 · 历史归档 ${meta.archive_total} 条`;
  }

  /* ───────── 连发（见 docs/已完成的计划/连发输入.md）：随时能发，每条单独一个气泡；她什么时候回、怎么回由后端决定，回复从 /api/stream 推过来 ───────── */
  const live = { es: null, typing: null, chain: Promise.resolve(), lastKey: 0, reported: false, lastReport: 0 };

  const knownMsg = (id) => id != null && state.messages.some((m) => m.id === id);
  /** 加一条消息（去重：发送接口的返回、推送、生活 tick 可能都会带来同一条）。 */
  function addLiveMessage(m) {
    if (!m || knownMsg(m.id)) return false;
    const blank = $(".blank");
    if (blank) blank.remove();
    if (m.role === "assistant") hideHerTyping();
    appendMessage(m);
    state.messages.push(m);
    if (live.typing) el.stream.appendChild(live.typing.node);   // 她的「正在输入…」一直在最下面
    scrollToBottom(m.role === "user");
    return true;
  }
  function showHerTyping(label) {
    if (!live.typing) live.typing = startAssistantStream();
    if (label && label !== "正在输入…") setStatus(live.typing, label); else clearStatus(live.typing);
    el.stream.appendChild(live.typing.node);
    scrollToBottom();
  }
  function hideHerTyping() {
    if (live.typing) { live.typing.node.remove(); live.typing = null; }
  }
  function handleStream(ev) {
    switch (ev.type) {
      case "user": addLiveMessage(ev.message); break;
      case "tool": showHerTyping(ev.label); break;
      case "tool_result": if (ev.name === "add_task") loadCalendar(cal.offset); break;
      case "break": addLiveMessage(ev.message); break;
      case "discarded": hideHerTyping(); break;
      case "done":
        hideHerTyping();
        (ev.messages || []).forEach((m) => addLiveMessage(m));     // 出戏 / 演示模式：不拆条，只在 done 里
        if ((ev.messages || []).length) notifyPhal(ev.messages);    // 我不在聊天页面时：她回我了，弹通知
        if (ev.stats) renderStats(ev.stats);
        if (ev.meta) showMeta(ev.meta);
        break;
      case "error": hideHerTyping(); appendError(ev.message); scrollToBottom(true); break;
      case "notice": toast(ev.text); break;
      case "room":
        toast(`她收下了「${ev.item.name}」，放在${ev.item.location}`);
        document.dispatchEvent(new CustomEvent("phal:room-changed", { detail: ev.item }));
        break;
      case "doc": refreshSide(); break;
      case "life": lifeTick(); setTimeout(lifeTick, 4000); setTimeout(lifeTick, 14000); break;
      default: break;
    }
  }
  function openStream() {
    if (live.es || typeof EventSource === "undefined") return;
    live.es = new EventSource("/api/stream");                 // 断了浏览器会自己重连
    live.es.onmessage = (e) => { try { handleStream(JSON.parse(e.data)); } catch (err) { console.error("bad stream event", e.data, err); } };
  }
  /** 「正在输入」：输入框里有字，或者 1.5 秒内有按键。打字时每 0.7 秒报一次，停下来报一声。 */
  const typingNow = () => !!el.input.value.trim() || Date.now() - live.lastKey < 1500;
  function reportTyping(t, force) {
    const now = Date.now();
    if (!force && t === live.reported && (!t || now - live.lastReport < 700)) return;
    live.reported = t;
    live.lastReport = now;
    fetch("/api/typing", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ typing: t }) }).catch(() => {});
  }

  function send() {
    const text = el.input.value.trim();
    if (!text) return;
    el.input.value = "";
    autosize();
    updateSendState();
    const quote = state.quote;
    clearQuote();
    clearSelection();
    reportTyping(typingNow(), true);
    const body = {
      content: text,
      quote_id: quote ? quote.id : null,
      quote_text: quote ? quote.content : null,
      quote_role: quote ? quote.role : null,
      quote_meta: quote ? quote.meta : null,
    };
    // 一条一条按顺序发（连着按回车也不会乱序）；不等她回
    live.chain = live.chain.then(async () => {
      try {
        const r = await api("/api/chat/send", { method: "POST", body: JSON.stringify(body) });
        addLiveMessage(r.message);
      } catch (err) {
        appendError(err.message || String(err));
        scrollToBottom(true);
      }
    });
  }

  async function regenerate() {
    if (state.sending) return;
    const ok = await confirmDialog({
      title: "让它重说一遍？",
      text: "当前这条回复会被删掉（窗口和历史归档里都删），然后用你上一条消息重新生成一次。",
      okText: "重说",
    });
    if (!ok) return;
    await runTurn("/api/chat/regenerate", null);
  }

  /** 读 SSE 流：把 "data: {...}" 拆出来交给 handle。 */
  async function readSSE(res, handle) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          try { handle(JSON.parse(payload)); } catch (err) { console.error("bad sse", payload, err); }
        }
      }
    }
  }

  /** /api/chat 和 /api/chat/regenerate 共用的流式渲染。 */
  async function runTurn(url, body) {
    if (state.sending) return;
    state.sending = true;
    updateSendState();

    const blank = $(".blank");
    if (blank) blank.remove();

    let current = null;   // { node, msg, text, status }
    let assistantText = "";
    let partsSeen = 0;    // 这一轮已经收尾了几条（拆分回复时用）

    const endBubble = (saved) => {
      if (!current) return;
      finalizeAssistant(current, saved || null);
      current = null;
      assistantText = "";
    };

    const handle = (event) => {
      switch (event.type) {
        case "user": {
          appendMessage(event.message);
          showMeta(event.meta);
          state.messages.push(event.message);
          scrollToBottom(true);
          break;
        }
        case "regenerate": {
          // 后端把上一轮的回复全删了（拆分回复时可能有好几条），界面上也全拿掉
          while (state.messages.length && state.messages[state.messages.length - 1].role === "assistant") {
            state.messages.pop();
            const nodes = el.stream.querySelectorAll(".msg--ai");
            if (nodes.length) nodes[nodes.length - 1].remove();
          }
          partsSeen = 0;
          showMeta(event.meta);
          break;
        }
        case "delta": {
          if (!current) current = startAssistantStream();
          clearStatus(current);
          assistantText += event.text;
          current.text.textContent = assistantText;
          scrollToBottom();
          break;
        }
        case "break": {
          // 模型发完一条了，收尾这个气泡，下一条会开新的
          endBubble(event.message);
          if (event.message) {
            state.messages.push(event.message);
            partsSeen += 1;
          }
          scrollToBottom();
          break;
        }
        case "tool": {
          if (!current) current = startAssistantStream();
          setStatus(current, event.label || "正在翻找…");
          scrollToBottom();
          break;
        }
        case "tool_result":
          if (current) clearStatus(current);
          // 聊天里让 Phal 记了任务，左侧日历要跟着刷新（完成 / 不干只能在日历上自己盖章）
          if (event.name === "add_task") loadCalendar(cal.offset);
          break;
        case "notice":
          toast(event.text);
          break;
        case "room":
          // 她收下了你送的东西，放进了房间
          toast(`她收下了「${event.item.name}」，放在${event.item.location}`);
          document.dispatchEvent(new CustomEvent("phal:room-changed", { detail: event.item }));
          break;
        case "doc":
          refreshSide();
          break;
        case "life":
          // 她回复完了，接着切到别的状态 / 开始写东西：马上刷新状态栏，过几秒再刷新一次拿她说的话
          lifeTick();
          setTimeout(lifeTick, 4000);
          setTimeout(lifeTick, 14000);
          break;
        case "error":
          if (current) { current.node.remove(); current = null; assistantText = ""; }
          appendError(event.message);
          scrollToBottom(true);
          break;
        case "done": {
          const list = Array.isArray(event.messages)
            ? event.messages
            : (event.message ? [event.message] : []);
          const remaining = list.slice(partsSeen);
          if (current) {
            if (remaining.length) {
              endBubble(remaining[0]);
              remaining.forEach((m) => state.messages.push(m));
            } else {
              // 最后一段是空的，别留一个空气泡
              current.node.remove();
              current = null;
              assistantText = "";
            }
          } else {
            remaining.forEach((m) => { appendMessage(m); state.messages.push(m); });
          }
          partsSeen = list.length;
          if (event.stats) renderStats(event.stats);
          break;
        }
        default:
          break;
      }
    };

    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : null,
      });
      if (!res.ok || !res.body) {
        const raw = await res.text();
        let detail = raw;
        try { detail = JSON.parse(raw).detail || raw; } catch (_) {}
        throw new Error(detail || `请求失败 (${res.status})`);
      }
      await readSSE(res, handle);
    } catch (err) {
      if (current) { current.node.remove(); current = null; }
      appendError(err.message || String(err));
      scrollToBottom(true);
    } finally {
      state.sending = false;
      updateSendState();
      el.input.focus();
      refreshStats();
    }
  }

  function startAssistantStream() {
    // 注意：这个对象是引用传递的，收到 done 之后要把服务端返回的 id 同步回来，
    // 否则闭包里 msg.id 永远是 undefined，消息就没有「删除」按钮。
    const msg = { role: "assistant", content: "", pending: true };
    const node = buildMessageEl(msg);
    node.dataset.streaming = "1";
    const bubble = $(".bubble", node);
    bubble.innerHTML = "";
    const text = document.createElement("div");
    text.className = "bubble__text";
    const dots = document.createElement("div");
    dots.className = "dots";
    dots.innerHTML = "<i></i><i></i><i></i>";
    bubble.appendChild(text);
    bubble.appendChild(dots);

    if (state.lastDay == null) ensureDaySep(dayOf(new Date().toISOString()));
    el.stream.appendChild(node);
    return { node, msg, text, bubble, dots, status: null };
  }

  function setStatus(current, label) {
    if (current.dots) { current.dots.remove(); current.dots = null; }
    if (!current.status) {
      current.status = document.createElement("div");
      current.status.className = "status";
      current.node.appendChild(current.status);
    }
    current.status.textContent = label;
  }

  function clearStatus(current) {
    if (current.status) { current.status.remove(); current.status = null; }
  }

  function finalizeAssistant(current, saved) {
    if (current.dots) current.dots.remove();
    clearStatus(current);
    if (saved) {
      // 把服务端给的 id / 时间戳同步回对象，闭包里的 msg.id 才有值
      Object.assign(current.msg, saved);
      if (saved.id != null) current.node.dataset.id = String(saved.id);
    }
    const meta = document.createElement("div");
    meta.className = "msg__meta";
    meta.textContent = timeOf(saved ? saved.created_at : new Date().toISOString());
    current.node.appendChild(meta);
    markBannedWords(current.node, current.text.textContent);
  }

  function appendError(message) {
    const node = document.createElement("div");
    node.className = "msg msg--ai msg--error";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = message;
    node.appendChild(bubble);
    el.stream.appendChild(node);
  }

  /* ───────────────────────── 抽屉 / 标签 ───────────────────────── */

  function openDrawer(tab) {
    el.drawer.classList.add("is-open");
    el.scrim.classList.add("is-open");
    el.drawer.setAttribute("aria-hidden", "false");
    if (tab) switchTab(tab);
  }

  function closeDrawer() {
    el.drawer.classList.remove("is-open");
    el.scrim.classList.remove("is-open");
    el.drawer.setAttribute("aria-hidden", "true");
  }

  function requestCloseDrawer() { closeDrawer(); }

  function switchTab(tab) {
    $$(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === tab));
    $$(".pane").forEach((p) => p.classList.toggle("is-active", p.dataset.pane === tab));
    if (tab === "memory") { refreshStats(); loadMemories(); loadStyle(); }
    if (tab === "drafts") loadDrafts();
  }

  /* ───────────────────────── 说话风格 ───────────────────────── */

  async function loadStyle() {
    try {
      const s = await api("/api/settings");
      state.settings = Object.assign(state.settings || {}, s);
      const box = $("#styleNotes");
      if (box && document.activeElement !== box) box.value = s.style_notes || "";
      $("#styleEdits").textContent = s.edits_count
        ? `你亲手改过 ${s.edits_count} 条回答，最近的几条会原样当范例给它看。`
        : "在聊天里点 AI 的回复 →「编辑」，改成你想要的说法，它会照着学。";
      $("#styleSwitch").checked = !!s.enable_style_learning;
    } catch (_) { /* 面板打不开风格不影响别的 */ }
  }

  /* ───────────────────────── 设置 ───────────────────────── */

  /* 设置都在「设置」页里（settings.js）；这里只留把最新设置应用到界面上 */
  function applySettings(s) {
    state.settings = s;
    el.personaName.textContent = s.persona_name || "AI";
    document.title = s.persona_name || "AI 聊天";
  }

  document.addEventListener("phal:settings-changed", (e) => {
    if (!e.detail) return;
    applySettings(e.detail);
    refreshBannedFlags();
  });
  document.addEventListener("phal:festivals-changed", () => loadCalendar(cal.offset));

  /* ───────────────────────── 统计 / 记忆 / 搜索 ───────────────────────── */

  function renderStats(stats) {
    if (!stats) return;
    el.statsLine.innerHTML =
      `当前会话 <b>${stats.session_messages}</b> 条 · 历史归档 <b>${stats.archive_messages}</b> 条 · ` +
      `长期记忆 <b>${stats.memories}</b> 条 · 共 <b>${stats.sessions}</b> 段对话`;
  }

  async function refreshStats() {
    try {
      const data = await api("/api/bootstrap");
      state.now = data.now;
      renderStats(data.stats);
    } catch (_) { /* 忽略 */ }
  }

  async function loadMemories() {
    try {
      const data = await api("/api/memories");
      renderMemories(data.memories);
    } catch (err) {
      el.memoryList.innerHTML = "";
      el.memoryList.appendChild(emptyHint(`读取失败：${err.message}`));
    }
  }

  function emptyHint(text) {
    const d = document.createElement("div");
    d.className = "empty-hint";
    d.textContent = text;
    return d;
  }

  function renderMemories(list) {
    el.memoryList.innerHTML = "";
    if (!list.length) {
      el.memoryList.appendChild(emptyHint("还没有记忆条目。聊天中模型会自动记下重要的事，也可以手动添加。"));
      return;
    }
    for (const m of list) {
      const row = document.createElement("div");
      row.className = "mem";
      const meta = document.createElement("div");
      meta.className = "mem__meta";
      const left = document.createElement("span");
      const CAT = { about_user: "关于我", phal_fact: "Phal 说过", shared: "我们之间" };
      left.textContent = `${m.created_at.slice(0, 10)} · ${CAT[m.category] || m.kind}${m.source === "model" ? " · 模型写入" : m.source === "auto" ? " · 自动提取" : ""}`;
      const ops = document.createElement("span");
      ops.className = "mem__ops";
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "删除";
      del.addEventListener("click", async () => {
        try {
          await api(`/api/memories/${m.id}`, { method: "DELETE" });
          loadMemories();
          refreshStats();
        } catch (err) { toast(err.message); }
      });
      ops.appendChild(del);
      meta.append(left, ops);
      const text = document.createElement("div");
      text.className = "mem__text";
      text.textContent = m.content;
      row.append(meta, text);
      el.memoryList.appendChild(row);
    }
  }

  async function doSearch() {
    const q = $("#searchInput").value.trim();
    el.searchHits.innerHTML = "";
    if (!q) {
      el.searchHits.appendChild(emptyHint("输入关键词，翻一翻更早的聊天记录。"));
      return;
    }
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=30`);
      if (!data.results.length) {
        el.searchHits.appendChild(emptyHint(`没有找到含「${q}」的记录。`));
        return;
      }
      const head = emptyHint(`共 ${data.total} 条匹配，显示最近 ${data.results.length} 条`);
      el.searchHits.appendChild(head);
      for (const r of data.results) {
        const row = document.createElement("div");
        row.className = "hit";
        const meta = document.createElement("div");
        meta.className = "hit__meta";
        const left = document.createElement("span");
        left.textContent = `${r.created_at} · ${ROLE_LABEL[r.role] || r.role}`;
        const ops = document.createElement("span");
        ops.className = "hit__ops";
        const qbtn = document.createElement("button");
        qbtn.type = "button";
        qbtn.textContent = "引用";
        // 归档记录不在当前会话里，只带文本快照，避免 id 串台
        qbtn.addEventListener("click", () => {
          setQuote({ id: null, role: r.role, content: r.content });
          closeDrawer();
        });
        const cbtn = document.createElement("button");
        cbtn.type = "button";
        cbtn.textContent = "复制";
        cbtn.addEventListener("click", () => {
          navigator.clipboard?.writeText(r.content).then(() => toast("已复制"), () => toast("复制失败"));
        });
        const dbtn = document.createElement("button");
        dbtn.type = "button";
        dbtn.textContent = "删除";
        dbtn.addEventListener("click", async () => {
          const ok = await confirmDialog({
            title: "删除这条历史记录？",
            text: "本地归档里的这条会被永久删除。如果它还在当前聊天窗口里，窗口里那条也会一起消失。",
            okText: "删除",
          });
          if (!ok) return;
          try {
            const res = await api(`/api/archive/${r.id}`, { method: "DELETE" });
            renderStats(res.stats);
            toast("已删除");
            await doSearch();
            await reloadMessages();
          } catch (err) {
            toast(`删除失败：${err.message}`);
          }
        });
        ops.append(qbtn, cbtn, dbtn);
        meta.append(left, ops);
        const text = document.createElement("div");
        text.className = "hit__text";
        text.textContent = r.content;
        row.append(meta, text);
        el.searchHits.appendChild(row);
      }
    } catch (err) {
      el.searchHits.innerHTML = "";
      el.searchHits.appendChild(emptyHint(`搜索失败：${err.message}`));
    }
  }

  /* ───────────────────────── 初始化 ───────────────────────── */

  function wire() {
    el.input.addEventListener("input", () => { autosize(); updateSendState(); live.lastKey = Date.now(); reportTyping(true); });
    setInterval(() => reportTyping(typingNow()), 300);
    el.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    });
    el.send.addEventListener("click", send);
    el.quoteClose.addEventListener("click", clearQuote);

    document.addEventListener("click", (e) => {
      if (!e.target.closest(".msg") && !e.target.closest(".actions")) clearSelection();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { clearSelection(); if (el.drawer.classList.contains("is-open")) requestCloseDrawer(); }
    });

    $("#btnSettings").addEventListener("click", () => { if (window.PhalSettings) window.PhalSettings.open(); });
    $("#btnRoom").addEventListener("click", () => { if (window.PhalRoom) window.PhalRoom.open(); });
    $("#btnMoney").addEventListener("click", () => { if (window.PhalMoney) window.PhalMoney.open(); });
    $("#btnNotebook").addEventListener("click", () => { if (window.PhalNotebook) window.PhalNotebook.open(); });
    $("#btnMemory").addEventListener("click", () => openDrawer("memory"));
    $("#btnSearch").addEventListener("click", () => { openDrawer("memory"); setTimeout(() => $("#searchInput").focus(), 320); });
    $("#drawerClose").addEventListener("click", closeDrawer);
    el.scrim.addEventListener("click", closeDrawer);
    $$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

    $("#btnDoSearch").addEventListener("click", doSearch);
    $("#searchInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); doSearch(); } });

    $("#btnAddMemory").addEventListener("click", async () => {
      const input = $("#memoryInput");
      const content = input.value.trim();
      if (!content) return;
      try {
        await api("/api/memories", { method: "POST", body: JSON.stringify({ content, kind: "fact", category: $("#memoryCategory").value }) });
        input.value = "";
        loadMemories();
        refreshStats();
      } catch (err) { toast(err.message); }
    });
    $("#memoryInput").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#btnAddMemory").click(); } });

    $("#lifeSwitch").addEventListener("change", async (e) => {
      try {
        await api("/api/life/enabled", { method: "POST", body: JSON.stringify({ enabled: e.target.checked }) });
        if (e.target.checked) lifeTick(); else { life.status = { enabled: false }; renderLife(); }
      } catch (err) { toast(`保存失败：${err.message}`); }
    });

    let styleTimer = null;
    $("#styleNotes").addEventListener("input", () => {
      clearTimeout(styleTimer);
      styleTimer = setTimeout(async () => {
        try {
          await api("/api/settings", { method: "POST", body: JSON.stringify({ style_notes: $("#styleNotes").value }) });
          toast("风格笔记已保存");
        } catch (err) { toast(`保存失败：${err.message}`); }
      }, 900);
    });
    $("#styleSwitch").addEventListener("change", async (e) => {
      try {
        await api("/api/settings", { method: "POST", body: JSON.stringify({ enable_style_learning: e.target.checked }) });
      } catch (err) { toast(`保存失败：${err.message}`); }
    });
    $("#btnLearnStyle").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "正在读过往聊天…";
      try {
        const data = await api("/api/style/learn", { method: "POST" });
        $("#styleNotes").value = data.style_notes || "";
        toast(data.style_notes ? "学好了" : "聊得还太少，学不出什么");
      } catch (err) {
        toast(`学习失败：${err.message}`);
      } finally {
        btn.disabled = false;
        btn.textContent = "让它重新读一遍过往聊天";
      }
    });

    $("#btnSummarize").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "整理中…";
      try {
        const data = await api("/api/memories/summarize", { method: "POST" });
        renderMemories(data.memories);
        const n = data.created.length;
        toast(n ? `新增 ${n} 条长期记忆` : "没有新的可记内容");
        refreshStats();
      } catch (err) {
        toast(`整理失败：${err.message}`);
      } finally {
        btn.disabled = false;
        btn.textContent = "让模型整理当前会话";
      }
    });

    $("#btnNewSession").addEventListener("click", async () => {
      if (!confirm("开一段新对话？\n\n只会清空当前聊天窗口，历史归档和人设都不会动。")) return;
      try {
        const data = await api("/api/session/new", { method: "POST" });
        state.messages = [];
        renderAll();
        renderStats(data.stats);
        closeDrawer();
        toast("新对话开始");
      } catch (err) { toast(err.message); }
    });

    // —— 删除 ——
    $("#btnClearConversation").addEventListener("click", () => clearHistory("current"));
    $("#btnClearAll").addEventListener("click", () => clearHistory("all"));
    $("#btnClearMemories").addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "清空长期记忆？",
        text: "memories 表里的所有记忆条目都会被永久删除。聊天记录不受影响。",
        okText: "清空",
      });
      if (!ok) return;
      try {
        const data = await api("/api/memories", { method: "DELETE" });
        renderMemories([]);
        renderStats(data.stats);
        toast(`已清空 ${data.removed} 条记忆`);
      } catch (err) { toast(`清空失败：${err.message}`); }
    });

    // —— 日历 ——
    $("#calPrev").addEventListener("click", () => loadCalendar(cal.offset - 1));
    $("#calNext").addEventListener("click", () => loadCalendar(cal.offset + 1));
    $("#calWhere").addEventListener("click", toggleMonth);
    $("#btnCalendar").addEventListener("click", toggleRail);
    if (el.railScrim) el.railScrim.addEventListener("click", closeRail);

    // —— 关闭服务 ——
    $("#btnShutdown").addEventListener("click", shutdownService);

  }

  /* ───────────────────────── 阅读器发来的引用 ───────────────────────── */

  /** 阅读器和聊天在同一个网页里：阅读器里「发给 Phal / 跟她聊聊这条」会直接把引用递过来。 */
  document.addEventListener("phal:quote", (e) => {
    const q = e.detail;
    if (!q || !q.text) return;
    setQuote({ id: null, role: q.role || "user", content: q.text }, q.meta || null);
    toast(q.meta && q.meta.kind === "annotation" ? "已引用她的批注，接着聊吧" : "已引用书里的这段，写点什么发给她吧");
  });

  /** 打开书架 / 阅读器（同一个网页里的全屏层，不跳转）。聊天里指向 /reader 的链接也走这里。 */
  function openReader(params) {
    document.dispatchEvent(new CustomEvent("phal:open-reader", { detail: params || { shelf: true } }));
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest('a[href^="/reader"]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    e.preventDefault();
    const q = new URL(a.href, location.href).searchParams;
    openReader({ book: q.get("book"), ch: q.get("ch"), off: q.get("off"), at: q.get("at"), shelf: !q.get("book") });
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest("#btnReader")) openReader({ shelf: true });
  });

  function takePendingQuote() {
    let raw = null;
    try { raw = localStorage.getItem("phal.pendingQuote"); localStorage.removeItem("phal.pendingQuote"); } catch (_) { /* 无痕 */ }
    if (!raw) return;
    try {
      const q = JSON.parse(raw);
      if (q && q.text) {
        setQuote({ id: null, role: q.role || "user", content: q.text }, q.meta || null);
        toast(q.meta && q.meta.kind === "annotation" ? "已引用她的批注，接着聊吧" : "已引用书里的这段，写点什么发给她吧");
      }
    } catch (_) { /* 坏数据直接忽略 */ }
  }

  /* ───────────────────────── Phal 的生活：状态栏 + 主动消息 ───────────────────────── */

  const LIFE_ICONS = {
    tea: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 10h11v4.5A4.5 4.5 0 0 1 11.5 19h-2A4.5 4.5 0 0 1 5 14.5z"/><path d="M16 11.5h1.2a2.3 2.3 0 0 1 0 4.6H16"/><path d="M8 4.5c-.8 1 .8 1.6 0 2.8M12 4.5c-.8 1 .8 1.6 0 2.8"/></svg>',
    read: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 6.5C10.2 5 7.6 4.4 4 4.6v13c3.6-.2 6.2.4 8 1.9 1.8-1.5 4.4-2.1 8-1.9v-13c-3.6-.2-6.2.4-8 1.9z"/><path d="M12 6.5v13"/></svg>',
    daze: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17.5h9.5a3.5 3.5 0 0 0 .4-6.97A5 5 0 0 0 7.3 9.4 4.2 4.2 0 0 0 7 17.5z"/></svg>',
    sleep: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
    video: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="5" width="17" height="14" rx="3"/><path d="M10.5 9.5l4 2.5-4 2.5z"/></svg>',
    game: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M7 8h10a4 4 0 0 1 3.9 4.9l-.6 3a2.4 2.4 0 0 1-4 1.2L15 16H9l-1.3 1.1a2.4 2.4 0 0 1-4-1.2l-.6-3A4 4 0 0 1 7 8z"/><path d="M8.5 10.8v2.4M7.3 12h2.4"/><circle cx="15.5" cy="11.2" r=".6"/><circle cx="17.2" cy="12.8" r=".6"/></svg>',
    create: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20l1-4L16.5 4.5a2 2 0 0 1 3 3L8 19z"/><path d="M14.5 6.5l3 3"/></svg>',
  };

  /* ───────── 系统通知：她主动发消息时在通知栏弹一条（一批多段话只算一次） ───────── */

  const notif = { on: (() => { try { return localStorage.getItem("phal.notify") !== "0"; } catch (_) { return true; } })() };
  const canNotify = () => typeof Notification !== "undefined";

  function notifyStatusText() {
    if (!canNotify()) return "这个浏览器不支持系统通知。";
    if (!notif.on) return "通知已关闭。";
    if (Notification.permission === "granted") return "通知已开启。你不在聊天页面时（窗口最小化 / 在后台，或者开着房间、设置、阅读器），她发来消息或者回你，都会在通知栏弹出一条。";
    if (Notification.permission === "denied") return "浏览器拒绝了通知：点地址栏左边的锁形图标，把「通知」改成允许，再刷新页面。";
    return "还没授权：随便点一下页面，浏览器会问你要不要允许通知。";
  }

  function updateNotifyUi() {
    const sw = $("#notifySwitch");
    if (sw) { sw.checked = notif.on; $("#notifyHint").textContent = notifyStatusText(); }
  }

  /** 把一批主动消息合成一条通知。点通知回到页面，并打开消息里指向的文档 / 书。 */
  /** 我正看着聊天页面吗：窗口在前台、有焦点，而且没有打开房间 / 设置 / 零花钱 / 阅读器这些页面。 */
  function onChatPage() {
    if (document.hidden || !document.hasFocus()) return false;
    return ["roomRoot", "setRoot", "moneyRoot", "readerRoot"].every((id) => { const n = document.getElementById(id); return !n || n.hidden; });
  }
  /** 她发来的消息（主动发的、回我的都算）：我不在聊天页面时弹系统通知；正看着聊天页面就不弹。 */
  function notifyPhal(messages) {
    if (!messages || !messages.length || !notif.on || !canNotify() || Notification.permission !== "granted") return;
    if (onChatPage()) return;
    const first = messages[0];
    const meta = parseMeta(first.meta);
    const name = (state.settings && state.settings.persona_name) || "Phal";
    const label = SOURCE_LABEL[first.source];
    const body = messages.map((m) => (m.content || "").replace(/\s+/g, " ").trim()).filter(Boolean).join(" / ").slice(0, 120);
    try {
      const n = new Notification(label ? `${name} · ${label}` : name, {
        body,
        icon: "/static/whale-girl.ico",
        tag: `phal-${first.id}`,
        silent: false,
      });
      n.onclick = () => {
        window.focus();
        n.close();
        if (meta && meta.doc_id != null) document.dispatchEvent(new CustomEvent("phal:open-doc", { detail: { id: meta.doc_id } }));
        else if (meta && meta.book_id != null && meta.chapter != null) openReader({ book: meta.book_id, ch: meta.chapter, off: meta.offset || 0 });
      };
    } catch (_) { /* 有的环境构造不了通知，忽略 */ }
  }

  function wireNotify() {
    updateNotifyUi();
    // 浏览器要求授权必须由用户操作触发：第一次点页面时顺便问一次
    const ask = () => {
      document.removeEventListener("pointerdown", ask, true);
      if (notif.on && canNotify() && Notification.permission === "default") {
        Notification.requestPermission().then(updateNotifyUi, () => {});
      }
    };
    document.addEventListener("pointerdown", ask, true);
    $("#notifySwitch").addEventListener("change", (e) => {
      notif.on = e.target.checked;
      try { localStorage.setItem("phal.notify", notif.on ? "1" : "0"); } catch (_) { /* 无痕 */ }
      if (notif.on && canNotify() && Notification.permission === "default") Notification.requestPermission().then(updateNotifyUi, () => {});
      updateNotifyUi();
    });
  }

  const life = { status: null, at: 0, timer: null };

  function lifeElapsedText() {
    const s = life.status;
    if (!s) return "";
    let sec = s.elapsed || 0;
    if (!s.paused) sec += Math.max(0, (Date.now() - life.at) / 1000);
    sec = Math.floor(sec);
    if (s.sleeping) return "";
    const t = sec < 60 ? `${sec} 秒` : `${Math.floor(sec / 60)} 分钟`;
    return s.paused ? `已 ${t} · 聊天中，先停着` : `已 ${t}`;
  }

  function renderLife() {
    const bar = $("#lifebar");
    const s = life.status;
    if (!bar) return;
    const why = $("#btnWhy");
    if (!s || s.enabled === false) { bar.hidden = true; if (why) why.hidden = true; return; }
    bar.hidden = false;
    if (why) why.hidden = false;
    $("#lifeIcon").innerHTML = LIFE_ICONS[s.key] || "";
    $("#lifeLabel").textContent = s.label;
    $("#lifeBlurb").textContent = s.blurb || "";
    $("#lifeMood").textContent = s.mood ? `· ${s.mood}` : "";
    $("#lifeMood").title = s.mood_why || "";
    $("#lifeTime").textContent = lifeElapsedText();
    bar.dataset.state = s.key;
    bar.classList.toggle("is-paused", !!s.paused);
    if (s.book) {
      bar.href = `/reader?book=${s.book.id}&at=phal`;
      bar.title = `《${s.book.title}》：点一下去看她读到哪了`;
    } else {
      bar.href = "#";
      bar.title = "Phal 在做的事";
    }
  }

  /* 「为什么」：她最近的随机决定（下一个状态、这顿怎么吃、今天的事、买东西）和原因。设置页「生活事件」里也会调。 */
  const WHY_TABS = [["", "全部"], ["state", "状态"], ["meal", "吃饭"], ["event", "事件"], ["spend", "买东西"]];
  async function openWhy(kind = "") {
    let pop = $("#whyPop");
    if (pop) pop.remove();
    pop = document.createElement("div");
    pop.id = "whyPop";
    pop.className = "whypop";
    const head = document.createElement("div");
    head.className = "whypop__head";
    const tabs = document.createElement("div");
    tabs.className = "whypop__tabs";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "whypop__x";
    close.textContent = "×";
    head.append(tabs, close);
    const list = document.createElement("div");
    list.className = "whypop__list";
    pop.append(head, list);
    document.body.appendChild(pop);
    const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const load = async (k) => {
      tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("is-on", b.dataset.k === k));
      list.innerHTML = '<div class="whypop__empty">……</div>';
      try {
        const r = await fetch(`/api/why?limit=40${k ? `&kind=${k}` : ""}`).then((x) => x.json());
        list.innerHTML = r.items.length ? r.items.map((it) => `<div class="whypop__item"><span class="whypop__meta">${esc(it.at)} · ${esc(it.kind_label)}</span>${esc(it.text)}</div>`).join("")
          : '<div class="whypop__empty">最近没有记录（设置 → 状态与概率 里可以开关）。</div>';
      } catch (_) { list.innerHTML = '<div class="whypop__empty">暂时取不到</div>'; }
    };
    WHY_TABS.forEach(([k, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.k = k;
      b.textContent = label;
      b.addEventListener("click", () => load(k));
      tabs.appendChild(b);
    });
    const off = (e) => { if (e.type === "keydown" ? e.key === "Escape" : !pop.contains(e.target) && e.target.id !== "btnWhy") done(); };
    const done = () => { pop.remove(); document.removeEventListener("mousedown", off, true); document.removeEventListener("keydown", off, true); };
    close.addEventListener("click", done);
    document.addEventListener("mousedown", off, true);
    document.addEventListener("keydown", off, true);
    load(kind);
  }
  window.PhalWhy = { open: openWhy };
  document.addEventListener("click", (e) => { if (e.target && e.target.id === "btnWhy") openWhy(""); });

  function lastMessageId() {
    return state.messages.reduce((m, x) => (typeof x.id === "number" && x.id > m ? x.id : m), 0);
  }

  async function lifeTick() {
    try {
      const res = await api("/api/life/tick", { method: "POST", body: JSON.stringify({ after_id: Math.max(1, lastMessageId()) }) });
      life.status = res.status;
      life.at = Date.now();
      renderLife();
      if (docv.id == null) refreshSide();
      if (res.messages && res.messages.length && !state.sending) {
        const known = new Set(state.messages.map((m) => m.id));
        const fresh = res.messages.filter((m) => !known.has(m.id));
        if (fresh.length) {
          appendPhalMessages(fresh);
          toast(`${(state.settings && state.settings.persona_name) || "Phal"} 发来一条消息`);
          notifyPhal(fresh);                 // 一批算一次，不管分了几段
          refreshStats();
        }
      }
    } catch (_) { /* 后端暂时连不上就等下一次 */ }
  }

  function startLife() {
    lifeTick();
    clearInterval(life.timer);
    life.timer = setInterval(lifeTick, 30000);
    setInterval(() => { if (life.status && life.status.enabled !== false) $("#lifeTime").textContent = lifeElapsedText(); }, 1000);
  }

  async function loadDrafts() {
    const box = $("#draftList");
    try {
      const data = await api("/api/drafts");
      $("#lifeSwitch").checked = !!data.enabled;
      box.innerHTML = "";
      if (!data.drafts.length) {
        const e = document.createElement("div");
        e.className = "empty-hint";
        e.textContent = "还没有草稿。她有时会写点什么，有时写了又划掉。";
        box.appendChild(e);
        return;
      }
      data.drafts.forEach((d) => {
        const card = document.createElement("div");
        card.className = "draft" + (d.status === "abandoned" ? " is-abandoned" : "");
        const head = document.createElement("div");
        head.className = "draft__head";
        const title = document.createElement("span");
        title.className = "draft__title";
        title.textContent = d.title || (d.status === "abandoned" ? "（没写完）" : "（无题）");
        const kind = document.createElement("span");
        kind.className = "draft__kind";
        kind.textContent = `${d.kind}${d.status === "abandoned" ? " · 放弃了" : ""}${d.shared ? " · 给你看过" : ""}`;
        const del = document.createElement("button");
        del.type = "button";
        del.className = "draft__del";
        del.textContent = "×";
        del.title = "删掉这份草稿";
        del.addEventListener("click", async () => {
          if (!confirm("删掉这份草稿？")) return;
          try { await api(`/api/drafts/${d.id}`, { method: "DELETE" }); loadDrafts(); } catch (err) { toast(`删除失败：${err.message}`); }
        });
        head.append(title, kind, del);
        const body = document.createElement("div");
        body.className = "draft__body";
        body.textContent = d.content;
        const foot = document.createElement("div");
        foot.className = "draft__foot";
        foot.textContent = d.created_at.slice(0, 16);
        card.append(head, body, foot);
        box.appendChild(card);
      });
    } catch (err) {
      box.textContent = `草稿本加载失败：${err.message}`;
    }
  }

  /* ───────────────────────── 右侧卡片：在读的书 / 写作文档 / 我的文字 ───────────────────────── */

  const side = { data: null };

  function openSideDrawer(open) {
    $("#rrail").classList.toggle("is-open", open);
    $("#rrailScrim").classList.toggle("is-open", open);
    $("#rrail").setAttribute("aria-hidden", open ? "false" : "true");
  }

  function renderReadingCard(r) {
    const card = $("#cardReading");
    if (!r) { card.hidden = true; return; }
    card.hidden = false;
    card.innerHTML = "";
    const head = document.createElement("div");
    head.className = "sidecard__head";
    const t = document.createElement("span");
    t.className = "sidecard__title";
    t.textContent = r.reading_now ? "正在看" : "在读";
    head.appendChild(t);
    const body = document.createElement("button");
    body.type = "button";
    body.className = "bookmini";
    body.title = "在阅读器里打开，看她读到哪了";
    const name = document.createElement("div");
    name.className = "bookmini__t";
    name.textContent = `《${r.title}》`;
    const where = document.createElement("div");
    where.className = "bookmini__w";
    where.textContent = `${r.chapter_title || `第 ${r.chapter + 1} 章`} · ${r.percent}%`;
    const bar = document.createElement("div");
    bar.className = "bookmini__bar";
    const fill = document.createElement("i");
    fill.style.width = `${r.percent}%`;
    bar.appendChild(fill);
    body.append(name, where, bar);
    if (r.note) {
      const note = document.createElement("div");
      note.className = "bookmini__n";
      note.textContent = `「${r.note}」`;
      body.appendChild(note);
    }
    body.addEventListener("click", () => { openSideDrawer(false); openReader({ book: r.id, at: "phal" }); });
    card.append(head, body);
  }

  function docRow(d, mine) {
    const li = document.createElement("li");
    li.className = "docrow" + (d.full ? " is-full" : "");
    const main = document.createElement("button");
    main.type = "button";
    main.className = "docrow__main";
    const title = document.createElement("span");
    title.className = "docrow__title";
    title.textContent = d.title;
    const meta = document.createElement("span");
    meta.className = "docrow__meta";
    meta.textContent = mine ? `${d.chars} 字` : (d.full ? "写满了" : `${d.chars} / ${side.data.limit}`);
    main.append(title, meta);
    if (!mine) {
      const bar = document.createElement("span");
      bar.className = "docrow__bar";
      const fill = document.createElement("i");
      fill.style.width = `${Math.min(100, (d.chars / side.data.limit) * 100)}%`;
      bar.appendChild(fill);
      main.appendChild(bar);
    }
    main.addEventListener("click", () => { openSideDrawer(false); openDoc(d.id); });
    const ren = document.createElement("button");
    ren.type = "button";
    ren.className = "docrow__ren";
    ren.textContent = "✎";
    ren.title = "改标题";
    ren.addEventListener("click", () => {
      const input = document.createElement("input");
      input.className = "input input--tiny";
      input.value = d.title;
      input.maxLength = 60;
      li.replaceChildren(input);
      input.focus();
      input.select();
      let done = false;
      const finish = async (save) => {
        if (done) return;
        done = true;
        const v = input.value.trim();
        if (save && v && v !== d.title) {
          try { await api(`/api/docs/${d.id}`, { method: "PATCH", body: JSON.stringify({ title: v }) }); }
          catch (err) { toast(`改标题失败：${err.message}`); }
        }
        refreshSide();
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
      input.addEventListener("blur", () => finish(true));
    });
    li.append(main, ren);
    return li;
  }

  /** 信的卡片：标题下面一行小字，写年份和日期（只有信这样显示）；只读，没有改标题。 */
  function letterRow(d) {
    const li = document.createElement("li");
    li.className = "docrow docrow--letter";
    const main = document.createElement("button");
    main.type = "button";
    main.className = "docrow__main";
    const title = document.createElement("span");
    title.className = "docrow__title";
    title.textContent = d.title;
    const date = document.createElement("span");
    date.className = "docrow__date";
    date.textContent = d.dated_text || "";
    main.append(title, date);
    main.addEventListener("click", () => { openSideDrawer(false); openDoc(d.id); });
    li.appendChild(main);
    return li;
  }

  function renderSide() {
    const d = side.data;
    if (!d) return;
    renderReadingCard(d.reading);
    const letters = $("#letterList");
    letters.innerHTML = "";
    (d.letters || []).forEach((x) => letters.appendChild(letterRow(x)));
    if (!(d.letters || []).length) {
      const e = document.createElement("li");
      e.className = "docrow__empty";
      e.textContent = "每个月最后一天晚上，她会写一封信放在这里。";
      letters.appendChild(e);
    }
    const list = $("#docList");
    list.innerHTML = "";
    d.docs.forEach((x) => list.appendChild(docRow(x, false)));
    const hint = $("#docsHint");
    if (!d.docs.length) hint.textContent = "还没有文档。给她建一份空白的，她试着创作时就会真的往里写。";
    else if (d.blocked) hint.textContent = d.waiting ? "都写满了，她在等你给她新文档。" : "都写满了，她下次想写会先问你要新的。";
    else hint.textContent = "";
    hint.hidden = !hint.textContent;
    const mine = $("#mineList");
    mine.innerHTML = "";
    d.mine.forEach((x) => mine.appendChild(docRow(x, true)));
    if (!d.mine.length) {
      const e = document.createElement("li");
      e.className = "docrow__empty";
      e.textContent = "放进你的随笔 / 小说，她写东西时会模仿它的笔触。";
      mine.appendChild(e);
    }
  }

  async function refreshSide() {
    try {
      side.data = await api("/api/side");
      renderSide();
    } catch (_) { /* 卡片刷新失败不影响聊天 */ }
  }

  async function newDoc(kind, title) {
    try {
      const res = await api("/api/docs", { method: "POST", body: JSON.stringify({ kind, title }) });
      await refreshSide();
      return res.doc;
    } catch (err) {
      toast(`建文档失败：${err.message}`);
      return null;
    }
  }

  function wireSide() {
    $("#btnSide").addEventListener("click", () => openSideDrawer(!$("#rrail").classList.contains("is-open")));
    $("#rrailScrim").addEventListener("click", () => openSideDrawer(false));
    $("#docAdd").addEventListener("click", () => { $("#docNewForm").hidden = !$("#docNewForm").hidden; if (!$("#docNewForm").hidden) $("#docNewTitle").focus(); });
    $("#docNewForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const t = $("#docNewTitle").value.trim();
      if (!t) return;
      $("#docNewTitle").value = "";
      $("#docNewForm").hidden = true;
      const doc = await newDoc("phal", t);
      if (doc) toast("给她建好了，她下次想写就会写在里面");
    });
    $("#mineAdd").addEventListener("click", () => { $("#mineNewForm").hidden = !$("#mineNewForm").hidden; if (!$("#mineNewForm").hidden) $("#mineNewTitle").focus(); });
    $("#mineNewForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const t = $("#mineNewTitle").value.trim();
      if (!t) return;
      $("#mineNewTitle").value = "";
      $("#mineNewForm").hidden = true;
      const doc = await newDoc("mine", t);
      if (doc) { openSideDrawer(false); openDoc(doc.id); }
    });
    $("#mineFile").addEventListener("change", async (e) => {
      for (const f of e.target.files) {
        try {
          await api(`/api/docs/import?filename=${encodeURIComponent(f.name)}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: f });
        } catch (err) { toast(`${f.name}：${err.message}`); }
      }
      e.target.value = "";
      toast("已放进「我的文字」");
      refreshSide();
    });
    document.addEventListener("phal:open-doc", (e) => openDoc(e.detail.id));
  }

  /* ───────────────────────── 文档编辑器（网页里直接改） ───────────────────────── */

  const docv = { id: null, doc: null, base: null, dirty: false, timer: null, saving: false };

  function docState(text) { $("#docState").textContent = text; }

  function docCount() {
    const n = $("#docText").value.replace(/\s+/g, "").length;
    const lim = docv.doc && docv.doc.limit;
    $("#docCount").textContent = lim ? `${n} / ${lim} 字` : `${n} 字`;
    $("#docCount").classList.toggle("is-over", !!lim && n >= lim);
  }

  async function openDoc(id) {
    try {
      const data = await api(`/api/docs/${id}`);
      docv.id = id;
      docv.doc = data.doc;
      docv.base = data.doc.updated_at;
      docv.dirty = false;
      $("#docRoot").hidden = false;
      document.body.style.overflow = "hidden";
      $("#docTitle").value = data.doc.title;
      $("#docText").value = data.content;
      const ro = !!data.doc.readonly;                 // 信：只读
      $("#docText").readOnly = ro;
      $("#docTitle").readOnly = ro;
      $("#docSave").hidden = ro;
      $("#docHistory").hidden = ro;
      $("#docNote").textContent = ro
        ? `${data.doc.dated_text || ""}　这是 Phal 写给你的信：只读，她写好之后不会再改。`
        : data.doc.kind === "phal"
          ? "这是 Phal 的文档：她试图创作时会接着往下写。你也可以直接改，改完点「保存」她才会看到修改记录（她可能会问你为什么，也可能不同意、改回去）；想让她自己改，就在聊天里明确说，比如「把《" + data.doc.title + "》里…改成…」。改动都留有历史版本，可以撤销。"
          : "这是你自己的文字：她写东西时会模仿它的笔触，但不会改它。";
      docState("");
      docCount();
      $("#docDelete").hidden = false;
      $("#docHist").hidden = true;
      if (!ro) setTimeout(() => $("#docText").focus(), 50);
    } catch (err) {
      toast(`打开失败：${err.message}`);
    }
  }

  async function saveDoc(now) {
    if (!docv.dirty || docv.saving || docv.id == null) return;
    docv.saving = true;
    docState("保存中…");
    try {
      const res = await api(`/api/docs/${docv.id}`, { method: "PUT", body: JSON.stringify({ content: $("#docText").value, base: docv.base }) });
      docv.doc = res.doc;
      docv.base = res.doc.updated_at;
      docv.dirty = false;
      docState("已保存");
    } catch (err) {
      if (/期间被改过/.test(err.message)) {
        toast("Phal 刚在这份文档里写了新内容，已刷新。你刚才的改动没保存，请再改一次。");
        docv.dirty = false;
        await openDoc(docv.id);
      } else {
        docState("保存失败");
        toast(`保存失败：${err.message}`);
      }
    } finally {
      docv.saving = false;
    }
  }

  async function closeDoc() {
    clearTimeout(docv.timer);
    if (docv.dirty) {
      if (confirm("还有没保存的改动。\n确定 = 保存后返回；取消 = 放弃这些改动并返回。")) await saveDoc(true);
      else docv.dirty = false;
    }
    $("#docRoot").hidden = true;
    document.body.style.overflow = "";
    docv.id = null;
    refreshSide();
  }

  async function showDocHistory() {
    const box = $("#docHistList");
    box.innerHTML = "";
    const data = await api(`/api/docs/${docv.id}/versions`).catch(() => ({ versions: [] }));
    if (!data.versions.length) {
      box.textContent = "还没有历史版本。每次改动前都会自动留一份。";
    }
    data.versions.forEach((v) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "histrow";
      row.textContent = `${v.created_at.slice(5, 16)}　${v.reason}　${v.size} 字符`;
      row.addEventListener("click", async () => {
        if (!confirm("恢复到这个版本？现在的内容会先存进历史版本，不会丢。")) return;
        await saveDoc(true);
        try {
          await api(`/api/docs/${docv.id}/restore`, { method: "POST", body: JSON.stringify({ version_id: v.id }) });
          await openDoc(docv.id);
          toast("已恢复");
        } catch (err) { toast(`恢复失败：${err.message}`); }
      });
      box.appendChild(row);
    });
    $("#docHist").hidden = false;
  }

  function wireDoc() {
    $("#docBack").addEventListener("click", closeDoc);
    // 改完要手动点「保存」（或 Ctrl+S）才算数：保存之后 Phal 才会读到修改记录
    $("#docText").addEventListener("input", () => {
      docv.dirty = true;
      docState("有没保存的改动");
      docCount();
    });
    $("#docSave").addEventListener("click", () => saveDoc(true));
    $("#docRoot").addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveDoc(true); }
    });
    window.addEventListener("beforeunload", (e) => {
      if (docv.id != null && docv.dirty) { e.preventDefault(); e.returnValue = ""; }
    });
    $("#docTitle").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#docTitle").blur(); } });
    $("#docTitle").addEventListener("change", async () => {
      const v = $("#docTitle").value.trim();
      if (!v || !docv.doc || v === docv.doc.title) { $("#docTitle").value = docv.doc ? docv.doc.title : ""; return; }
      try {
        const res = await api(`/api/docs/${docv.id}`, { method: "PATCH", body: JSON.stringify({ title: v }) });
        docv.doc = res.doc;
        docv.base = res.doc.updated_at;
        $("#docTitle").value = res.doc.title;
        docState("标题已改");
      } catch (err) { toast(`改标题失败：${err.message}`); $("#docTitle").value = docv.doc.title; }
    });
    $("#docHistory").addEventListener("click", showDocHistory);
    $("#docHistClose").addEventListener("click", () => { $("#docHist").hidden = true; });
    $("#docDelete").addEventListener("click", async () => {
      if (!confirm(`删掉《${docv.doc.title}》？\n文件会从文件夹里删除，历史版本也一并清掉，无法恢复。`)) return;
      try {
        await api(`/api/docs/${docv.id}`, { method: "DELETE" });
        docv.dirty = false;
        await closeDoc();
        toast("已删除");
      } catch (err) { toast(`删除失败：${err.message}`); }
    });
    document.addEventListener("keydown", (e) => {
      if ($("#docRoot").hidden) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); saveDoc(true); }
      if (e.key === "Escape" && document.activeElement !== $("#docText")) closeDoc();
    });
    window.addEventListener("beforeunload", () => { if (docv.dirty) saveDoc(true); });
  }

  async function init() {
    wire();
    wireSide();
    wireDoc();
    wireNotify();
    autosize();
    updateSendState();
    try {
      const boot = await api("/api/bootstrap");
      applySettings(boot.settings);
      state.now = boot.now;
      state.defaultPersona = boot.default_persona || boot.settings.system_prompt || "";
      state.clockOffset = new Date(`${boot.now.datetime.replace(" ", "T")}`).getTime() - Date.now();
      tickClock();
      setInterval(tickClock, 15000);
      renderStats(boot.stats);

      const data = await api("/api/messages");
      state.messages = data.messages || [];
      renderAll();

      // 日历：先渲染，再补心情；然后检查有没有要提醒的任务
      loadCalendar(0);
      checkReminders();

      if (boot.settings.echo_mode) {
        el.hint.textContent = "离线演示模式：填好 API Key 后即可真实对话";
      }
      takePendingQuote();
      startLife();
      openStream();                       // 连发：她的回复、正在输入从这里推过来
      refreshSide();
    } catch (err) {
      appendError(`初始化失败：${err.message}`);
    }
  }

  init();
})();
