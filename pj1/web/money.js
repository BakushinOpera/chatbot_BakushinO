/* 零花钱账本：本月余额、本月收入 / 支出、分类汇总、完整的明细（包括她没跟你说过的）。
 * 数据来自 /api/money。金额的单位来自后端（d.unit，默认「块」），这里显示一位小数。
 * 她不会觉得你知道这些有什么奇怪：聊天时你提起，她正常接话。
 */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const ROOT = $("#moneyRoot");
  if (!ROOT) return;

  const S = { data: null, month: "", onlyUnshared: false, loading: false };

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
  const fmt = (b) => (b / 10).toFixed(1);
  const signed = (b) => `${b > 0 ? "+" : b < 0 ? "−" : ""}${fmt(Math.abs(b))}`;
  const monthText = (ym) => `${ym.slice(0, 4)} 年 ${Number(ym.slice(5))} 月`;
  const dayText = (d) => `${Number(d.slice(5, 7))} 月 ${Number(d.slice(8))} 日`;

  // 每一类一个淡淡的颜色
  const COLORS = { milktea: "#efb3aa", snack: "#f5d792", clothes: "#c9a6d4", accessory: "#e8aec6", game_buy: "#9dbbe0", iap: "#86b5c9",
    trinket: "#b4cd9b", takeout: "#f2bd8a", dine: "#e5a08c", grant: "#a9c99a", adjust: "#c9bfb0", expire: "#c9bfb0" };
  const colorOf = (k) => COLORS[k] || "#d6c8b8";

  let stateTimer = null;
  function setState(text, bad) {
    const n = $("#moneyState");
    n.textContent = text;
    n.classList.toggle("is-bad", !!bad);
    clearTimeout(stateTimer);
    if (text && !bad && !text.endsWith("…")) stateTimer = setTimeout(() => { n.textContent = ""; }, 2400);
  }

  /* ───────── 左边：余额、本月收支、分类汇总 ───────── */
  function sidePanel(d) {
    const s = d.summary;
    const side = el("aside", "mp-side");

    const bal = el("section", "mp-card mp-balance");
    bal.appendChild(el("div", "mp-card__label", "现在的余额"));
    const num = el("div", "mp-balance__num", fmt(d.balance));
    num.appendChild(el("small", null, " " + (d.unit || "块")));
    bal.appendChild(num);
    const row = el("div", "mp-balance__row");
    row.appendChild(el("span", `mp-tier mp-tier--${d.tier}`, d.tier_label));
    const ng = d.next_grant;
    row.appendChild(el("span", "mp-balance__sub", `${dayText(ng)}发 ${d.monthly.toFixed(1)} ${d.unit || "块"}`));
    bal.appendChild(row);
    side.appendChild(bal);

    const stats = el("section", "mp-stats");
    [["本月收入", `+${fmt(s.income)}`, "in"], ["本月支出", `−${fmt(s.spent)}`, "out"], ["本月笔数", String(s.count), ""]].forEach(([label, v, cls]) => {
      const t = el("div", "mp-card mp-stat");
      t.appendChild(el("div", "mp-card__label", label));
      t.appendChild(el("div", `mp-stat__v ${cls ? `is-${cls}` : ""}`, v));
      stats.appendChild(t);
    });
    side.appendChild(stats);

    const cats = el("section", "mp-card mp-cats");
    cats.appendChild(el("div", "mp-card__label", `${monthText(d.month)} · 分类汇总`));
    if (!s.by_category.length) cats.appendChild(el("div", "mp-empty", "这个月还没有消费。"));
    s.by_category.forEach((c) => {
      const r = el("div", "mp-cat");
      const top = el("div", "mp-cat__top");
      const dot = el("i", "mp-dot");
      dot.style.background = colorOf(c.key);
      top.append(dot, el("span", "mp-cat__name", c.label), el("span", "mp-cat__n", `${c.count} 笔`), el("span", "mp-cat__amt", fmt(c.amount)), el("span", "mp-cat__pct", `${c.percent}%`));
      const bar = el("div", "mp-bar");
      const fill = el("div", "mp-bar__fill");
      fill.style.width = `${Math.max(2, c.percent)}%`;
      fill.style.background = colorOf(c.key);
      bar.appendChild(fill);
      r.append(top, bar);
      cats.appendChild(r);
    });
    side.appendChild(cats);
    return side;
  }

  /* ───────── 右边：明细 ───────── */
  function entryRow(e) {
    const r = el("div", `mp-row mp-row--${e.kind}`);
    r.appendChild(el("span", "mp-row__time", e.time));
    const main = el("div", "mp-row__main");
    const top = el("div", "mp-row__top");
    top.appendChild(el("span", "mp-row__item", e.item));
    const pill = el("span", "mp-pill", e.category_label);
    pill.style.setProperty("--pc", colorOf(e.category || e.kind));
    top.appendChild(pill);
    if (e.room_item || e.room_book) top.appendChild(el("span", "mp-tag", e.room_book ? "上了书架" : "放进了房间"));
    if (e.kind === "spend") top.appendChild(el("span", e.shared ? "mp-tag is-shared" : "mp-tag is-hidden", e.shared ? "告诉你了" : "没告诉你"));
    main.appendChild(top);
    if (e.reason) main.appendChild(el("div", "mp-row__reason", `「${e.reason}」`));
    r.appendChild(main);
    r.appendChild(el("span", `mp-row__amt ${e.delta > 0 ? "is-in" : "is-out"}`, signed(e.delta)));
    return r;
  }

  function listPanel(d) {
    const box = el("section", "mp-list");
    const head = el("div", "mp-list__head");
    let entries = d.entries;
    head.appendChild(el("span", "mp-list__title", `明细 · ${entries.length} 条`));
    const lab = el("label", "mp-filter");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = S.onlyUnshared;
    cb.addEventListener("change", () => { S.onlyUnshared = cb.checked; render(); });
    lab.append(cb, document.createTextNode(" 只看她没告诉你的"));
    head.appendChild(lab);
    box.appendChild(head);
    if (S.onlyUnshared) entries = entries.filter((e) => e.kind === "spend" && !e.shared);
    if (!entries.length) {
      box.appendChild(el("div", "mp-empty", S.onlyUnshared ? "她这个月的消费都跟你说过了。" : "这个月还没有记录。"));
      return box;
    }
    const days = new Map();
    entries.forEach((e) => { if (!days.has(e.day)) days.set(e.day, []); days.get(e.day).push(e); });
    days.forEach((list, day) => {
      const sec = el("div", "mp-day");
      const spent = list.filter((e) => e.kind === "spend").reduce((a, e) => a - e.delta, 0);
      const h = el("div", "mp-day__head");
      h.appendChild(el("span", null, dayText(day)));
      if (spent) h.appendChild(el("span", "mp-day__sum", `支出 ${fmt(spent)}`));
      sec.appendChild(h);
      list.forEach((e) => sec.appendChild(entryRow(e)));
      box.appendChild(sec);
    });
    return box;
  }

  /* ───────── 页面 ───────── */
  function render() {
    const main = $("#moneyMain");
    main.innerHTML = "";
    const d = S.data;
    if (!d) return;
    if (!d.enabled) {
      main.appendChild(el("p", "rm-hint", "零花钱现在是关着的（设置 → 零花钱 → 启用零花钱）。下面是之前留下的记录。"));
    }
    const grid = el("div", "mp-grid");
    grid.append(sidePanel(d), listPanel(d));
    main.appendChild(grid);
    $("#moneyMonth").textContent = monthText(d.month);
    const ms = d.months;
    const i = ms.indexOf(d.month);
    $("#moneyPrev").disabled = i < 0 || i >= ms.length - 1;
    $("#moneyNext").disabled = i <= 0;
  }

  async function load(month) {
    S.loading = true;
    try {
      S.data = await api(`/api/money${month ? `?month=${encodeURIComponent(month)}` : ""}`);
      S.month = S.data.month;
      render();
    } catch (e) {
      setState(`读取失败：${e.message}`, true);
    } finally {
      S.loading = false;
    }
  }

  function shift(dir) {
    const ms = S.data.months;
    const i = ms.indexOf(S.data.month);
    const j = i - dir;                                  // months 是新 → 旧：往前一个月 = 下标 +1
    if (j >= 0 && j < ms.length) load(ms[j]);
  }

  /* ───────── 调整余额 ───────── */
  function openAdjust() {
    const body = $("#moneyPopBody");
    body.innerHTML = "";
    const box = el("div", "rm-card rm-card--edit");
    const form = el("div", "rm-form");
    const amount = el("input", "input input--tiny");
    amount.type = "number";
    amount.step = "0.1";
    amount.placeholder = "比如 50（加）或 −20（扣）";
    const note = el("input", "input input--tiny");
    note.type = "text";
    note.maxLength = 40;
    note.placeholder = "备注，可以留空，比如：过年的红包";
    form.append(el("label", null, `金额（${(S.data && S.data.unit) || "块"}）`), amount, el("label", null, "备注"), note);
    box.appendChild(form);
    box.appendChild(el("p", "rm-hint", "正数 = 额外给她零花钱，负数 = 扣掉一笔。会记进账本，余额马上变化。"));
    const ops = el("div", "rm-form__ops");
    const ok = el("button", "btn btn--mini", "记一笔");
    ok.type = "button";
    const cancel = el("button", "btn btn--ghost btn--mini", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", closeAdjust);
    ok.addEventListener("click", async () => {
      const v = Number(amount.value);
      if (!amount.value || !Number.isFinite(v) || v === 0) { amount.focus(); return; }
      ok.disabled = true;
      try {
        S.data = (await api("/api/money/adjust", { method: "POST", body: JSON.stringify({ amount: v, note: note.value.trim() }) }));
        S.month = S.data.month;
        closeAdjust();
        setState("已记一笔");
        render();
      } catch (e) {
        ok.disabled = false;
        setState(e.message, true);
      }
    });
    ops.append(ok, cancel);
    box.appendChild(ops);
    body.appendChild(box);
    $("#moneyPop").hidden = false;
    setTimeout(() => amount.focus(), 30);
  }
  function closeAdjust() { $("#moneyPop").hidden = true; }

  function open() { ROOT.hidden = false; load(""); }
  function close() { closeAdjust(); ROOT.hidden = true; }

  $("#moneyBack").addEventListener("click", close);
  $("#moneyPrev").addEventListener("click", () => shift(-1));
  $("#moneyNext").addEventListener("click", () => shift(1));
  $("#moneyAdjust").addEventListener("click", () => { if (S.data) openAdjust(); });
  $("#moneyPopClose").addEventListener("click", closeAdjust);
  $("#moneyPop").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeAdjust(); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || ROOT.hidden) return;
    if (!$("#moneyPop").hidden) closeAdjust(); else close();
  });
  document.addEventListener("phal:money-changed", () => { if (!ROOT.hidden && !S.loading) load(S.month); });

  window.PhalMoney = { open, close, reload: () => load(S.month) };
})();
