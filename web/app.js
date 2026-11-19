/* Hit Em High Fantasy (Fantasy Manager NBA): frontend (vanilla JS, geen build-stap) */
"use strict";

const S = { state: null, live: null, news: null, cache: { player: {} }, ui: {} };
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = v => typeof v === "number" && isFinite(v);
const f0 = v => num(v) ? Math.round(v).toLocaleString("nl-NL") : "–";
const f1 = v => num(v) ? v.toFixed(1) : "–";
const f2 = v => num(v) ? v.toFixed(2) : "–";
const sg = (v, d = 1) => num(v) ? (v > 0 ? "+" : "") + v.toFixed(d) : "–";
const pc = (v, d = 0) => num(v) ? (v * 100).toFixed(d) + "%" : "–";
const POSITIONS = ["Alle", "PG", "SG", "SF", "PF", "C"];
const BENCH = ["Bank", "IR"];
const WINDOWS = [["ros", "Verwacht"], ["season", "Seizoen"], ["l7", "L7"], ["l15", "L15"], ["l30", "L30"], ["last", "Vorig seizoen"]];
const WIN_L = Object.fromEntries(WINDOWS);
const STAT_L = { "0": "PTS", "1": "BLK", "2": "STL", "3": "AST", "6": "REB", "11": "TO", "17": "3PM", "13": "FGM", "14": "FGA", "15": "FTM", "16": "FTA", "40": "MIN", "4": "OREB", "5": "DREB", "37": "DD" };

/* ---------------- opslag, toegang & team ---------------- */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { } },
};
const AUTH = { code: store.get("fmb-code") || "", team: null, teams: null, needsCode: false };
(() => {
  const q = new URLSearchParams(location.search);
  if (q.get("team")) AUTH.team = +q.get("team");
  else if (store.get("fmb-team")) AUTH.team = +store.get("fmb-team");
  try { S.ui.punt = new Set(JSON.parse(store.get("fmb-punt") || "[]")); } catch (e) { S.ui.punt = new Set(); }
  S.ui.win = store.get("fmb-win") || "ros";
})();
class AuthError extends Error { }

/* ---------------- API ---------------- */
async function api(path, opts = {}) {
  const sep = path.includes("?") ? "&" : "?";
  const url = path.startsWith("/api/") && AUTH.team ? `${path}${sep}team=${AUTH.team}` : path;
  const headers = Object.assign({}, opts.headers || {}, AUTH.code ? { "X-Access-Code": AUTH.code } : {});
  const r = await fetch(url, Object.assign({}, opts, { headers }));
  if (r.status === 401) { showGate("code", "De toegangscode klopt niet (meer)."); throw new AuthError("geen toegang"); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}
async function loadState() {
  if (!AUTH.team) return;
  try {
    const s = await api("/api/state");
    if (s.status !== "ok") { setStatus("Data wordt geladen…"); setTimeout(loadState, 3000); return; }
    const first = !S.state;
    S.state = s;
    if (first) { loadLive(); loadNews(); }
    renderChrome();
    if (first || ["overzicht", "selectie", "markt", "categorieen"].includes(route().name)) render();
  } catch (e) { if (e instanceof AuthError) return; setStatus("Server niet bereikbaar: " + e.message); setTimeout(loadState, 5000); }
}
async function loadLive() {
  if (!S.state) return;
  try {
    S.live = await api("/api/live");
    if (S.live.matchup) S.state.matchup = S.live.matchup;
    renderChrome();
    const r = route().name;
    if (r === "live" || r === "overzicht") renderLiveParts();
  } catch (e) { /* stil */ }
}
async function loadNews() {
  if (!S.state) return;
  try { S.news = await api("/api/news"); if (route().name === "nieuws") render(); } catch (e) { }
}
function setStatus(t) { $("#statusText").textContent = t; }
setInterval(loadState, 120000);
setInterval(() => loadLive(), 20000);
setInterval(loadNews, 120000);

/* ---------------- tooltip & thema ---------------- */
const tip = $("#tip");
document.addEventListener("mouseover", e => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tip.style.opacity = 0; return; }
  tip.textContent = t.dataset.tip; tip.style.opacity = 1;
});
document.addEventListener("mousemove", e => {
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(e.clientX + 14, innerWidth - w - 8)) + "px";
  tip.style.top = (e.clientY - h - 10 < 8 ? e.clientY + 18 : e.clientY - h - 10) + "px";
});
(() => {
  const saved = store.get("fmb-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; store.set("fmb-theme", next);
  };
  $("#refreshBtn").onclick = async () => {
    setStatus("Verversen…"); S.cache = { player: {} };
    const r = await api("/api/refresh", { method: "POST" }).catch(() => ({}));
    if (r && r.msg) { setStatus(r.msg); return; }
    setTimeout(() => { loadState(); loadLive(); loadNews(); }, 6000);
  };
})();

/* ---------------- helpers ---------------- */
const P = id => S.state.players[id];
const myRoster = () => S.state.roster.map(P).filter(Boolean);
const oppRoster = () => S.state.oppRoster.map(P).filter(Boolean);
const CATS = () => S.state.meta.cats;
const CAT = id => CATS().find(c => c.id === id) || { id, abbr: STAT_L[id] || id, name: id };
const short = n => (n || "").replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/i, "").split(" ").slice(-1)[0];
const isStarter = p => p.slot && !BENCH.includes(p.slot);
const isPct = c => ["19", "20", "21"].includes(c);

/* waarde van een categorie per wedstrijd in een periode */
function pg(p, w) { return w === "ros" ? p.ppg : (p.pg[w] && p.pg[w].s); }
function gp(p, w) { return w === "ros" ? null : (p.pg[w] ? p.pg[w].gp : 0); }
function catVal(stats, c) {
  if (!stats) return null;
  const m = CAT(c);
  if (m.ratio) { const d = stats[m.ratio[1]]; return d ? (stats[m.ratio[0]] || 0) / d : null; }
  return stats[c] ?? 0;
}
function fmtCat(c, v, total) {
  if (!num(v)) return "–";
  if (isPct(c)) return (v * 100).toFixed(1) + "%";
  if (CAT(c).ratio) return v.toFixed(2);
  return total ? f0(v) : f1(v);
}
/* totaalwaarde (som van z-scores) zonder gepunte categorieën */
function value(p, w = S.ui.win) {
  const z = p.z && p.z[w]; if (!z) return null;
  let s = 0; for (const c of CATS()) if (!S.ui.punt.has(c.id) && num(z[c.id])) s += z[c.id];
  return s;
}
function zCell(z, txt) {
  if (!num(z)) return `<span class="zc">${txt}</span>`;
  const cls = z >= 1.5 ? "z4" : z >= 0.5 ? "z3" : z <= -1.5 ? "z0" : z <= -0.5 ? "z1" : "z2";
  return `<span class="zc ${cls}" data-tip="z-score ${sg(z, 2)}">${txt}</span>`;
}

const INJ = {
  OUT: ["Out", "var(--critical)", "■", 4], INJURY_RESERVE: ["IR", "var(--critical)", "■", 4],
  SUSPENSION: ["Geschorst", "var(--critical)", "■", 4], DAY_TO_DAY: ["Dag-tot-dag", "var(--warning)", "▲", 2],
  QUESTIONABLE: ["Twijfelachtig", "var(--warning)", "▲", 2], DOUBTFUL: ["Onzeker", "var(--serious)", "▲", 3],
  INJURED: ["Geblesseerd", "var(--serious)", "▲", 3],
};
const isOut = p => INJ[p.injury] && INJ[p.injury][3] >= 4;
function inj(p) {
  const i = INJ[p.injury]; if (!i) return "";
  return `<span class="inj" data-tip="Blessurestatus ESPN: ${esc(p.injury)}"><span style="color:${i[1]}">${i[2]}</span>${i[0]}</span>`;
}
function attr(score, lg) {
  if (!num(score)) return `<span class="attr na${lg ? " lg" : ""}">–</span>`;
  const b = score <= 5 ? 1 : score <= 10 ? 2 : score <= 15 ? 3 : 4;
  return `<span class="attr b${b}${lg ? " lg" : ""}">${Math.round(score)}</span>`;
}
/* kwaliteit 1-20 uit de totaalwaarde (verwacht) */
function quality(p) { const v = p.value && p.value.ros; return num(v) ? Math.max(1, Math.min(20, Math.round(8 + v * 1.25))) : null; }
function valChip(v) {
  if (!num(v)) return `<span class="muted">–</span>`;
  const cls = v >= 4 ? "pos" : v <= 0 ? "neg" : "mid";
  return `<span class="grade ${cls}" data-tip="Waarde = som van de z-scores per categorie (${esc(WIN_L[S.ui.win])}${S.ui.punt.size ? ", zonder " + [...S.ui.punt].map(c => CAT(c).abbr).join("/") : ""})">${sg(v, 1)}</span>`;
}
function todayStr(p) {
  const t = p.today;
  if (!t) return `<span class="muted">geen wedstrijd</span>`;
  const vs = `${t.home ? "vs" : "@"} ${esc(t.opp)}`;
  if (t.state === "in") return `${vs} <span class="livepill">${esc(t.detail || "live")}</span>`;
  if (t.state === "post") return `${vs} <span class="muted">${esc(t.detail || "afgelopen")}</span>`;
  return `${vs} <span class="muted">${tm(t.date)}</span>`;
}
const tm = ms => ms ? new Date(ms).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" }) : "";
const dayL = ms => ms ? new Date(ms).toLocaleDateString("nl-NL", { weekday: "short", day: "numeric", month: "short" }) : "";
function form(p, n = 5) {
  const g = (p.form || []).slice(-n);
  if (!g.length) return `<span class="muted small">–</span>`;
  return `<span class="form">${g.map(x => {
    const c = !num(x.v) ? "mid" : x.v >= 3 ? "pos" : x.v <= -2 ? "neg" : "mid";
    return `<i class="${c}" data-tip="${esc(`${dayL(x.date)}${x.opp ? " vs " + x.opp : ""}: ${x.line} · waarde ${sg(x.v, 1)}`)}">${f0(x.pts)}</i>`;
  }).join("")}</span>`;
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 60) return "zojuist";
  if (s < 3600) return Math.round(s / 60) + " min";
  if (s < 86400) return Math.round(s / 3600) + " uur";
  if (s < 86400 * 7) return Math.round(s / 86400) + " d";
  return new Date(iso).toLocaleDateString("nl-NL", { day: "numeric", month: "short" });
}
function ownerLabel(p) {
  if (p.mine) return "<strong>Mijn team</strong>";
  if (p.oppTeam) return "<span class='muted'>Tegenstander</span>";
  if (p.onTeamId) { const t = S.state.fantasyTeams[p.onTeamId]; return esc(t ? t.abbrev : "Team " + p.onTeamId); }
  return p.status === "WAIVERS" ? "<span class='up'>Waivers</span>" : "<span class='up'>Vrij</span>";
}
const isFree = p => !p.mine && !p.oppTeam && !p.onTeamId;
/* Basketball Reference */
const ADV = [
  ["usg_pct", "USG%", "Usage: aandeel teambezit dat bij hem eindigt", v => f1(v)], ["ts_pct", "TS%", "True shooting % (incl. 3P en FT)", v => num(v) ? (v * 100).toFixed(1) : "–"],
  ["per", "PER", "Player Efficiency Rating (15 = gemiddeld)", v => f1(v)], ["bpm", "BPM", "Box Plus/Minus per 100 bezit t.o.v. gemiddelde", v => sg(v)],
  ["vorp", "VORP", "Value Over Replacement Player (seizoen)", v => f1(v)], ["ws_per_48", "WS/48", "Win Shares per 48 minuten (.100 = gemiddeld)", v => num(v) ? v.toFixed(3).replace(/^0/, "") : "–"],
  ["ast_pct", "AST%", "Aandeel van teamgenoten-velddoelpunten dat hij assisteert", v => f1(v)], ["trb_pct", "REB%", "Aandeel beschikbare rebounds", v => f1(v)],
  ["stl_pct", "STL%", "Steals per 100 bezit van de tegenstander", v => f1(v)], ["blk_pct", "BLK%", "Aandeel geblokte tweepunters van de tegenstander", v => f1(v)],
  ["tov_pct", "TOV%", "Turnovers per 100 eigen balbezit", v => f1(v)],
];
function easeChip(e, txt) {
  if (!num(e)) return txt ? `<span class="muted">${txt}</span>` : "";
  const cls = e >= 1.03 ? "pos" : e <= 0.97 ? "neg" : "mid";
  const word = e >= 1.03 ? "Gunstig" : e <= 0.97 ? "Lastig" : "Neutraal";
  return `<span class="chip ${cls}" data-tip="Tegenstanderfactor ${sg((e - 1) * 100, 1)}% voor tellende categorieën (tempo + wat deze defensie weggeeft, Basketball Reference)"><span class="dot"></span>${txt || word}</span>`;
}
const easeCls = e => !num(e) ? "" : e >= 1.04 ? "z4" : e >= 1.015 ? "z3" : e <= 0.96 ? "z0" : e <= 0.985 ? "z1" : "z2";
function bbrefNote() {
  const b = S.state.meta.bbrefStatus || {};
  if (b.players) return `Advanced stats: Basketball Reference${b.source && b.source !== "basketball-reference.com" ? " via " + esc(b.source) : ""}, stand ${ago(b.updated)}.${b.error ? ` <span data-tip="${esc(b.error)}">(direct ophalen mislukte)</span>` : ""}`;
  return `<span class="down">Advanced stats nog niet beschikbaar${b.error ? ": " + esc(b.error) : " (worden geladen)"}.</span>`;
}
const bbrefLink = id => id ? `https://www.basketball-reference.com/players/${id[0]}/${id}.html` : null;
const leftN = p => (p.left || []).length;

/* ---------------- grafieken (SVG) ---------------- */
function gameChart(games) {
  const g = games.slice(-30);
  if (!g.length) return `<div class="muted">Nog geen wedstrijden.</div>`;
  const w = 640, h = 180, pl = 30, pb = 24, pt = 10;
  const vals = g.map(x => x.v).filter(num);
  const max = Math.max(4, ...vals), min = Math.min(-4, ...vals);
  const y = v => pt + (h - pt - pb) * (max - v) / (max - min);
  const bw = (w - pl) / g.length;
  let s = `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Waarde per wedstrijd">`;
  for (let v = Math.ceil(min / 2) * 2; v <= max; v += 2) s += `<line x1="${pl}" x2="${w}" y1="${y(v)}" y2="${y(v)}" stroke="var(--border)" stroke-width="${v === 0 ? 1.2 : .5}"/><text x="${pl - 6}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="var(--text-muted)">${v}</text>`;
  g.forEach((q, i) => {
    const cx = pl + i * bw + bw / 2, bwid = Math.min(18, bw * .62);
    if (num(q.v)) {
      const top = Math.min(y(q.v), y(0)), hh = Math.max(1.5, Math.abs(y(q.v) - y(0)));
      s += `<rect x="${cx - bwid / 2}" y="${top}" width="${bwid}" height="${hh}" rx="2" fill="${q.v >= 0 ? "var(--div-pos)" : "var(--div-neg)"}"/>`;
    }
    if (g.length <= 16 || i % 2 === 0) s += `<text x="${cx}" y="${h - 10}" text-anchor="middle" font-size="9" fill="var(--text-muted)">${esc(q.opp || "")}</text>`;
    s += `<rect x="${pl + i * bw}" y="0" width="${bw}" height="${h - pb}" fill="transparent" data-tip="${esc(`${dayL(q.date)} vs ${q.opp || "?"}: ${q.line} · waarde ${sg(q.v, 1)}`)}"/>`;
  });
  return s + "</svg>";
}
function probBar(p, w = 120) {
  if (!num(p)) return "";
  const col = p >= .5 ? "var(--div-pos)" : "var(--div-neg)";
  const half = w / 2, len = Math.abs(p - .5) * 2 * half;
  const x = p >= .5 ? half : half - len;
  return `<svg class="chart" width="${w}" height="12" role="img" aria-label="Winkans ${Math.round(p * 100)}%"><rect x="0" y="5" width="${w}" height="2" fill="var(--surface-3)"/><rect x="${x}" y="1" width="${Math.max(2, len)}" height="10" rx="2" fill="${col}"/><rect x="${half - .5}" y="0" width="1" height="12" fill="var(--text-muted)"/></svg>`;
}

/* ---------------- router ---------------- */
function route() {
  const h = location.hash.replace(/^#\/?/, "") || "overzicht";
  const [name, ...args] = h.split("/").map(decodeURIComponent);
  return { name, args };
}
window.addEventListener("hashchange", () => { render(); window.scrollTo(0, 0); });

const TITLES = { league: "League", overzicht: "Overzicht", selectie: "Selectie", speler: "Spelerprofiel", live: "Wedstrijdcentrum", markt: "Spelersmarkt", categorieen: "Categorieën", schema: "Schema", nieuws: "Nieuws" };
function renderChrome() {
  const s = S.state; if (!s) return;
  renderTeamSel();
  const m = s.meta, t = m.team || {};
  $("#brandTeam").textContent = t.name || "Hit Em High";
  $("#brandSub").textContent = `${t.wins ?? 0}-${t.losses ?? 0}-${t.ties ?? 0} · Matchup ${m.matchupPeriod ?? "–"}${m.demo ? " · demo" : ""}`;
  const live = S.live && S.live.anyLive;
  $("#navLive").hidden = !live;
  const parts = [`ESPN ${new Date(s.generated).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" })}`];
  if (S.live && S.live.updated) parts.push((live ? "LIVE " : "live ") + new Date(S.live.updated).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
  setStatus(parts.join(" · "));
}
function render() {
  if (!S.state) return;
  const r = route();
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.route === r.name || (r.name === "speler" && a.dataset.route === "selectie")));
  $("#pageTitle").textContent = TITLES[r.name] || "Hit Em High Fantasy";
  const m = S.state.meta;
  $("#crumbs").textContent = `${m.leagueName || ""} · Seizoen ${m.season - 1}-${String(m.season).slice(2)} · Matchup ${m.matchupPeriod ?? "–"} · ${m.date ? new Date(m.date + "T12:00").toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" }) : ""}`;
  const view = $("#view");
  const errs = (S.state.errors || []).slice(-2).map(e => `<div class="err">${esc(e.msg)}</div>`).join("");
  const fn = { league: viewLeague, overzicht: viewHome, selectie: viewSquad, speler: viewPlayer, live: viewLive, markt: viewMarket, categorieen: viewCats, schema: viewSchedule, nieuws: viewNews }[r.name] || viewHome;
  const out = fn(r.args);
  if (out instanceof Promise) { view.innerHTML = errs + `<div class="loading">Laden…</div>`; out.then(h => { view.innerHTML = errs + h; bind(view); }); }
  else { view.innerHTML = errs + out; bind(view); }
}
function bind(root) {
  $$("[data-go]", root).forEach(el => el.addEventListener("click", e => { if (e.target.closest("a")) return; location.hash = el.dataset.go; }));
  $$("[data-win]", root).forEach(b => b.onclick = () => { S.ui.win = b.dataset.win; store.set("fmb-win", S.ui.win); render(); });
  $$("[data-punt]", root).forEach(b => b.onclick = () => { const c = b.dataset.punt; S.ui.punt.has(c) ? S.ui.punt.delete(c) : S.ui.punt.add(c); store.set("fmb-punt", JSON.stringify([...S.ui.punt])); render(); });
  (S.ui.after || []).forEach(f => f(root)); S.ui.after = [];
}
const after = f => (S.ui.after = S.ui.after || []).push(f);
const winSeg = () => `<div class="seg" title="Periode voor stats en waarde">${WINDOWS.map(([k, l]) => `<button data-win="${k}" aria-pressed="${S.ui.win === k}">${l}</button>`).join("")}</div>`;
const puntSeg = () => `<div class="seg" title="Klik om een categorie te punten (niet meetellen)">${CATS().map(c => `<button data-punt="${c.id}" aria-pressed="${!S.ui.punt.has(c.id)}" data-tip="${esc(c.name)}${S.ui.punt.has(c.id) ? " (gepunt)" : ""}">${esc(c.abbr)}</button>`).join("")}</div>`;

/* ================= MATCHUP ================= */
function scoreNow(m) {
  let w = 0, l = 0, t = 0;
  (m.cats || []).forEach(r => {
    const a = r.a.now, b = r.b.now; if (!num(a) || !num(b)) return;
    if (Math.abs(a - b) < 1e-9) t++; else if ((a > b) !== !!r.rev) w++; else l++;
  });
  return [w, l, t];
}
function matchupHeader() {
  const m = S.state.matchup, meta = S.state.meta;
  const t = meta.team || {}, o = meta.opponent;
  if (!m) return `<div class="panel"><div class="empty">Geen matchup gevonden voor deze periode.</div></div>`;
  const [w, l, ti] = scoreNow(m);
  const wp = m.winProb;
  const live = S.live && S.live.anyLive;
  return `<div class="panel" id="muHeader">
    <div class="mu">
      <div class="side"><div class="tn">${esc(t.name || "Mijn team")}</div><div class="rec">${t.wins ?? 0}-${t.losses ?? 0}-${t.ties ?? 0}</div>
        <div class="score">${w}</div><div class="proj">verwacht ${f1(m.expA)} · ${f1(m.gamesA.left)} wedstr. te gaan</div></div>
      <div class="vs">${live ? '<span class="livepill">Live</span>' : "VS"}<div class="small muted" style="margin-top:4px">${ti ? ti + " gelijk" : ""}</div></div>
      <div class="side r"><div class="tn">${esc(o ? o.name : "Geen tegenstander")}</div><div class="rec">${o ? `${o.wins ?? 0}-${o.losses ?? 0}-${o.ties ?? 0}` : ""}</div>
        <div class="score">${l}</div><div class="proj">verwacht ${f1(m.expB)} · ${f1(m.gamesB.left)} wedstr. te gaan</div></div>
    </div>
    <div class="wp"><div class="wp-bar" role="img" aria-label="Winkans ${Math.round(wp * 100)}%"><i style="width:${wp * 100}%;background:var(--series-1)"></i><i style="flex:1;background:var(--series-2)"></i></div>
      <div class="wp-l"><span><span class="sw" style="background:var(--series-1)"></span> Kans op meer categorieën: ${Math.round(wp * 100)}%</span><span>${Math.round((1 - wp) * 100)}% <span class="sw" style="background:var(--series-2)"></span></span></div></div>
  </div>`;
}
function catLabel(p) {
  if (p >= .85) return `<span class="chip pos"><span class="dot"></span>Veilig</span>`;
  if (p >= .6) return `<span class="chip pos"><span class="dot"></span>Voorsprong</span>`;
  if (p > .4) return `<span class="chip mid"><span class="dot"></span>Swing</span>`;
  if (p > .15) return `<span class="chip neg"><span class="dot"></span>Achterstand</span>`;
  return `<span class="chip neg"><span class="dot"></span>Verloren</span>`;
}
function catTable(m) {
  if (!m) return "";
  const o = S.state.meta.opponent;
  const rows = m.cats.map(r => {
    const c = CAT(r.cat);
    const lead = (a, b) => num(a) && num(b) && Math.abs(a - b) > 1e-9 ? ((a > b) !== !!r.rev) : null;
    const ln = lead(r.a.now, r.b.now);
    const ratioTip = x => x.nowDen != null ? ` data-tip="${f0(x.nowNum)}/${f0(x.nowDen)} nu · verwacht ${f0(x.num)}/${f0(x.den)}"` : "";
    return `<tr><td><span class="pn" data-tip="${esc(c.name)}${r.rev ? " (lager is beter)" : ""}">${esc(c.abbr)}</span></td>
      <td class="n big ${ln === true ? "up" : ""}"${ratioTip(r.a)}>${fmtCat(r.cat, r.a.now, true)}</td>
      <td class="n big ${ln === false ? "down" : ""}"${ratioTip(r.b)}>${fmtCat(r.cat, r.b.now, true)}</td>
      <td class="n muted">${fmtCat(r.cat, r.a.final, true)}</td><td class="n muted">${fmtCat(r.cat, r.b.final, true)}</td>
      <td>${probBar(r.pA)}</td><td class="n">${Math.round(r.pA * 100)}%</td><td>${catLabel(r.pA)}</td></tr>`;
  }).join("");
  return `<table class="t compact"><thead><tr><th>Cat.</th><th class="n">Ik</th><th class="n">${esc(o ? short(o.name) : "Opp")}</th><th class="n" data-tip="Verwachte eindstand">Ik verw.</th><th class="n">Opp verw.</th><th>Winkans</th><th class="n"></th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

/* ================= OVERZICHT ================= */
function inboxItems() {
  const items = [];
  const ro = myRoster(), starters = ro.filter(isStarter), bench = ro.filter(p => p.slot === "Bank");
  const m = S.state.matchup;
  // blessures
  ro.filter(p => p.slot !== "IR").forEach(p => {
    const i = INJ[p.injury]; if (!i) return;
    if (i[3] >= 4) items.push({ pri: isStarter(p) ? 6 : 4, ic: "✚", subj: `${p.name}: ${i[0]}`, body: `${isStarter(p) ? "Staat in je basis (" + p.slot + ")." : "Op de bank."} ${leftN(p)} wedstrijd(en) te gaan deze matchup; overweeg IR of een vervanger.`, go: `#/speler/${p.id}` });
    else items.push({ pri: 3, ic: "✚", subj: `${p.name}: ${i[0]}`, body: p.today ? `Speelt vandaag ${todayTxt(p)}. Check het nieuws voor de tip-off.` : "Houd het nieuws in de gaten.", go: `#/speler/${p.id}` });
  });
  // opstelling vandaag
  const playsToday = p => p.today && p.today.state !== "post" && !isOut(p);
  const idle = starters.filter(p => !playsToday(p));
  const ready = bench.filter(playsToday).sort((a, b) => (value(b, "ros") ?? -9) - (value(a, "ros") ?? -9));
  ready.forEach(b => {
    const swap = idle.find(s => (b.eligSlots || []).includes(s.slot));
    if (swap) { items.push({ pri: 5, ic: "⇄", subj: `Opstelling: ${b.name} in de basis`, body: `${b.name} speelt vandaag (${todayTxt(b)}), ${swap.name} niet${isOut(swap) ? " (out)" : ""}. Wissel op ${swap.slot}.`, go: `#/speler/${b.id}` }); idle.splice(idle.indexOf(swap), 1); }
    else if (idle.length) items.push({ pri: 4, ic: "⇄", subj: `${b.name} speelt vandaag op de bank`, body: `Niet elke starter speelt vandaag (${idle.map(p => short(p.name)).join(", ")}), maar de posities passen niet direct; kijk of je kunt schuiven.`, go: `#/speler/${b.id}` });
  });
  // swing-categorieën
  if (m) {
    const swing = m.cats.filter(r => r.pA > .3 && r.pA < .7);
    if (swing.length) items.push({ pri: 4, ic: "⚖", subj: `Swing-categorieën: ${swing.map(r => CAT(r.cat).abbr).join(", ")}`, body: `Hier ligt de matchup open. ${swing.map(r => `${CAT(r.cat).abbr} ${Math.round(r.pA * 100)}%`).join(" · ")}. Zoek in de spelersmarkt naar spelers die hierin sterk zijn.`, go: "#/markt" });
    const lost = m.cats.filter(r => r.pA <= .1).map(r => CAT(r.cat).abbr);
    if (lost.length && lost.length <= 3) items.push({ pri: 1, ic: "▼", subj: `Vrijwel verloren: ${lost.join(", ")}`, body: "Geen energie in steken; kies streamers voor de categorieën die nog open liggen.", go: "#/overzicht" });
  }
  // streaming: vrije spelers met veel wedstrijden in de rest van de matchup
  const val = p => value(p, "ros") ?? -9;
  const ros = p => (val(p) + 3) * leftN(p);  // simpele "rest van de week"-waarde
  const worst = ro.filter(p => p.slot !== "IR").sort((a, b) => ros(a) - ros(b))[0];
  const fa = Object.values(S.state.players).filter(p => isFree(p) && !isOut(p) && p.ppg).sort((a, b) => ros(b) - ros(a));
  if (worst && fa[0] && ros(fa[0]) - ros(worst) > 4)
    items.push({ pri: 3, ic: "＋", subj: `Streamer: ${fa[0].name} (${fa[0].pos}, ${fa[0].team})`, body: `${leftN(fa[0])} wedstrijden te gaan, waarde ${sg(val(fa[0]))}. Laagste bijdrage bij jou: ${worst.name} (${leftN(worst)} wedstr., ${sg(val(worst))}). ${f0(fa[0].owned)}% eigendom${num(fa[0].ownedChange) && fa[0].ownedChange > 0 ? `, +${f1(fa[0].ownedChange)}%` : ""}.`, go: `#/speler/${fa[0].id}` });
  // waiver: beste vrije speler op seizoenswaarde
  const faV = Object.values(S.state.players).filter(p => isFree(p) && !isOut(p)).sort((a, b) => val(b) - val(a))[0];
  const worstV = ro.filter(p => p.slot !== "IR" && !isOut(p)).sort((a, b) => val(a) - val(b))[0];
  if (faV && worstV && val(faV) - val(worstV) >= 1.5 && faV !== fa[0])
    items.push({ pri: 2, ic: "＋", subj: `Waiver-tip: ${faV.name} (${faV.pos}, ${faV.team})`, body: `Verwachte waarde ${sg(val(faV))} tegen ${sg(val(worstV))} voor ${worstV.name}.`, go: `#/speler/${faV.id}` });
  const ids = new Set(S.state.roster);
  ((S.news && S.news.mine) || []).slice(0, 6).forEach(n => {
    const pid = (n.player_ids || []).find(i => ids.has(i));
    const p = pid && P(pid);
    items.push({ pri: 0, ic: "✉", subj: n.headline, body: `${p ? p.name + " · " : ""}${n.source}${n.kind ? " " + n.kind.toLowerCase() : ""}`, when: n.published, go: p ? `#/speler/${p.id}` : "#/nieuws" });
  });
  return items.sort((a, b) => b.pri - a.pri);
}
const todayTxt = p => p.today ? `${p.today.home ? "vs" : "@"} ${p.today.opp} ${tm(p.today.date)}` : "";
const SLOT_ORDER = { PG: 0, SG: 1, G: 2, SF: 3, PF: 4, F: 5, "SG/SF": 6, "G/F": 7, "PF/C": 8, "F/C": 9, C: 10, UT: 11, Bank: 12, IR: 13 };
const bySlot = (a, b) => (SLOT_ORDER[a.slot] ?? 14) - (SLOT_ORDER[b.slot] ?? 14) || (value(b, "ros") ?? -9) - (value(a, "ros") ?? -9);
function lineupRows(rows) {
  const lr = {};
  ((S.live && S.live.me) || []).forEach(r => lr[r.id] = r);
  let group = null, h = "";
  rows.forEach(p => {
    const g = isStarter(p) ? "Basis" : p.slot === "IR" ? "IR" : "Bank";
    if (g !== group) { group = g; h += `<tr class="grp"><td colspan="6">${g === "Basis" ? "Basisopstelling vandaag" : g}</td></tr>`; }
    const l = lr[p.id];
    const t = (l && l.today) || p.today;
    const started = t && t.state && t.state !== "pre";
    const status = t ? `${t.home ? "vs" : "@"} ${esc(t.opp)} ${t.state === "in" ? `<span class="livepill">${esc(t.detail || "live")}</span>` : t.state === "post" ? `<span class="muted">${esc(t.detail || "")}</span>` : `<span class="muted">${tm(t.date)}</span>`}` : `<span class="muted">geen wedstrijd</span>`;
    h += `<tr class="click ${isStarter(p) && !p.today ? "idle" : ""}" data-go="#/speler/${p.id}">
      <td><span class="slot">${esc(p.slot)}</span></td>
      <td><div class="pn">${esc(p.name)} ${inj(p)}</div><div class="pm">${esc(p.pos)} · ${esc(p.team)}</div></td>
      <td class="small">${status}${started && l && l.line ? `<div class="pm">${esc(l.line)}</div>` : ""}</td>
      <td class="n" data-tip="Wedstrijden te gaan in deze matchup">${leftN(p)}</td>
      <td class="c">${valChip(value(p, "ros"))}</td>
      <td>${form(p, 4)}</td></tr>`;
  });
  return h;
}
function viewHome() {
  const ro = myRoster().sort(bySlot);
  const m = S.state.matchup;
  const inbox = inboxItems();
  const kpi = (l, v, n) => `<div class="kpi"><div class="l">${esc(l)}</div><div class="v">${v}</div><div class="n" title="${esc(n)}">${esc(n)}</div></div>`;
  const injured = ro.filter(p => INJ[p.injury]);
  const today = ro.filter(p => isStarter(p) && p.today).length, nStart = ro.filter(isStarter).length;
  const swing = m ? m.cats.filter(r => r.pA > .3 && r.pA < .7) : [];
  return `<div class="stack">
    <div id="muSlot">${matchupHeader()}</div>
    <div class="kpis">
      ${kpi("Wedstrijden te gaan", m ? `${f0(m.gamesA.left)} – ${f0(m.gamesB.left)}` : "–", m ? `gespeeld ${m.gamesA.played} – ${m.gamesB.played} (ik – opp)` : "")}
      ${kpi("Basis speelt vandaag", `${today}/${nStart}`, today < nStart ? "check je opstelling" : "iedereen speelt")}
      ${kpi("Swing-categorieën", swing.length, swing.map(r => CAT(r.cat).abbr).join(", ") || "geen")}
      ${kpi("Blessuremeldingen", injured.length, injured.map(p => short(p.name)).join(", ") || "Iedereen fit")}
    </div>
    <div class="grid g-side">
      <div class="stack">
        <div class="panel"><div class="panel-h"><h2>Categorieën deze matchup</h2><span class="sub">${m ? (m.source === "espn" ? "stand van ESPN" : "stand berekend uit de wedstrijden") + " + projectie rest" : ""}</span></div>
          <div class="scroll" id="catSlot">${catTable(m)}</div></div>
        <div class="panel"><div class="panel-h"><h2>Inbox</h2><span class="sub">${inbox.length} berichten</span></div>
          <ul class="inbox">${inbox.slice(0, 14).map(it => `<li data-go="${esc(it.go)}"><div class="ic">${it.ic}</div><div><div class="subj">${esc(it.subj)}</div>${it.body ? `<div class="body">${esc(it.body)}</div>` : ""}</div><div class="when">${it.when ? ago(it.when) : ""}</div></li>`).join("") || `<li><div></div><div class="muted">Geen meldingen.</div></li>`}</ul></div>
      </div>
      <div class="panel"><div class="panel-h"><h2>Opstelling</h2><a href="#/live" class="small">Wedstrijdcentrum →</a></div>
        <div class="scroll"><table class="t compact"><tbody id="homeLineup">${lineupRows(ro)}</tbody></table></div>
      </div>
    </div>
  </div>`;
}
function renderLiveParts() {
  const r = route().name;
  if (r === "overzicht") {
    const slot = $("#muSlot"); if (slot) slot.innerHTML = matchupHeader();
    const cs = $("#catSlot"); if (cs) cs.innerHTML = catTable(S.state.matchup);
    const lu = $("#homeLineup"); if (lu) { lu.innerHTML = lineupRows(myRoster().sort(bySlot)); bind(lu); }
  } else if (r === "live") { $("#view").innerHTML = viewLive(); bind($("#view")); }
}

/* ================= SELECTIE ================= */
function viewSquad() {
  const mode = S.ui.squadMode || "overzicht", w = S.ui.win;
  const ro = myRoster().sort(bySlot);
  const cats = CATS();
  let head, row;
  if (mode === "overzicht") {
    head = `<th>Slot</th><th>Speler</th><th class="c" data-tip="Kwaliteit 1-20 (verwachte waarde)">Kwal.</th><th>Vorm (ptn)</th><th>Vandaag</th><th class="n" data-tip="Wedstrijden te gaan deze matchup / volgende matchup">Wedstr.</th><th class="c">Waarde</th><th class="n">MIN</th>${cats.map(c => `<th class="n" data-tip="${esc(c.name)} per wedstrijd">${esc(c.abbr)}</th>`).join("")}<th class="n">Rang</th>`;
    row = p => { const s = pg(p, w), z = (p.z || {})[w] || {}; return `<td><span class="slot">${esc(p.slot)}</span></td>
      <td><div class="pn">${esc(p.name)} ${inj(p)}</div><div class="pm">${esc((p.elig || [p.pos]).join("/"))} · ${esc(p.team)}</div></td>
      <td class="c">${attr(quality(p))}</td><td>${form(p)}</td><td class="small">${todayStr(p)}</td>
      <td class="n">${leftN(p)} <span class="muted">/ ${p.next ?? "–"}</span></td><td class="c">${valChip(value(p, w))}</td>
      <td class="n">${s ? f1(s["40"]) : "–"}</td>${cats.map(c => `<td class="n">${zCell(z[c.id], fmtCat(c.id, catVal(s, c.id)))}</td>`).join("")}
      <td class="n muted">${p.rank ?? "–"}</td>`; };
  } else if (mode === "attributen") {
    head = `<th>Slot</th><th>Speler</th><th class="c">Kwal.</th>${cats.map(c => `<th class="c" data-tip="${esc(c.name)}">${esc(c.abbr)}</th>`).join("")}<th class="c" data-tip="Speeltijd">MIN</th>`;
    row = p => `<td><span class="slot">${esc(p.slot)}</span></td><td><div class="pn">${esc(p.name)}</div><div class="pm">${esc(p.pos)} · ${esc(p.team)}</div></td>
      <td class="c">${attr(quality(p))}</td>${cats.map(c => `<td class="c">${attr((p.attrs || {})[c.id])}</td>`).join("")}<td class="c">${attr((p.attrs || {}).MIN)}</td>`;
  } else if (mode === "advanced") {
    head = `<th>Slot</th><th>Speler</th>${ADV.map(([k, l, t]) => `<th class="n" data-tip="${esc(t)}">${l}</th>`).join("")}`;
    row = p => `<td><span class="slot">${esc(p.slot)}</span></td><td><div class="pn">${esc(p.name)}</div><div class="pm">${esc(p.pos)} · ${esc(p.team)}</div></td>
      ${ADV.map(([k, , , f]) => `<td class="n">${p.adv ? f(p.adv[k]) : "–"}</td>`).join("")}`;
  } else {
    const ws = ["season", "l7", "l15", "l30", "ros", "last"];
    head = `<th>Slot</th><th>Speler</th>${ws.map(x => `<th class="c">${WIN_L[x]}</th>`).join("")}<th data-tip="L15 t.o.v. seizoen">Trend</th><th class="n">GP</th>`;
    row = p => { const tr = num(value(p, "l15")) && num(value(p, "season")) ? value(p, "l15") - value(p, "season") : null; return `<td><span class="slot">${esc(p.slot)}</span></td><td><div class="pn">${esc(p.name)}</div><div class="pm">${esc(p.pos)} · ${esc(p.team)}</div></td>
      ${ws.map(x => `<td class="c">${valChip(value(p, x))}</td>`).join("")}<td class="${tr > 0 ? "up" : tr < 0 ? "down" : ""}">${num(tr) ? (tr > 0 ? "▲ " : "▼ ") + sg(tr) : "–"}</td><td class="n">${gp(p, "season") ?? "–"}</td>`; };
  }
  after(root => $$("[data-mode]", root).forEach(b => b.onclick = () => { S.ui.squadMode = b.dataset.mode; render(); }));
  let group = null;
  const body = ro.map(p => {
    const g = isStarter(p) ? "Basisopstelling" : "Bank en IR";
    const gh = g !== group ? `<tr class="grp"><td colspan="30">${g}</td></tr>` : ""; group = g;
    return gh + `<tr class="click" data-go="#/speler/${p.id}">${row(p)}</tr>`;
  }).join("");
  return `<div class="panel">
    <div class="panel-h"><h2>Selectie</h2>
      <div class="seg">${[["overzicht", "Overzicht"], ["attributen", "Attributen"], ["periodes", "Waarde per periode"], ["advanced", "Advanced"]].map(([k, l]) => `<button data-mode="${k}" aria-pressed="${mode === k}">${l}</button>`).join("")}</div>
      ${mode === "overzicht" ? winSeg() : ""}</div>
    <div class="panel-b" style="padding-top:0"><div class="controls"><span class="small muted">Categorieën in de waarde:</span>${puntSeg()}</div></div>
    <div class="scroll"><table class="t"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
  </div>
  <footer class="foot">Waarde = som van de z-scores per categorie t.o.v. de rosterbare spelers in de league (percentages gewogen naar pogingen, TO omgekeerd). Kleur per cel: <span class="zc z4">sterk</span> <span class="zc z3">goed</span> <span class="zc z2">gemiddeld</span> <span class="zc z1">zwak</span> <span class="zc z0">zeer zwak</span>. Attributen 1-20 volgen dezelfde z-scores (10-11 = gemiddelde rosterbare speler). Klik op een categorie om hem te punten.${mode === "advanced" ? " " + bbrefNote() : ""}</footer>`;
}

/* ================= SPELER ================= */
async function viewPlayer(args) {
  const id = +args[0];
  const tab = args[1] || "profiel";
  let d = S.cache.player[id];
  if (!d || Date.now() - d._t > 60000) {
    try { d = await api("/api/player/" + id); d._t = Date.now(); S.cache.player[id] = d; }
    catch (e) { return `<div class="empty">Speler niet gevonden (${esc(e.message)}).</div>`; }
  }
  const sp = P(id) || d;
  const v = value(d, "ros");
  const tabs = [["profiel", "Profiel"], ["log", "Wedstrijdlog"], ["schema", "Schema"], ["nieuws", `Nieuws (${d.news.length})`], ["vergelijk", "Vergelijk"]];
  const header = `<div class="panel"><div class="ph">
      <img src="${esc(d.headshot)}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'noimg'}))">
      <div><h2>${esc(d.name)}</h2>
        <div class="meta"><span class="slot">${esc(sp.slot || d.pos)}</span><span>${esc((d.elig || [d.pos]).join("/"))} · ${esc(d.team)}</span>${inj(d)}<span>${ownerLabel(sp)}</span><span class="muted">${f1(d.owned)}% eigendom · ${f1(d.started)}% start</span></div>
        <div class="meta" style="margin-top:6px"><span>Vandaag: ${todayStr(d)}</span><span>${leftN(d)} wedstr. te gaan deze matchup · ${d.next ?? "–"} volgende</span><span>Rang <strong>${d.rank ?? "–"}</strong></span></div>
      </div>
      <div class="ovr">${attr(quality(d), true)}<div class="lab">Kwaliteit</div><div style="margin-top:8px">${valChip(v)}</div><div class="lab">Waarde</div></div>
    </div></div>`;
  const tabbar = `<div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button class="tab" role="tab" aria-selected="${tab === k}" onclick="location.hash='#/speler/${id}/${k}'">${l}</button>`).join("")}</div>`;
  const body = { profiel: plProfile, log: plLog, schema: plSchedule, nieuws: plNews, vergelijk: plCompare }[tab](d);
  return `<div class="stack">${header}<div>${tabbar}${body}</div></div>`;
}
function plProfile(d) {
  const cats = CATS();
  const attrs = `<div class="attrs">${cats.map(c => `<div class="row" data-tip="${esc(c.name)} · z ${sg(((d.z || {}).ros || {})[c.id], 2)}"><span>${esc(c.name)}</span>${attr((d.attrs || {})[c.id])}</div>`).join("")}<div class="row" data-tip="Minuten per wedstrijd (verwacht)"><span>Speeltijd</span>${attr((d.attrs || {}).MIN)}</div></div>`;
  const ws = ["ros", "season", "l7", "l15", "l30", "last"].filter(w => w === "ros" ? d.ppg : d.pg[w]);
  const tbl = `<table class="t compact"><thead><tr><th>Periode</th><th class="n">GP</th><th class="n">MIN</th>${cats.map(c => `<th class="n">${esc(c.abbr)}</th>`).join("")}<th class="c">Waarde</th></tr></thead><tbody>
    ${ws.map(w => { const s = pg(d, w), z = (d.z || {})[w] || {}; return `<tr><td>${WIN_L[w]}</td><td class="n">${gp(d, w) ?? "–"}</td><td class="n">${s ? f1(s["40"]) : "–"}</td>${cats.map(c => `<td class="n">${zCell(z[c.id], fmtCat(c.id, catVal(s, c.id)))}</td>`).join("")}<td class="c">${valChip(value(d, w))}</td></tr>`; }).join("")}</tbody></table>`;
  return `<div class="grid g-side-r">
    <div class="panel"><div class="panel-h"><h2>Attributen</h2><span class="sub">1-20 · verwachte productie</span></div><div class="panel-b">${attrs}</div></div>
    <div class="stack">
      <div class="panel"><div class="panel-h"><h2>Waarde per wedstrijd</h2><span class="sub">som van de z-scores · laatste ${Math.min(30, d.form.length)} wedstrijden</span></div><div class="panel-b">${gameChart(d.form)}</div></div>
      <div class="panel"><div class="panel-h"><h2>Gemiddelden per periode</h2><span class="sub">per wedstrijd</span></div><div class="scroll">${tbl}</div></div>
      <div class="panel"><div class="panel-h"><h2>Advanced</h2>${d.adv && d.adv.bbrefId ? `<a class="small" href="${esc(bbrefLink(d.adv.bbrefId))}" target="_blank" rel="noopener">Basketball Reference →</a>` : `<span class="sub">Basketball Reference</span>`}</div>
        <div class="panel-b">${d.adv ? `<div class="statgrid">${ADV.map(([k, l, t, f]) => `<div class="stat" data-tip="${esc(t)}"><div class="l">${l}</div><div class="v">${f(d.adv[k])}</div>${k === "bpm" ? `<div class="c">O ${sg(d.adv.obpm)} · D ${sg(d.adv.dbpm)}</div>` : k === "ts_pct" ? `<div class="c">3PAr ${f2(d.adv.fg3a_per_fga_pct)} · FTr ${f2(d.adv.fta_per_fga_pct)}</div>` : ""}</div>`).join("")}</div>` : `<div class="muted">${(S.state.meta.bbrefStatus || {}).players ? "Geen Basketball Reference-data voor deze speler (nog niet gespeeld dit seizoen)." : bbrefNote()}</div>`}</div></div>
      ${d.outlook ? `<div class="panel"><div class="panel-h"><h2>ESPN-outlook</h2></div><div class="panel-b"><p class="outlook">${esc(d.outlook)}</p></div></div>` : ""}
    </div></div>`;
}
function plLog(d) {
  const g = (d.form || []).slice().reverse();
  return `<div class="panel"><div class="scroll"><table class="t compact"><thead><tr><th>Datum</th><th>Tegen</th><th class="n">MIN</th><th class="n">PTS</th><th class="n">REB</th><th class="n">AST</th><th class="n">STL</th><th class="n">BLK</th><th class="n">3PM</th><th class="n">FG</th><th class="n">FT</th><th class="n">TO</th><th class="c">Waarde</th></tr></thead><tbody>
    ${g.map(x => { return `<tr><td>${dayL(x.date)}</td><td>${esc(x.opp || "–")}</td>${logCells(d, x)}<td class="c">${valChip(x.v)}</td></tr>`; }).join("") || `<tr><td colspan="13" class="empty">Nog geen wedstrijden.</td></tr>`}
  </tbody></table></div></div>`;
}
function logCells(d, x) {
  const s = x.s || {}, i = k => f0(s[k] ?? 0);
  return [i("40"), i("0"), i("6"), i("3"), i("2"), i("1"), i("17"), `${i("13")}/${i("14")}`, `${i("15")}/${i("16")}`, i("11")].map(v => `<td class="n">${v}</td>`).join("");
}
function plSchedule(d) {
  const cats = CATS().filter(c => !c.ratio || c.id === "19");
  const fk = c => c.id === "19" ? "13" : c.id;
  return `<div class="panel"><div class="scroll"><table class="t compact"><thead><tr><th>Datum</th><th>Tegenstander</th><th>Thuis/uit</th><th class="n" data-tip="Tempo tegenstander (bezit per 48 min)">Tempo</th><th class="n" data-tip="Defensive rating tegenstander (punten toegestaan per 100 bezit)">DRtg</th><th>Matchup</th>${cats.map(c => `<th class="n" data-tip="Factor op ${esc(c.name)}${c.id === "19" ? " (velddoelpunten)" : ""}">${esc(c.abbr)}</th>`).join("")}<th></th></tr></thead><tbody>
    ${(d.upcoming || []).map(g => `<tr><td>${dayL(g.date)} ${tm(g.date)}</td><td>${esc(g.opp)}</td><td>${g.home ? "thuis" : "uit"}</td><td class="n">${f1(g.pace)}</td><td class="n">${f1(g.drtg)}</td><td>${easeChip(g.ease)}</td>
      ${cats.map(c => { const v = g.f && g.f[fk(c)]; const e = num(v) ? (c.reverse ? 2 - v : v) : null; return `<td class="n"><span class="zc ${easeCls(e)}">${num(v) ? sg((v - 1) * 100, 0) + "%" : "–"}</span></td>`; }).join("")}
      <td>${g.inMatchup ? '<span class="chip pos"><span class="dot"></span>deze matchup</span>' : ""}</td></tr>`).join("") || `<tr><td colspan="8" class="empty">Geen wedstrijden gevonden.</td></tr>`}
  </tbody></table></div></div>
  <footer class="foot">Matchupfactor per categorie: hoeveel deze tegenstander per wedstrijd weggeeft t.o.v. het league-gemiddelde, gecombineerd met het tempo, gehalveerd tegen ruis en begrensd op ±15%. Bron: Basketball Reference (tempo, DRtg, opponent per game). De factoren tellen mee in de matchup-projectie.</footer>`;
}
function newsList(items, showPlayers) {
  if (!items.length) return `<div class="empty">Geen nieuws gevonden.</div>`;
  return `<ul class="news">${items.map(n => {
    const ps = showPlayers ? (n.player_ids || []).map(P).filter(Boolean) : [];
    return `<li><div class="top"><span class="chip ${n.source === "Rotowire" ? "src-rw" : "src-espn"}">${esc(n.source)}${n.via ? " via " + esc(n.via) : ""}</span>${n.kind ? `<span>${esc(n.kind)}</span>` : ""}<span>${ago(n.published)}</span>${n.author ? `<span>${esc(n.author)}</span>` : ""}${ps.map(p => `<span class="ptag" data-go="#/speler/${p.id}">${esc(p.name)}</span>`).join("")}</div>
      <a class="hl" href="${esc(n.link || "#")}" target="_blank" rel="noopener">${esc(n.headline)}</a>${n.description && n.description !== n.headline ? `<div class="ds">${esc(n.description)}</div>` : ""}</li>`;
  }).join("")}</ul>`;
}
function plNews(d) { return `<div class="panel">${newsList(d.news, false)}</div>`; }
function plCompare(d) {
  const comps = d.competitors, cats = CATS();
  const sel = S.ui.cmpWith && S.ui.cmpWith[d.id] || (comps.find(c => c.id !== d.id) || {}).id;
  const other = comps.find(c => c.id === sel);
  after(root => { const s = $("#cmpSel", root); if (s) s.onchange = () => { S.ui.cmpWith = S.ui.cmpWith || {}; S.ui.cmpWith[d.id] = +s.value; render(); }; });
  const w = S.ui.win;
  const rows = cats.map(c => {
    const a = catVal(pg(d, w), c.id), b = other ? catVal(pg(other, w), c.id) : null;
    const za = ((d.z || {})[w] || {})[c.id], zb = other ? ((other.z || {})[w] || {})[c.id] : null;
    const dl = num(za) && num(zb) ? za - zb : null;
    return `<tr><td>${esc(c.name)}</td><td class="n">${zCell(za, fmtCat(c.id, a))}</td><td class="n">${zCell(zb, fmtCat(c.id, b))}</td><td class="n ${dl > 0 ? "up" : dl < 0 ? "down" : ""}">${sg(dl, 2)}</td></tr>`;
  }).join("");
  return `<div class="grid g2">
    <div class="panel"><div class="panel-h"><h2>Directe concurrenten (${esc(d.pos)})</h2><span class="sub">rond dezelfde rang</span></div><div class="scroll"><table class="t compact">
      <thead><tr><th class="n">#</th><th>Speler</th><th class="c">Kwal.</th><th class="c">Waarde</th><th class="n">Wedstr.</th><th>Vorm</th><th>Status</th></tr></thead><tbody>
      ${comps.map(c => `<tr class="click ${c.id === d.id ? "mine" : ""}" data-go="#/speler/${c.id}"><td class="n muted">${c.rank ?? "–"}</td><td class="pn">${esc(c.name)} <span class="pm">${esc(c.team)}</span></td><td class="c">${attr(quality(c))}</td><td class="c">${valChip(value(c, "ros"))}</td><td class="n">${leftN(c)}</td><td>${form(c, 5)}</td><td>${P(c.id) ? ownerLabel(P(c.id)) : ""}</td></tr>`).join("")}
      </tbody></table></div></div>
    <div class="panel"><div class="panel-h"><h2>Naast elkaar</h2>
      <select id="cmpSel" aria-label="Vergelijk met">${comps.filter(c => c.id !== d.id).map(c => `<option value="${c.id}" ${c.id === sel ? "selected" : ""}>${esc(c.name)} (${esc(c.team)})</option>`).join("")}</select></div>
      <div class="panel-b" style="padding-bottom:0">${winSeg()}</div>
      <div class="scroll"><table class="t compact"><thead><tr><th>Categorie</th><th class="n">${esc(short(d.name))}</th><th class="n">${esc(other ? short(other.name) : "–")}</th><th class="n">Verschil (z)</th></tr></thead><tbody>${rows}
      <tr><td><strong>Waarde</strong></td><td class="n">${valChip(value(d, w))}</td><td class="n">${other ? valChip(value(other, w)) : "–"}</td><td></td></tr></tbody></table></div></div>
  </div>`;
}

/* ================= WEDSTRIJDCENTRUM ================= */
function liveSide(rows, title) {
  if (!rows) return `<div class="panel"><div class="empty">Geen tegenstander deze matchup.</div></div>`;
  const sorted = rows.slice().sort((a, b) => (SLOT_ORDER[a.slot] ?? 14) - (SLOT_ORDER[b.slot] ?? 14));
  let group = null;
  const tot = {}; sorted.filter(r => !BENCH.includes(r.slot) && r.stats).forEach(r => Object.entries(r.stats).forEach(([k, v]) => tot[k] = (tot[k] || 0) + v));
  return `<div class="panel"><div class="panel-h"><h2>${esc(title)}</h2><span class="sub">vandaag: ${f0(tot["0"])} pts · ${f0(tot["6"])} reb · ${f0(tot["3"])} ast</span></div><div class="scroll"><table class="t compact"><tbody>
    ${sorted.map(r => {
      const g = BENCH.includes(r.slot) ? "Bank" : "Basis"; const gh = g !== group ? `<tr class="grp"><td colspan="4">${g === "Basis" ? "Basisopstelling" : "Bank en IR"}</td></tr>` : ""; group = g;
      const t = r.today;
      const status = !t ? `<span class="muted">geen wedstrijd</span>` : t.state === "in" ? `<span class="livepill">${esc(t.detail || "live")}</span>` : t.state === "post" ? esc(t.detail || "afgelopen") : `${t.home ? "vs" : "@"} ${esc(t.opp)} ${tm(t.date)}`;
      return gh + `<tr class="click ${g === "Basis" && !t ? "idle" : ""}" data-go="#/speler/${r.id}"><td><span class="slot">${esc(r.slot)}</span></td>
        <td><div class="pn">${esc(r.name)} ${inj(r)}</div><div class="pm">${esc(r.pos)} · ${esc(r.team)}${r.line ? " · " + esc(r.line) : ""}</div></td>
        <td class="small">${status}${t ? `<div><span class="progress"><i style="width:${Math.round((t.frac || 0) * 100)}%"></i></span></div>` : ""}</td>
        <td class="n big">${r.stats ? f0(r.stats["0"]) : "–"}<div class="small muted">${r.left} te gaan</div></td></tr>`;
    }).join("")}</tbody></table></div></div>`;
}
function viewLive() {
  const L = S.live;
  if (!L || !L.me) return `<div class="empty">Live-data laden…</div>`;
  const m = S.state.meta;
  const games = L.games || [];
  return `<div class="stack">${matchupHeader()}
    <div class="panel"><div class="panel-h"><h2>Categorieën live</h2><span class="sub">stand van de hele matchup + verwachting</span></div><div class="scroll">${catTable(S.state.matchup)}</div></div>
    <div class="grid g2">${liveSide(L.me, (m.team || {}).name || "Mijn team")}${liveSide(L.opp, m.opponent ? m.opponent.name : "Tegenstander")}</div>
    <div class="grid g2">
      <div class="panel"><div class="panel-h"><h2>Live-ticker</h2><span class="sub">statwijzigingen sinds de vorige verversing</span></div>
        ${L.events && L.events.length ? `<ul class="ticker">${L.events.map(e => `<li><span class="d up">${Object.entries(e.delta).map(([k, v]) => `${v > 0 ? "+" : ""}${v} ${k}`).join(" ")}</span><span><span class="pn">${esc(e.name)}</span> ${e.mine ? "" : '<span class="muted">(tegenstander)</span>'}<div class="pm">${esc(e.line || "")}</div></span><span class="muted small">${new Date(e.at).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit" })}</span></li>`).join("")}</ul>` : `<div class="empty">Nog geen live-gebeurtenissen. Tijdens de wedstrijden verschijnen hier de stats van jouw spelers en die van je tegenstander.</div>`}</div>
      <div class="panel"><div class="panel-h"><h2>Wedstrijden met jullie spelers vandaag</h2><span class="sub">${games.length} wedstrijden</span></div>
        ${games.map(g => `<div class="gamecard"><div>${g.teams.map(t => `<div class="tm"><span class="sc">${esc(t.score ?? "")}</span><span>${esc(t.abbrev)}</span></div>`).join("")}</div>
          <div class="small" style="text-align:right">${g.state === "in" ? `<span class="livepill">${esc(g.detail)}</span>` : g.state === "pre" && g.date ? esc(new Date(g.date).toLocaleString("nl-NL", { weekday: "short", hour: "2-digit", minute: "2-digit" })) : esc(g.detail || "")}</div></div>`).join("") || `<div class="empty">Vandaag geen wedstrijden met jullie spelers.</div>`}</div>
    </div>
    <footer class="foot">Winkans per categorie: huidige stand + verwachte productie in de resterende wedstrijden (dagelijkse opstelling met de beste spelers die spelen, geblesseerden (out) tellen niet), met een normale benadering van de onzekerheid. De totale winkans komt uit 3.000 simulaties. Bijgewerkt ${L.updated ? new Date(L.updated).toLocaleTimeString("nl-NL") : "–"}.</footer></div>`;
}

/* ================= SPELERSMARKT ================= */
function viewMarket(args) {
  const pos = args[0] || S.ui.mktPos || "Alle"; S.ui.mktPos = pos;
  const filt = S.ui.mktFilt || "free", q = (S.ui.mktQ || "").toLowerCase(), w = S.ui.win;
  const sortKey = S.ui.mktSort || "value", asc = !!S.ui.mktAsc;
  const cats = CATS();
  const cols = [
    { k: "rank", l: "#", n: 1, v: p => p.rank, f: p => `<span class="muted">${p.rank ?? "–"}</span>` },
    { k: "name", l: "Speler", v: p => p.name, f: p => `<div class="pn">${esc(p.name)} ${inj(p)}</div><div class="pm">${esc((p.elig || [p.pos]).join("/"))} · ${esc(p.team)}${p.mine ? " · " + esc(p.slot) : ""}</div>` },
    { k: "left", l: "Wedstr.", n: 1, tip: "Wedstrijden te gaan deze matchup / volgende", v: p => leftN(p) + (p.next || 0) / 10, f: p => `${leftN(p)} <span class="muted">/ ${p.next ?? "–"}</span>` },
    { k: "today", l: "Vandaag", v: p => p.today ? 1 : 0, f: p => `<span class="small">${p.today ? `${p.today.home ? "vs" : "@"} ${esc(p.today.opp)}` : "–"}</span>` },
    { k: "value", l: "Waarde", c: 1, v: p => value(p, w), f: p => valChip(value(p, w)) },
    { k: "gp", l: "GP", n: 1, v: p => gp(p, w), f: p => gp(p, w) ?? "–" },
    { k: "min", l: "MIN", n: 1, v: p => (pg(p, w) || {})["40"], f: p => f1((pg(p, w) || {})["40"]) },
    ...(S.ui.mktView === "adv"
      ? ADV.map(([k, l, t, f]) => ({ k: "a" + k, l, n: 1, tip: t, v: p => p.adv ? p.adv[k] : null, f: p => p.adv ? f(p.adv[k]) : "–" }))
      : cats.map(c => ({ k: "c" + c.id, l: c.abbr, n: 1, tip: c.name + (S.ui.punt.has(c.id) ? " (gepunt)" : ""), v: p => ((p.z || {})[w] || {})[c.id], f: p => zCell(((p.z || {})[w] || {})[c.id], fmtCat(c.id, catVal(pg(p, w), c.id))) }))),
    { k: "owned", l: "% eig.", n: 1, v: p => p.owned, f: p => `${f0(p.owned)}${num(p.ownedChange) && Math.abs(p.ownedChange) >= .5 ? ` <span class="${p.ownedChange > 0 ? "up" : "down"} small">${sg(p.ownedChange)}</span>` : ""}` },
    { k: "owner", l: "Status", v: p => p.mine ? 0 : isFree(p) ? 1 : 2, f: ownerLabel },
  ];
  let rows = Object.values(S.state.players).filter(p => pos === "Alle" || (p.elig || [p.pos]).includes(pos));
  if (filt === "free") rows = rows.filter(p => p.mine || isFree(p));
  if (filt === "mine") rows = rows.filter(p => p.mine);
  if (q) rows = rows.filter(p => (p.name + " " + p.team).toLowerCase().includes(q));
  const col = cols.find(c => c.k === sortKey) || cols[4];
  rows.sort((a, b) => { const x = col.v(a), y = col.v(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; const r = typeof x === "string" ? x.localeCompare(y) : x - y; return asc ? r : -r; });
  after(root => {
    $$("[data-pos]", root).forEach(b => b.onclick = () => { location.hash = "#/markt/" + encodeURIComponent(b.dataset.pos); });
    $$("[data-filt]", root).forEach(b => b.onclick = () => { S.ui.mktFilt = b.dataset.filt; render(); });
    $$("[data-mview]", root).forEach(b => b.onclick = () => { S.ui.mktView = b.dataset.mview; render(); });
    const s = $("#mktQ", root); if (s) { s.oninput = () => { S.ui.mktQ = s.value; render(); const n = $("#mktQ"); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }; }
    $$("th[data-k]", root).forEach(th => th.onclick = () => { if (S.ui.mktSort === th.dataset.k) S.ui.mktAsc = !S.ui.mktAsc; else { S.ui.mktSort = th.dataset.k; S.ui.mktAsc = ["name", "rank", "owner"].includes(th.dataset.k) || th.dataset.k === "c11"; } render(); });
  });
  return `<div class="panel">
    <div class="panel-h"><div class="seg">${POSITIONS.map(x => `<button data-pos="${x}" aria-pressed="${x === pos}">${x}</button>`).join("")}</div>
      <div class="controls"><div class="seg">${[["all", "Alle"], ["free", "Vrij & waivers"], ["mine", "Mijn spelers"]].map(([k, l]) => `<button data-filt="${k}" aria-pressed="${filt === k}">${l}</button>`).join("")}</div>
      <input type="search" id="mktQ" placeholder="Zoek speler of team…" value="${esc(S.ui.mktQ || "")}" aria-label="Zoek"><span class="muted small">${rows.length} spelers</span></div></div>
    <div class="panel-b" style="padding-top:0"><div class="controls"><div class="seg">${[["cats", "Categorieën"], ["adv", "Advanced"]].map(([k, l]) => `<button data-mview="${k}" aria-pressed="${(S.ui.mktView || "cats") === k}">${l}</button>`).join("")}</div>${winSeg()}<span class="small muted">Punt:</span>${puntSeg()}</div></div>
    <div class="scroll"><table class="t"><thead><tr>${cols.map(c => `<th class="sortable ${c.n ? "n" : c.c ? "c" : ""} ${c.k === sortKey ? "sorted" + (asc ? " asc" : "") : ""}" data-k="${c.k}" ${c.tip ? `data-tip="${esc(c.tip)}"` : ""}>${esc(c.l)}</th>`).join("")}</tr></thead>
    <tbody>${rows.slice(0, 250).map(p => `<tr class="click ${p.mine ? "mine" : ""}" data-go="#/speler/${p.id}">${cols.map(c => `<td class="${c.n ? "n" : c.c ? "c" : ""}">${c.f(p)}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${cols.length}" class="empty">Geen spelers.</td></tr>`}</tbody></table></div>
  </div>
  <footer class="foot">Stats per wedstrijd in de gekozen periode. Waarde = som van de z-scores (zonder gepunte categorieën). "Wedstr." = wedstrijden te gaan in deze matchup / in de volgende: in H2H-categorieën telt elke extra wedstrijd. Klik op een kolomkop om te sorteren. ${bbrefNote()}</footer>`;
}

/* ================= CATEGORIEËN ================= */
async function viewCats() {
  let L;
  try { L = await api("/api/league"); } catch (e) { return `<div class="empty">League laden mislukt (${esc(e.message)}).</div>`; }
  const T = S.state.fantasyTeams, me = S.state.meta.teamId, cats = CATS();
  const hasSeason = Object.values(L.strength).some(x => x.season);
  const src = S.ui.catSrc && (S.ui.catSrc !== "season" || hasSeason) ? S.ui.catSrc : hasSeason ? "season" : "proj";
  const n = Object.keys(L.strength).length;
  const cell = (tid, c) => {
    const st = L.strength[tid]; const v = st[src] && st[src][c.id]; const rk = st[src + "Rank"] && st[src + "Rank"][c.id];
    const cls = !rk ? "" : rk <= Math.ceil(n / 4) ? "z4" : rk <= Math.ceil(n / 2) ? "z3" : rk > n - Math.ceil(n / 4) ? "z0" : "z1";
    return `<td class="n"><span class="zc ${cls}" data-tip="Rang ${rk ?? "–"} van ${n}">${fmtCat(c.id, v, src === "season")}</span></td>`;
  };
  after(root => $$("[data-src]", root).forEach(b => b.onclick = () => { S.ui.catSrc = b.dataset.src; render(); }));
  const ids = Object.keys(L.strength).map(Number).sort((a, b) => (T[a] ? T[a].name : "").localeCompare(T[b] ? T[b].name : ""));
  const mine = L.strength[me];
  const ranks = mine ? cats.map(c => ({ c, r: (mine[src + "Rank"] || {})[c.id] })).filter(x => x.r) : [];
  const strong = ranks.filter(x => x.r <= Math.ceil(n / 3)).map(x => x.c.abbr);
  const weak = ranks.filter(x => x.r > n - Math.ceil(n / 3)).map(x => x.c.abbr);
  // vrije spelers die het meest helpen in je zwakste categorieën
  const weakIds = ranks.filter(x => x.r > n / 2).sort((a, b) => b.r - a.r).slice(0, 3).map(x => x.c.id);
  const helpers = weakIds.length ? Object.values(S.state.players).filter(p => isFree(p) && !isOut(p) && p.team !== "FA" && p.z && p.z.ros)
    .map(p => ({ p, s: weakIds.reduce((a, c) => a + (p.z.ros[c] || 0), 0) })).sort((a, b) => b.s - a.s).slice(0, 8) : [];
  return `<div class="stack">
    <div class="grid g2">
      <div class="panel"><div class="panel-h"><h2>Jouw categorieprofiel</h2><span class="sub">${src === "season" ? "seizoenstotalen (ESPN)" : "projectie per wedstrijd van je basis"}</span></div>
        <div class="panel-b">${mine ? `<div class="statgrid">${cats.map(c => { const r = (mine[src + "Rank"] || {})[c.id]; return `<div class="stat" data-tip="${esc(c.name)}"><div class="l">${esc(c.abbr)}</div><div class="v">${r ?? "–"}<span class="muted" style="font-size:12px">/${n}</span></div><div class="c">${fmtCat(c.id, (mine[src] || {})[c.id], src === "season")}</div></div>`; }).join("")}</div>
          <p class="small" style="margin-bottom:0">Sterk: <strong>${strong.join(", ") || "–"}</strong> · Zwak: <strong>${weak.join(", ") || "–"}</strong>${weak.length >= 2 ? ` · Overweeg te punten: <strong>${weak.slice(0, 2).join(" en ")}</strong> en vol in te zetten op de rest. Zet de punt-knoppen in de spelersmarkt om de waarde daarop aan te passen.` : ""}</p>` : `<div class="muted">Geen data.</div>`}</div></div>
      <div class="panel"><div class="panel-h"><h2>Vrije spelers voor je zwakke categorieën</h2><span class="sub">${weakIds.map(c => CAT(c).abbr).join(" + ") || "–"}</span></div>
        <div class="scroll"><table class="t compact"><thead><tr><th>Speler</th>${weakIds.map(c => `<th class="n">${esc(CAT(c).abbr)}</th>`).join("")}<th class="c">Waarde</th><th class="n">Wedstr.</th></tr></thead><tbody>
        ${helpers.map(({ p }) => `<tr class="click" data-go="#/speler/${p.id}"><td><div class="pn">${esc(p.name)}</div><div class="pm">${esc(p.pos)} · ${esc(p.team)}</div></td>${weakIds.map(c => `<td class="n">${zCell(p.z.ros[c], fmtCat(c, catVal(p.ppg, c)))}</td>`).join("")}<td class="c">${valChip(value(p, "ros"))}</td><td class="n">${leftN(p)}</td></tr>`).join("") || `<tr><td class="empty" colspan="6">Geen suggesties.</td></tr>`}
        </tbody></table></div></div>
    </div>
    <div class="panel"><div class="panel-h"><h2>Categorieën per team</h2>
      <div class="seg">${hasSeason ? `<button data-src="season" aria-pressed="${src === "season"}">Seizoen (ESPN)</button>` : ""}<button data-src="proj" aria-pressed="${src === "proj"}">Projectie per wedstrijd</button></div></div>
      <div class="scroll"><table class="t"><thead><tr><th>Team</th>${cats.map(c => `<th class="n" data-tip="${esc(c.name)}">${esc(c.abbr)}</th>`).join("")}</tr></thead>
      <tbody>${ids.map(tid => `<tr class="${tid === me ? "mine" : ""}"><td><div class="pn">${esc(T[tid] ? T[tid].name : "Team " + tid)}</div><div class="pm">${esc(T[tid] ? T[tid].owner || "" : "")}</div></td>${cats.map(c => cell(tid, c)).join("")}</tr>`).join("")}</tbody></table></div></div>
    <footer class="foot">Projectie per wedstrijd = som van de verwachte productie per wedstrijd van de beste ${Object.values(S.state.meta.slots || {}).reduce((a, b) => a + b, 0) - (S.state.meta.slots.Bank || 0) - (S.state.meta.slots.IR || 0)} spelers (exclusief IR). Kleur = rang binnen de league (blauw = top, rood = onderkant). Bij TO is lager beter.</footer>
  </div>`;
}

/* ================= SCHEMA ================= */
async function viewSchedule() {
  let d;
  try { d = await api("/api/schedule"); } catch (e) { return `<div class="empty">Schema laden mislukt (${esc(e.message)}).</div>`; }
  const mine = {}, opp = {};
  myRoster().filter(p => p.slot !== "IR").forEach(p => (mine[p.team] = mine[p.team] || []).push(p));
  oppRoster().filter(p => p.slot !== "IR").forEach(p => (opp[p.team] = opp[p.team] || []).push(p));
  const slots = Object.entries(S.state.meta.slots || {}).filter(([k]) => !BENCH.includes(k)).reduce((a, [, v]) => a + v, 0);
  // per dag: hoeveel van mijn spelers spelen er (t.o.v. het aantal basisplaatsen)
  const perDay = d.days.map(day => {
    const ps = myRoster().filter(p => p.slot !== "IR" && !isOut(p) && (d.teams.find(t => t.abbrev === p.team) || { games: {} }).games[day.sp]);
    const po = oppRoster().filter(p => p.slot !== "IR" && !isOut(p) && (d.teams.find(t => t.abbrev === p.team) || { games: {} }).games[day.sp]);
    return { day, n: ps.length, o: po.length, ps };
  });
  const head = d.days.map(x => `<th class="c ${x.sp === d.sp ? "today" : ""} ${x.next ? "nextwk" : ""}" data-tip="${esc(x.date)} · ${x.games} wedstrijden">${esc(x.dow)}<div class="pm">${x.date ? +x.date.slice(8) : ""}</div></th>`).join("");
  const rows = d.teams.map(t => `<tr class="${mine[t.abbrev] ? "mine" : ""}"><td><span class="pn">${esc(t.abbrev)}</span>${mine[t.abbrev] ? ` <span class="chip pos" data-tip="${esc(mine[t.abbrev].map(p => p.name).join(", "))}"><span class="dot"></span>${mine[t.abbrev].length}</span>` : ""}${opp[t.abbrev] ? ` <span class="chip neg" data-tip="${esc("Tegenstander: " + opp[t.abbrev].map(p => p.name).join(", "))}"><span class="dot"></span>${opp[t.abbrev].length}</span>` : ""}</td>
    ${d.days.map(x => { const g = t.games[x.sp]; return `<td class="c small ${x.sp === d.sp ? "today" : ""} ${x.next ? "nextwk" : ""} ${x.past ? "muted" : ""}">${g ? `<span class="zc ${x.past ? "" : easeCls(g.ease)}" ${num(g.ease) ? `data-tip="${esc(`${t.abbrev} ${g.home ? "vs" : "@"} ${g.opp}: tegenstanderfactor ${sg((g.ease - 1) * 100, 1)}%`)}"` : ""}>${(g.home ? "" : "@") + esc(g.opp)}</span>` : ""}</td>`; }).join("")}
    <td class="n big">${t.left}</td><td class="n muted">${t.total}</td><td class="n">${t.next}</td><td class="n">${f1(t.pace)}</td><td class="n">${f1(t.drtg)}</td><td>${easeChip(t.ease, num(t.ease) ? sg((t.ease - 1) * 100, 1) + "%" : "")}</td></tr>`).join("");
  return `<div class="stack">
    <div class="panel"><div class="panel-h"><h2>Jouw spelers per dag</h2><span class="sub">${slots} basisplaatsen · meer spelers dan plaatsen = wedstrijden die niet tellen</span></div>
      <div class="scroll"><table class="t compact"><thead><tr><th></th>${head}</tr></thead><tbody>
      <tr><td>Ik</td>${perDay.map(x => `<td class="c ${x.day.sp === d.sp ? "today" : ""} ${x.day.next ? "nextwk" : ""}"><span class="zc ${x.n > slots ? "z0" : x.n >= slots - 2 ? "z3" : x.n <= 3 ? "z1" : "z2"}" data-tip="${esc(x.ps.map(p => p.name).join(", "))}">${x.n}</span></td>`).join("")}</tr>
      <tr><td>Tegenstander</td>${perDay.map(x => `<td class="c ${x.day.sp === d.sp ? "today" : ""} ${x.day.next ? "nextwk" : ""}">${x.day.next ? "" : x.o}</td>`).join("")}</tr>
      <tr><td class="muted">NBA-wedstrijden</td>${d.days.map(x => `<td class="c muted ${x.next ? "nextwk" : ""}">${x.games}</td>`).join("")}</tr>
      </tbody></table></div></div>
    <div class="panel"><div class="panel-h"><h2>NBA-schema</h2><span class="sub">deze matchup en de volgende (grijze achtergrond) · gesorteerd op wedstrijden te gaan</span></div>
      <div class="scroll"><table class="t compact sched"><thead><tr><th>Team</th>${head}<th class="n" data-tip="Wedstrijden te gaan deze matchup">Te gaan</th><th class="n">Totaal</th><th class="n" data-tip="Wedstrijden volgende matchup">Volgende</th><th class="n" data-tip="Eigen tempo (Basketball Reference)">Tempo</th><th class="n" data-tip="Eigen defensive rating">DRtg</th><th data-tip="Gemiddelde tegenstanderfactor van de resterende wedstrijden deze matchup">Matchups</th></tr></thead><tbody>${rows}</tbody></table></div></div>
    <footer class="foot">@ = uitwedstrijd. Kleur per wedstrijd: <span class="zc z4">gunstig</span> <span class="zc z3">licht gunstig</span> <span class="zc z1">licht lastig</span> <span class="zc z0">lastig</span> voor fantasy-productie, op basis van tempo en wat de tegenstander per wedstrijd weggeeft (Basketball Reference${d.bbref ? ", " + ago(d.bbref) : ""}). Teams met veel wedstrijden in de rest van de matchup zijn goud voor streamers; op dagen met weinig NBA-wedstrijden (zie onderste rij) krijg je je spelers makkelijker kwijt in de basis.</footer>
  </div>`;
}

/* ================= NIEUWS ================= */
function viewNews() {
  if (!S.news) { loadNews(); return `<div class="loading">Nieuws laden…</div>`; }
  const scope = S.ui.newsScope || "mine", src = S.ui.newsSrc || "all";
  const sel = S.ui.newsSel || new Set(); S.ui.newsSel = sel;
  let items = scope === "mine" ? S.news.mine : scope === "opp" ? S.news.opp : S.news.latest;
  if (src !== "all") items = items.filter(n => n.source === src);
  if (sel.size) items = items.filter(n => (n.player_ids || []).some(i => sel.has(i)));
  const ro = scope === "opp" ? oppRoster() : myRoster();
  after(root => {
    $$("[data-scope]", root).forEach(b => b.onclick = () => { S.ui.newsScope = b.dataset.scope; S.ui.newsSel = new Set(); render(); });
    $$("[data-nsrc]", root).forEach(b => b.onclick = () => { S.ui.newsSrc = b.dataset.nsrc; render(); });
    $$("[data-pid]", root).forEach(b => b.onclick = () => { const i = +b.dataset.pid; sel.has(i) ? sel.delete(i) : sel.add(i); render(); });
  });
  return `<div class="panel"><div class="panel-h">
      <div class="controls"><div class="seg">${[["mine", "Mijn team"], ["opp", "Tegenstander"], ["all", "Alle spelers"]].map(([k, l]) => `<button data-scope="${k}" aria-pressed="${scope === k}">${l}</button>`).join("")}</div>
      <div class="seg">${[["all", "Alle bronnen"], ["ESPN", "ESPN"], ["Rotowire", "Rotowire"]].map(([k, l]) => `<button data-nsrc="${k}" aria-pressed="${src === k}">${l}</button>`).join("")}</div></div>
      <span class="sub">${items.length} items</span></div>
    ${scope !== "all" ? `<div class="panel-b"><div class="pchips">${ro.map(p => `<button class="pchip" data-pid="${p.id}" aria-pressed="${sel.has(p.id)}">${esc(p.name)}</button>`).join("")}</div></div>` : ""}
    ${newsList(items.slice(0, 150), true)}</div>
    <footer class="foot">Bronnen: ESPN-artikelen en -video's, Rotowire-spelersupdates (via ESPN en de Rotowire-RSS-feed). ${etlLine(S.news.etl)}</footer>`;
}
function etlLine(e) {
  if (!e) return "";
  const r = (e.runs || [])[0], t = e.totals || {};
  const where = e.backend === "postgres" ? "Postgres" : e.backend === "json" ? "lokaal JSON-bestand" : e.backend;
  let txt = `Opslag: ${esc(where)}${num(t.items) ? ` · ${f0(t.items)} artikelen${num(t.links) ? `, ${f0(t.links)} spelerkoppelingen` : ""}` : ""}.`;
  if (r) txt += ` Laatste ETL-run${r.started ? ` (${ago(r.started)})` : ""}: ${esc(r.status)}, ${r.extracted ?? "–"} opgehaald, ${r.loaded_items ?? 0} nieuw${num(r.seconds) ? ` (${r.seconds}s)` : ""}.`;
  if (r && r.message) txt += ` <span data-tip="${esc(r.message)}">⚠ waarschuwing</span>`;
  return txt;
}

/* ================= LEAGUE ================= */
async function viewLeague() {
  let L;
  try { L = await api("/api/league"); } catch (e) { return `<div class="empty">League laden mislukt (${esc(e.message)}).</div>`; }
  const T = S.state.fantasyTeams, me = S.state.meta.teamId;
  const tn = id => T[id] ? esc(T[id].name) : "Team " + id;
  const mu = L.matchups.map(m => {
    const mine = m.a === me || m.b === me;
    const side = (id, sc, exp, wp, r) => `<div class="side ${r ? "r" : ""}"><div class="tn">${tn(id)}</div><div class="rec">${T[id] ? esc(T[id].owner || "") : ""}</div>
      <div class="score" style="font-size:26px">${sc}</div><div class="proj">verwacht ${f1(exp)} · ${Math.round(wp * 100)}%</div></div>`;
    const chips = m.cats.map(r => { const c = CAT(r.cat); const cls = r.pA >= .6 ? "pos" : r.pA <= .4 ? "neg" : "mid"; return `<span class="chip ${cls}" data-tip="${esc(`${c.name}: ${fmtCat(r.cat, r.a, true)} – ${fmtCat(r.cat, r.b, true)} · kans ${Math.round(r.pA * 100)}% voor ${T[m.a] ? T[m.a].name : ""}`)}">${esc(c.abbr)}</span>`; }).join(" ");
    return `<div class="panel" style="${mine ? "box-shadow: inset 3px 0 0 var(--mine-edge)" : ""}"><div class="mu">${side(m.a, m.now[0], m.expA, m.winProb)}<div class="vs">${L.anyLive ? '<span class="livepill">Live</span>' : "VS"}${m.now[2] ? `<div class="small muted">${m.now[2]} gelijk</div>` : ""}</div>${side(m.b, m.now[1], m.expB, 1 - m.winProb, true)}</div>
      <div class="wp"><div class="wp-bar" role="img" aria-label="Winkans"><i style="width:${m.winProb * 100}%;background:var(--series-1)"></i><i style="flex:1;background:var(--series-2)"></i></div></div>
      <div class="panel-b" style="padding-top:6px">${chips}</div></div>`;
  }).join("");
  const rows = L.standings.map((t, i) => `<tr class="${t.id === me ? "mine" : ""} click" data-team="${t.id}"><td class="n muted">${i + 1}</td><td><div class="pn">${esc(t.name)}</div><div class="pm">${esc(t.owner || "")}</div></td><td class="n">${t.wins}-${t.losses}-${t.ties}</td><td class="n">${num(t.pct) ? t.pct.toFixed(3).replace(/^0/, "") : "–"}</td><td class="n">${esc(t.streak || "")}</td><td class="n muted">${t.moves ?? "–"}</td></tr>`).join("");
  after(root => $$("[data-team]", root).forEach(tr => tr.onclick = () => { if (+tr.dataset.team !== me && confirm(`Bekijk de app als ${T[tr.dataset.team].name}?`)) switchTeam(+tr.dataset.team); }));
  return `<div class="grid g-side"><div class="stack"><h2 style="font-size:13px;margin:0">Matchups (periode ${L.matchupPeriod ?? "–"})</h2>${mu || `<div class="empty">Geen matchups gevonden.</div>`}</div>
    <div class="panel"><div class="panel-h"><h2>Stand</h2><span class="sub">categorieën W-V-G · klik op een team om de app als dat team te bekijken</span></div><div class="scroll"><table class="t"><thead><tr><th class="n">#</th><th>Team</th><th class="n">W-V-G</th><th class="n">Pct</th><th class="n">Reeks</th><th class="n" data-tip="Transacties deze matchup">Moves</th></tr></thead><tbody>${rows}</tbody></table></div></div></div>`;
}

/* ================= TOEGANG & TEAMKEUZE ================= */
function showGate(kind, msg) {
  S.state = null;
  document.body.classList.add("gated");
  const v = $("#view");
  $("#pageTitle").textContent = kind === "code" ? "Welkom" : "Kies je team";
  $("#crumbs").textContent = AUTH.leagueName || "";
  setStatus("");
  if (kind === "code") {
    v.innerHTML = `<div class="gate panel"><div class="panel-b">
      <div class="brand-mark" style="margin-bottom:12px"><svg viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="13" fill="#4de5f9" stroke="#8c1457" stroke-width="2.2"/><path d="M3.5 16h25M16 3.5v25M7.5 6.8c3.6 2.6 5.6 5.7 5.6 9.2s-2 6.6-5.6 9.2M24.5 6.8c-3.6 2.6-5.6 5.7-5.6 9.2s2 6.6 5.6 9.2" fill="none" stroke="#8c1457" stroke-width="2.2" stroke-linecap="round"/></svg></div>
      <h2 style="margin:0 0 4px">Hit Em High Fantasy</h2>
      <p class="muted" style="margin:0 0 14px">${esc(AUTH.leagueName || "Je league")}: vul de toegangscode in die je van de beheerder hebt gekregen. Let's go! 🎺🏀</p>
      ${msg ? `<div class="err">${esc(msg)}</div>` : ""}
      <form id="codeForm" class="controls"><input type="password" id="codeIn" placeholder="Toegangscode" autocomplete="current-password" required style="flex:1;min-width:0">
      <button class="btn primary" type="submit">Let's go</button></form></div></div>`;
    $("#codeForm").onsubmit = e => { e.preventDefault(); AUTH.code = $("#codeIn").value.trim(); store.set("fmb-code", AUTH.code); boot(); };
    setTimeout(() => $("#codeIn") && $("#codeIn").focus(), 50);
  } else {
    const teams = AUTH.teams || [];
    v.innerHTML = `<div class="gate panel" style="max-width:640px"><div class="panel-h"><h2>Welk team is van jou?</h2><span class="sub">${esc(AUTH.leagueName || "")}</span></div>
      <ul class="inbox">${teams.map(t => `<li data-pick="${t.id}"><div class="ic" style="font-size:11px;font-weight:800">${esc((t.abbrev || t.name || "").slice(0, 2).toUpperCase())}</div><div><div class="subj">${esc(t.name)}</div><div class="body">${esc(t.owner || "")} · ${t.wins}-${t.losses}-${t.ties} · team-ID ${t.id}</div></div><div class="when">Kies →</div></li>`).join("")}</ul></div>`;
    $$("[data-pick]", v).forEach(li => li.onclick = () => switchTeam(+li.dataset.pick));
  }
}
function switchTeam(tid) {
  AUTH.team = tid; store.set("fmb-team", String(tid));
  const u = new URL(location.href); u.searchParams.set("team", tid); history.replaceState(null, "", u.pathname + u.search + (location.hash || "#/overzicht"));
  S.state = null; S.live = null; S.news = null; S.cache = { player: {} };
  document.body.classList.remove("gated");
  $("#view").innerHTML = `<div class="loading">Team laden…</div>`;
  loadState();
}
function renderTeamSel() {
  const sel = $("#teamSel"); if (!sel || !AUTH.teams) return;
  if (!sel.options.length) {
    sel.innerHTML = AUTH.teams.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join("");
    sel.onchange = () => switchTeam(+sel.value);
    $("#shareBtn").onclick = async () => {
      const link = `${location.origin}/?team=${AUTH.team}`;
      try { await navigator.clipboard.writeText(link); setStatus("Link gekopieerd: " + link); } catch (e) { prompt("Kopieer deze link:", link); }
    };
  }
  sel.value = String(AUTH.team);
}
async function boot() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    AUTH.needsCode = cfg.needsCode; AUTH.leagueName = cfg.leagueName;
    if (cfg.needsCode && !AUTH.code) return showGate("code");
    let t;
    for (let i = 0; i < 40; i++) {
      t = await api("/api/teams");
      if (t.teams.length) break;
      $("#view").innerHTML = `<div class="loading">League laden: de eerste keer duurt dit een paar seconden…</div>`;
      await new Promise(r => setTimeout(r, 3000));
    }
    AUTH.teams = t.teams; AUTH.leagueName = t.leagueName;
    const valid = AUTH.team && t.teams.some(x => x.id === AUTH.team);
    if (!valid) { AUTH.team = null; return showGate("team"); }
    switchTeam(AUTH.team);
  } catch (e) { if (!(e instanceof AuthError)) $("#view").innerHTML = `<div class="empty">Server niet bereikbaar (${esc(e.message)}).</div>`; }
}

/* ---------------- start ---------------- */
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });
boot();
