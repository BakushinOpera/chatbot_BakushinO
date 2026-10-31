/* 姿势调试器（房间页面里按 Ctrl+Shift+P）：调 Phal 每个状态在画面里的位置、旋转角度、大小、镜像，两个视角分开调。
 * 打开时她停在选中的状态（不跟着生活状态换），关掉恢复。改了自动保存到数据目录（room_pose_tuning.json，接口 /api/room/pose-tuning）；
 * 调好以后让 Claude 把这些数值并进 manifest.json 当默认值。 */
(() => {
  "use strict";
  const ROOT = document.getElementById("roomRoot");
  if (!ROOT) return;
  const R = () => window.PhalRoom;
  const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const FIELDS = [
    ["dx", "左右", -80, 80, 0.5, 0, "cm"], ["dy", "上下", -80, 80, 0.5, 0, "cm"],
    ["rot", "旋转", -180, 180, 1, 0, "°"], ["scale", "大小", 0.5, 2, 0.01, 1, "×"],
  ];
  const VIEW_LABEL = { room: "房间视角", desk: "书桌视角" };
  const S = { el: null, key: "", variant: null, saveT: 0 };

  const tuning = () => R().getTuning();
  const curView = () => (R().view() === "desk" ? "desk" : "room");
  const fullKey = () => S.key + (Number.isInteger(S.variant) ? `#${S.variant}` : "");
  const valOf = (view) => ((tuning()[fullKey()] || {})[view] || {});

  function states() {
    const m = R().manifest(), st = (m && m.states) || {};
    return Object.entries(st).filter(([k, v]) => k !== "note" && v && typeof v === "object" && v.spot !== "away");
  }
  /** 这个状态（这种做法）用的是哪张图：坐姿 / 躺姿看 seat.anim，站着的看 anim，都没有就是站姿图。 */
  function animOf(key, variant) {
    const st = (R().manifest().states || {})[key] || {};
    const v = Number.isInteger(variant) && st.variants ? st.variants[variant] || {} : {};
    return (v.seat && v.seat.anim) || (st.variants ? "" : (st.seat && st.seat.anim)) || st.anim || "stand";
  }
  function variantLabel(v, i) { return v.seat ? (v.seat.spot === "desk_seat" ? "坐在书桌边" : v.seat.spot.startsWith("sofa") ? "坐在沙发上" : v.seat.spot) : i === 0 ? "原地站着" : `做法 ${i + 1}`; }

  function put(view, patch) {
    const all = JSON.parse(JSON.stringify(tuning())), k = fullKey();
    all[k] = all[k] || {};
    const cur = Object.assign({}, all[k][view] || {}, patch);
    FIELDS.forEach(([f, , , , , d]) => { if (cur[f] === d || cur[f] == null || Number.isNaN(cur[f])) delete cur[f]; });
    if (!cur.flip) delete cur.flip;
    if (Object.keys(cur).length) all[k][view] = cur; else delete all[k][view];
    if (!Object.keys(all[k]).length) delete all[k];
    R().setTuning(all);
    saveLater();
  }
  function saveLater() {
    clearTimeout(S.saveT);
    msg("保存中…");
    S.saveT = setTimeout(async () => {
      try {
        const r = await fetch("/api/room/pose-tuning", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tuning: tuning() }) });
        if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
        msg("已保存");
      } catch (e) { msg(`保存失败：${e.message}`); }
    }, 400);
  }
  const msg = (t) => { const n = S.el && S.el.querySelector(".ptune__msg"); if (n) n.textContent = t; };

  function lock() { R().lockPose(S.key, Number.isInteger(S.variant) ? S.variant : undefined); setTimeout(fill, 60); }

  function open() {
    if (S.el) return;
    const sts = states();
    if (!sts.length) return;
    const now = R().actor() && R().actor().state;
    S.key = (now && now.state && sts.some(([k]) => k === now.state)) ? now.state : sts[0][0];
    S.variant = null;
    S.el = h("div", "ptune");
    S.el.innerHTML = `<div class="ptune__head"><b>姿势调试器</b><span class="ptune__msg"></span><button type="button" class="ptune__x" title="关闭（Ctrl+Shift+P）">×</button></div>
      <div class="ptune__row"><label>状态</label><select class="ptune__state"></select></div>
      <div class="ptune__row ptune__vrow"><label>做法</label><select class="ptune__variant"></select></div>
      <div class="ptune__row"><label>视角</label><span class="ptune__view"></span><button type="button" class="ptune__sw">换视角</button></div>
      <div class="ptune__anim"></div>
      <div class="ptune__fields"></div>
      <label class="ptune__chk"><input type="checkbox" class="ptune__flip"> 左右镜像</label>
      <div class="ptune__ops">
        <button type="button" class="ptune__reset">重置这个视角</button>
        <button type="button" class="ptune__same">套用到用同一张图的状态</button>
        <button type="button" class="ptune__copy">复制全部数值</button>
      </div>
      <p class="ptune__hint">拖滑块或直接填数。左右 / 上下按「厘米」算（离得远的地方一厘米的像素少）；旋转顺时针为正；大小在原来的基础上再乘。改了自动保存，调好以后告诉 Claude 配置成默认。</p>`;
    ROOT.appendChild(S.el);
    S.el.querySelector(".ptune__x").onclick = close;
    const sel = S.el.querySelector(".ptune__state");
    sts.forEach(([k, v]) => { const o = h("option", null, `${v.label || k}（${k}）`); o.value = k; sel.appendChild(o); });
    sel.value = S.key;
    sel.onchange = () => { S.key = sel.value; S.variant = null; syncVariants(); lock(); };
    S.el.querySelector(".ptune__variant").onchange = (e) => { S.variant = Number(e.target.value); lock(); };
    S.el.querySelector(".ptune__sw").onclick = () => { R().setView(curView() === "desk" ? "room" : "desk"); setTimeout(fill, 400); };
    const box = S.el.querySelector(".ptune__fields");
    FIELDS.forEach(([f, label, lo, hi, step, , unit]) => {
      const row = h("div", "ptune__f");
      row.appendChild(h("label", null, label));
      const rng = h("input"); rng.type = "range"; rng.min = lo; rng.max = hi; rng.step = step; rng.dataset.f = f;
      const num = h("input"); num.type = "number"; num.min = lo; num.max = hi; num.step = step; num.dataset.f = f;
      const on = (src, other) => () => { const v = Number(src.value); if (!Number.isFinite(v)) return; other.value = v; put(curView(), { [f]: v }); };
      rng.oninput = on(rng, num); num.onchange = on(num, rng);
      row.append(rng, num, h("span", "ptune__u", unit));
      box.appendChild(row);
    });
    S.el.querySelector(".ptune__flip").onchange = (e) => put(curView(), { flip: e.target.checked });
    S.el.querySelector(".ptune__reset").onclick = () => {
      const all = JSON.parse(JSON.stringify(tuning())), k = fullKey();
      if (all[k]) { delete all[k][curView()]; if (!Object.keys(all[k]).length) delete all[k]; }
      R().setTuning(all); saveLater(); fill();
    };
    S.el.querySelector(".ptune__same").onclick = () => {
      const anim = animOf(S.key, S.variant), v = curView(), val = valOf(v), all = JSON.parse(JSON.stringify(tuning())), done = [];
      states().forEach(([k, st]) => {
        const keys = st.variants ? st.variants.map((_, i) => [`${k}#${i}`, animOf(k, i)]) : [[k, animOf(k)]];
        keys.forEach(([fk, a]) => { if (a === anim && fk !== fullKey()) { all[fk] = all[fk] || {}; if (Object.keys(val).length) all[fk][v] = Object.assign({}, val); else delete all[fk][v]; if (!Object.keys(all[fk]).length) delete all[fk]; done.push(fk); } });
      });
      R().setTuning(all); saveLater();
      msg(done.length ? `套用到：${done.join("、")}（${VIEW_LABEL[v]}）` : "没有别的状态用这张图");
    };
    S.el.querySelector(".ptune__copy").onclick = async () => {
      try { await navigator.clipboard.writeText(JSON.stringify(tuning(), null, 1)); msg("已复制"); } catch (_) { msg("复制失败"); }
    };
    syncVariants();
    lock();
  }
  function syncVariants() {
    const st = (R().manifest().states || {})[S.key] || {}, row = S.el.querySelector(".ptune__vrow"), vs = S.el.querySelector(".ptune__variant");
    vs.innerHTML = "";
    if (!st.variants || !st.variants.length) { row.style.display = "none"; S.variant = null; return; }
    row.style.display = "";
    st.variants.forEach((v, i) => { const o = h("option", null, variantLabel(v, i)); o.value = String(i); vs.appendChild(o); });
    if (!Number.isInteger(S.variant)) S.variant = 0;
    vs.value = String(S.variant);
  }
  function fill() {
    if (!S.el) return;
    const v = curView(), val = valOf(v);
    S.el.querySelector(".ptune__view").textContent = VIEW_LABEL[v];
    S.el.querySelector(".ptune__anim").textContent = `用的图：${animOf(S.key, S.variant)}　·　存成「${fullKey()}」`;
    S.el.querySelectorAll(".ptune__fields input").forEach((n) => { const f = FIELDS.find((x) => x[0] === n.dataset.f); n.value = val[n.dataset.f] != null ? val[n.dataset.f] : f[5]; });
    S.el.querySelector(".ptune__flip").checked = !!val.flip;
  }
  function close() {
    if (!S.el) return;
    clearTimeout(S.saveT);
    S.el.remove(); S.el = null;
    R().lockPose(null);
  }

  document.addEventListener("keydown", (e) => {
    if (ROOT.hidden || !(e.ctrlKey && e.shiftKey && e.code === "KeyP")) return;
    e.preventDefault();
    if (S.el) close(); else open();
  });
  window.PhalPoseTune = { open, close };
})();
