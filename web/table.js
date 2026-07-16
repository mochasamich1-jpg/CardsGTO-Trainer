"use strict";
const $ = (s) => document.querySelector(s);

// physical chair positions (percent of #table); seat 0 = hero, bottom center
const SEAT_POS = [
  { top: 88, left: 50 }, // 0 hero
  { top: 79, left: 24 }, // 1
  { top: 52, left: 8 },  // 2
  { top: 24, left: 14 }, // 3
  { top: 11, left: 34 }, // 4
  { top: 11, left: 66 }, // 5
  { top: 24, left: 86 }, // 6
  { top: 52, left: 92 }, // 7
  { top: 79, left: 76 }, // 8
];
const CENTER = { top: 44, left: 50 };
const ARCH_LABEL = { station: "Calling Station", rec: "Recreational", nit: "Nit", tag: "Reg / TAG", maniac: "Maniac", hero: "" };
const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };
const FOCUS = {
  "RFI":    { t: "Preflop opens",        c: "Open-raise the chart; don't limp, don't over-fold." },
  "R7-iso": { t: "Isolate limpers",      c: "Punish limps with big raises in position." },
  "vsRFI":  { t: "Facing opens",         c: "Value-lean your 3-bets against this pool." },
  "R1":     { t: "Big-bet discipline",   c: "The pool under-bluffs big turn/river bets. Overfold." },
  "R3":     { t: "Stop bluffing stations", c: "They don't fold. Shift those chips to value." },
  "R4":     { t: "Thin value sizing",    c: "Bet bigger with made hands vs sticky players." },
  "R5":     { t: "Value targets",        c: "Don't bet medium hands into nits." },
  "R13":    { t: "Multiway c-bets",      c: "Check air multiway — fold equity dies." },
  "R9":     { t: "Limp-reraise alarm",   c: "A limp-reraise is AA/KK. Get away." },
  "R10":    { t: "Passive aggression",   c: "A passive player's big bet is the nuts." },
  "R11":    { t: "Maniac defense",       c: "Call wider vs the maniac's barrels." },
  "R18":    { t: "Rake-aware set-mining", c: "Skip OOP small-pair calls under $6 rake." },
};

let view = null, rendered = 0, busy = false, rep = null, curOpts = null;
let lastCompleted = null, reviewTimer = null, replayGen = 0;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function req(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
  const ct = r.headers.get("content-type") || "";
  if (!ct.includes("application/json")) throw new Error(`${path} -> non-JSON response`);
  return r.json();
}
async function post(path, body) {
  return req(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : null });
}
async function get(path) { return req(path); }

// on any API hiccup, re-pull the authoritative server state instead of soft-locking
async function resync() {
  try {
    const v = await get("/api/state");
    if (v && v.seats && v.seats.length) await handleView(v, true, true);
    else showIdle();
  } catch (e) {
    console.error("resync failed:", e);
    showIdle();
  }
}

// ---------- rendering primitives ----------
function cardEl(code, small, deal) {
  const d = document.createElement("div");
  d.className = "card" + (small ? " small" : "") + (deal ? " deal" : "");
  if (!code) { d.classList.add("back"); return d; }
  const r = code[0], s = code[1];
  d.classList.add(s === "h" || s === "d" ? "red" : "black");
  const rank = r === "T" ? "10" : r;
  d.innerHTML = `<div class="idx"><span class="r">${rank}</span><span class="s">${SUIT[s]}</span></div><div class="big">${SUIT[s]}</div>`;
  return d;
}
function cardSpan(code) {
  const red = code[1] === "h" || code[1] === "d";
  const rank = code[0] === "T" ? "10" : code[0];
  return `<span class="c${red ? " red" : ""}">${rank}${SUIT[code[1]]}</span>`;
}

function newRep() {
  return { board: [], streetBets: {}, folded: new Set(), lastLabel: null, actor: null,
           revealed: {}, winners: [], winAmt: {}, pot: 0, freshBoard: 0 };
}

function seatName(seat) {
  const s = view && view.seats.find((x) => x.seat === seat);
  return s ? s.name : `Seat ${seat}`;
}

// ---------- hand feed ----------
function feedLine(html, cls) {
  const el = document.createElement("div");
  el.className = "feed-line" + (cls ? " " + cls : "");
  el.innerHTML = html;
  const feed = $("#feed");
  feed.appendChild(el);
  feed.scrollTop = feed.scrollHeight;
}
function feedEvent(ev) {
  if (ev.type === "blinds") {
    feedLine(`HAND #${String(view.hand_number).padStart(6, "0")}`, "hand-head");
    feedLine(`<b>${seatName(ev.sb_seat)}</b> posts $${ev.sb} · <b>${seatName(ev.bb_seat)}</b> posts $${ev.bb}`);
  } else if (ev.type === "board") {
    feedLine(`<b>${ev.street.toUpperCase()}</b> — ${ev.cards.map(cardSpan).join(" ")} <span style="float:right">$${ev.pot}</span>`, "street-head");
  } else if (ev.type === "action") {
    const who = ev.is_hero ? `<b>You</b>` : `<b>${ev.name}</b>`;
    let what = ev.action;
    if (ev.action === "call") what = `calls $${ev.committed}`;
    else if (ev.action === "check") what = "checks";
    else if (ev.action === "fold") what = "folds";
    else if (ev.action === "bet") what = `bets $${ev.to}`;
    else if (ev.action === "raise") what = `raises to $${ev.to}`;
    feedLine(`${who} ${what}`, ev.is_hero ? "hero-line" : "");
  } else if (ev.type === "showdown" || ev.type === "win") {
    const names = ev.winner_names.join(", ");
    const rake = ev.rake ? ` · rake $${ev.rake}` : "";
    feedLine(`<b>${names}</b> win${ev.winners.length > 1 ? "" : "s"} $${ev.pot - (ev.rake || 0)}${rake}`, "result");
  }
}

// ---------- replay state machine ----------
function step(ev, silent) {
  if (ev.type === "blinds") {
    rep.streetBets = {}; rep.streetBets[ev.sb_seat] = ev.sb; rep.streetBets[ev.bb_seat] = ev.bb;
    rep.pot = ev.pot;
  } else if (ev.type === "board") {
    rep.freshBoard = ev.cards.length;
    rep.board = rep.board.concat(ev.cards); rep.streetBets = {}; rep.pot = ev.pot; rep.lastLabel = null;
  } else if (ev.type === "action") {
    rep.streetBets[ev.seat] = ev.to; rep.pot = ev.pot; rep.actor = ev.seat;
    rep.lastLabel = { seat: ev.seat, action: ev.action, committed: ev.committed, to: ev.to };
    if (ev.action === "fold") rep.folded.add(ev.seat);
  } else if (ev.type === "showdown" || ev.type === "win") {
    rep.revealed = ev.revealed || {}; rep.winners = ev.winners || []; rep.actor = null;
    rep.winAmt = ev.payoffs || {};
    rep.pot = 0; rep.streetBets = {}; rep.lastLabel = null;
  }
  if (!silent) feedEvent(ev);
}

function tagText(l) {
  if (l.action === "fold") return "Fold";
  if (l.action === "check") return "Checks";
  if (l.action === "call") return `Calls $${l.committed}`;
  if (l.action === "bet") return `Bets $${l.to}`;
  return `Raises to $${l.to}`;
}

function draw() {
  // board slots
  const bd = $("#board"); bd.innerHTML = "";
  for (let i = 0; i < 5; i++) {
    if (i < rep.board.length) {
      const fresh = i >= rep.board.length - rep.freshBoard;
      bd.appendChild(cardEl(rep.board[i], false, fresh));
    } else {
      const sl = document.createElement("div"); sl.className = "slot"; bd.appendChild(sl);
    }
  }
  rep.freshBoard = 0;
  $("#potAmt").textContent = rep.pot > 0 ? `$${rep.pot}` : "";
  $("#street").textContent = view.hand_over ? "" :
    rep.board.length === 0 ? "Preflop" : rep.board.length === 3 ? "Flop" : rep.board.length === 4 ? "Turn" : "River";

  const table = $("#table");
  table.querySelectorAll(".pod, .bet-chip, .dealer-btn").forEach((p) => p.remove());

  view.seats.forEach((s) => {
    const pod = document.createElement("div");
    pod.className = "pod" + (s.is_hero ? " hero" : "");
    const p = SEAT_POS[s.seat];
    pod.style.top = p.top + "%"; pod.style.left = p.left + "%";
    const isFolded = rep.folded.has(s.seat);
    if (isFolded) pod.classList.add("folded");
    const acting = rep.actor === s.seat && !view.hand_over;
    if (acting) pod.classList.add("actor");
    const won = view.hand_over && rendered >= view.timeline.length && rep.winners.includes(s.seat);
    if (won) pod.classList.add("winner");

    // cards
    const cd = document.createElement("div"); cd.className = "cards";
    let cards = null;
    if (s.is_hero) cards = (view.hero_hole && view.hero_hole.length) ? view.hero_hole : null;
    else if (rep.revealed && rep.revealed[s.seat]) cards = rep.revealed[s.seat];
    if (cards) cards.forEach((c) => cd.appendChild(cardEl(c, !s.is_hero, false)));
    else { cd.appendChild(cardEl(null, true)); cd.appendChild(cardEl(null, true)); }
    pod.appendChild(cd);

    // plate
    const plate = document.createElement("div"); plate.className = "plate";
    const arch = s.archetype && s.archetype !== "hero" ? (ARCH_LABEL[s.archetype] || s.archetype) : "";
    const thinking = s.is_hero && view.awaiting_hero && rendered >= view.timeline.length;
    plate.innerHTML =
      `<div class="toprow"><span class="poschip">${s.pos}</span><span class="name">${s.name}</span></div>` +
      `<div class="stack"><b>$${s.stack}</b></div>` +
      `<div class="sub${thinking ? " think" : ""}">${thinking ? "Thinking" : arch}</div>`;
    pod.appendChild(plate);

    // action tag under the pod
    let tag = null;
    if (won) tag = { text: rep.winAmt[s.seat] > 0 ? `WINS +$${rep.winAmt[s.seat]}` : "WINS", cls: "win" };
    else if (rep.lastLabel && rep.lastLabel.seat === s.seat)
      tag = { text: tagText(rep.lastLabel), cls: rep.lastLabel.action === "fold" ? "fold" : (rep.lastLabel.action === "raise" || rep.lastLabel.action === "bet") ? "aggro" : "" };
    else if (isFolded) tag = { text: "Fold", cls: "fold" };
    if (tag) {
      const t = document.createElement("div"); t.className = "action-tag " + tag.cls; t.textContent = tag.text;
      pod.appendChild(t);
    }
    table.appendChild(pod);

    // bet chip, lerped toward table center
    const bet = rep.streetBets[s.seat] || 0;
    if (bet > 0 && !isFolded) {
      const b = document.createElement("div"); b.className = "bet-chip"; b.textContent = `$${bet}`;
      b.style.top = (p.top + (CENTER.top - p.top) * 0.36) + "%";
      b.style.left = (p.left + (CENTER.left - p.left) * 0.30) + "%";
      table.appendChild(b);
    }

    // dealer button between pod and center
    if (s.is_button) {
      const d = document.createElement("div"); d.className = "dealer-btn"; d.textContent = "D";
      d.style.top = (p.top + (CENTER.top - p.top) * 0.22) + "%";
      d.style.left = (p.left + (CENTER.left - p.left) * 0.16 + 4) + "%";
      table.appendChild(d);
    }
  });
}

// ---------- flow ----------
function delayFor(ev) {
  return ev.type === "action" ? 380 : ev.type === "board" ? 560 : (ev.type === "showdown" || ev.type === "win") ? 700 : 140;
}

async function replay(gen, instant) {
  const tl = view.timeline;
  for (; rendered < tl.length; rendered++) {
    if (gen !== replayGen) return false;   // a newer hand took over
    step(tl[rendered], false);
    draw();
    if (!instant) await sleep(delayFor(tl[rendered]));
    if (gen !== replayGen) return false;
  }
  return true;
}

function fmtMoney(n) { return (n < 0 ? "-$" : "$") + Math.abs(n); }

function updateHeader(v) {
  $("#handTag").textContent = v.hand_number ? `HAND #${String(v.hand_number).padStart(6, "0")}` : "HAND #—";
  const hands = v.hands_completed || 0;
  $("#mHands").textContent = hands;
  const secs = v.session_seconds || 0;
  $("#mRate").textContent = hands >= 2 && secs > 30 ? Math.round(hands / (secs / 3600)) : "—";
  const net = v.hero_session_net || 0;
  const netEl = $("#mNet");
  netEl.textContent = fmtMoney(net);
  netEl.className = net > 0 ? "pos" : net < 0 ? "neg" : "";
  $("#mBB").textContent = hands > 0 ? ((net / 2) / hands * 100).toFixed(0) : "—";
  const st = v.stats || {};
  $("#mClean").textContent = st.reviewed ? Math.round(100 * st.clean / st.reviewed) + "%" : "—";

  // sidebar: session block progress
  $("#sessHands").textContent = `${hands % 50} / 50`;
  $("#sessBar").style.width = (hands % 50) * 2 + "%";
  $("#sessCap").textContent = `block ${Math.floor(hands / 50) + 1} · ${fmtMoney(net)} net`;

  // sidebar: focus = most-flagged leak
  const counts = st.leak_counts || {};
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  if (top && FOCUS[top[0]]) {
    $("#focusTitle").textContent = FOCUS[top[0]].t;
    $("#focusCap").textContent = `${FOCUS[top[0]].c} (flagged ${top[1]}×)`;
  } else if (st.reviewed) {
    $("#focusTitle").textContent = "No recurring leaks";
    $("#focusCap").textContent = "Keep taking the exploit lines.";
  }
}

async function handleView(v, reset, instant) {
  view = v;
  if (reset) {
    replayGen++;
    rendered = 0; rep = newRep();
    hideReview();
  }
  const gen = replayGen;
  updateHeader(v);
  hideBars();
  const finished = await replay(gen, instant);
  if (!finished) return;                    // superseded by a newer hand
  if (v.awaiting_hero) {
    rep.actor = v.hero_seat; draw();
    showDecision(v.hero_options);
  } else if (v.hand_over) {
    lastCompleted = v;
    draw();
    showIdle();
    clearTimeout(reviewTimer);
    reviewTimer = setTimeout(() => { if (gen === replayGen) showReview(v); }, 500);
  }
}

// ---------- action bar states ----------
function hideBars() {
  ["#bar-idle", "#bar-decision", "#bar-sizing"].forEach((s) => $(s).classList.add("hidden"));
}
function showIdle() {
  hideBars();
  $("#bar-idle").classList.remove("hidden");
  $("#idleMsg").textContent = view && view.hand_number ? "Deal the next hand" : "Deal to start the session";
  $("#reviewBtn").classList.toggle("hidden", !lastCompleted);
}
function showDecision(o) {
  curOpts = o;
  hideBars();
  $("#bar-decision").classList.remove("hidden");
  $("#decLabel").textContent = `Your decision · ${view.street}`;
  $("#decMain").textContent = o.can_check ? "Check or bet" : `$${o.to_call} to call · pot $${o.pot}`;
  const foldB = $("#foldBtn"), callB = $("#callBtn"), raiseB = $("#raiseBtn");
  foldB.style.display = o.can_fold ? "" : "none";
  callB.textContent = o.can_check ? "Check" : `Call $${o.to_call}`;
  raiseB.textContent = o.to_call > 0 ? "Raise" : "Bet";
  raiseB.style.display = o.can_raise ? "" : "none";
}
function showSizing() {
  const o = curOpts; if (!o || !o.can_raise) return;
  hideBars();
  $("#bar-sizing").classList.remove("hidden");
  const sl = $("#raiseSlider");
  const lo = o.min_raise_to ?? o.max_raise_to, hi = o.max_raise_to ?? lo;
  sl.min = Math.min(lo, hi); sl.max = hi; sl.step = 1; sl.value = Math.min(lo, hi);
  updateRaiseAmt();
}
function updateRaiseAmt() {
  const v = parseInt($("#raiseSlider").value, 10) || 0;
  $("#raiseAmt").textContent = "$" + v;
  $("#confirmRaise").textContent = v >= (curOpts.max_raise_to ?? Infinity) ? "All-in" : "Confirm";
}
function presetTo(frac) {
  const o = curOpts;
  if (frac === "allin") return o.max_raise_to;
  const target = Math.round(frac * o.pot) + o.to_call;
  return Math.max(o.min_raise_to ?? target, Math.min(o.max_raise_to ?? target, target));
}

// ---------- server calls ----------
async function act(action, to) {
  if (busy) return; busy = true; hideBars();
  try {
    const v = await post("/api/action", { action, to });
    await handleView(v, false, false);
  } catch (e) {
    console.error(e); await resync();
  } finally { busy = false; }
}
async function newHand() {
  if (busy) return; busy = true;
  clearTimeout(reviewTimer);
  hideReview();
  try {
    const v = await post("/api/new-hand");
    await handleView(v, true, false);
  } catch (e) {
    console.error(e); await resync();
  } finally { busy = false; }
}

// ---------- review panel ----------
function hideReview() {
  $("#reviewBlock").classList.add("hidden");
  $("#reviewLock").classList.remove("hidden");
}
function showReview(v) {
  const rv = v.review; if (!rv) return;
  $("#reviewLock").classList.add("hidden");
  $("#reviewBlock").classList.remove("hidden");
  $("#reviewSummary").textContent = rv.summary;
  const ul = $("#reviewFindings"); ul.innerHTML = "";
  if (!rv.findings.length) {
    const li = document.createElement("li"); li.className = "finding info";
    li.textContent = "No specific coaching notes — standard spot.";
    ul.appendChild(li);
  }
  rv.findings.forEach((f) => {
    const li = document.createElement("li"); li.className = "finding " + f.kind;
    li.innerHTML = `<span class="tag">${f.rule} · ${f.street}${f.ev ? " · " + f.ev : ""}</span>${f.text}`;
    ul.appendChild(li);
  });
}

// ---------- boot: resume whatever the server is doing ----------
async function boot() {
  let v = null;
  try { v = await get("/api/state"); } catch (e) { /* server starting up */ }
  if (v && v.seats && v.seats.length) {
    // resume mid-session: fast-forward the whole timeline with no animation
    await handleView(v, true, true);
    if (v.hand_over && v.review) { lastCompleted = v; showReview(v); }
  } else {
    showIdle();
  }
}

// ---------- wire up ----------
window.addEventListener("DOMContentLoaded", () => {
  $("#dealBtn").addEventListener("click", newHand);
  $("#reviewBtn").addEventListener("click", () => { if (lastCompleted) showReview(lastCompleted); });
  $("#foldBtn").addEventListener("click", () => act("fold"));
  $("#callBtn").addEventListener("click", () => curOpts && act(curOpts.can_check ? "check" : "call"));
  $("#raiseBtn").addEventListener("click", showSizing);
  $("#cancelRaise").addEventListener("click", () => showDecision(curOpts));
  $("#confirmRaise").addEventListener("click", () => act("raise", parseInt($("#raiseSlider").value, 10)));
  $("#raiseSlider").addEventListener("input", updateRaiseAmt);
  document.querySelectorAll(".sizepresets button").forEach((b) => {
    b.addEventListener("click", () => {
      const f = b.dataset.frac === "allin" ? "allin" : parseFloat(b.dataset.frac);
      $("#raiseSlider").value = presetTo(f); updateRaiseAmt();
    });
  });
  $("#resetBtn").addEventListener("click", async () => {
    if (busy) return;
    await post("/api/reset"); location.reload();
  });

  // keyboard shortcuts
  window.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    const dec = !$("#bar-decision").classList.contains("hidden");
    const siz = !$("#bar-sizing").classList.contains("hidden");
    const idle = !$("#bar-idle").classList.contains("hidden");
    const k = e.key.toLowerCase();
    if (siz) {
      if (k === "enter") $("#confirmRaise").click();
      else if (k === "escape") $("#cancelRaise").click();
    } else if (dec) {
      if (k === "f" && curOpts.can_fold) $("#foldBtn").click();
      else if (k === "c") $("#callBtn").click();
      else if (k === "r" && curOpts.can_raise) $("#raiseBtn").click();
    } else if (idle) {
      if (k === "enter" || k === "d") $("#dealBtn").click();
    }
  });

  boot();
});
