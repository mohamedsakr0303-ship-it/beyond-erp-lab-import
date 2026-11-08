/* BeyondERP — owner console: a page of its own for running the hosted service (customers, subscriptions, payments,
   support, plans and what they include, the team). Everything it shows and changes goes through the workspace's
   console_* functions (supabase/migrations/*_owner_console.sql, *_owner_control.sql), which check the team member's
   access on every call; this page only arranges it. */
"use strict";
(() => {
// ================================================================== small helpers
const $ = (s, el = document) => el.querySelector(s), $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const LS = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } };
const LSset = (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };
const S = { lang: LS("berp.console.lang", null) || LS("berp.lang", "en") || "en", me: null, look: null, H: {}, alerts: [], seen: new Set(), cache: {} };
const L = (en, ar) => S.lang === "ar" ? ar : en;
// the same page code serves the support desk (support.html): its own menu and name, the team's support pages only
const DESK = window.BEYOND_CONSOLE === "support";
const CNAME = () => DESK ? L("Support desk", "مكتب الدعم") : L("Owner console", "لوحة المالك");
const num = n => (Number(n) || 0).toLocaleString("en-US", { maximumFractionDigits: 2 });
const money = n => (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const day = x => x ? String(x).slice(0, 10) : "—";
const dt = x => x ? String(x).slice(0, 10) + " " + String(x).slice(11, 16) : "—";
const ago = x => { if (!x) return "—"; const s = (Date.now() - new Date(x)) / 1000; const f = (n, en, ar) => L(`${n} ${en} ago`, `منذ ${n} ${ar}`);
  return s < 60 ? L("just now", "الآن") : s < 3600 ? f(Math.floor(s / 60), "min", "دقيقة") : s < 86400 ? f(Math.floor(s / 3600), "h", "ساعة") : s < 86400 * 45 ? f(Math.floor(s / 86400), "d", "يوم") : day(x); };
const daysTo = x => x ? Math.ceil((new Date(x) - Date.now()) / 86400000) : null;
const today = () => new Date().toISOString().slice(0, 10);
const initials = s => String(s || "?").replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join("") || "?";
const hue = s => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
const avatar = s => `<span class="av" style="background:hsl(${hue(s)} 55% 45%)">${esc(initials(s))}</span>`;
const kpi = (l, v, cls = "", sub = "", href = "") => `<${href ? `a href="${href}"` : "div"} class="kpi ${cls}"><div class="l">${esc(l)}</div><div class="v">${v}</div>${sub ? `<div class="s">${sub}</div>` : ""}</${href ? "a" : "div"}>`;
const chip = (t, cls = "") => `<span class="chip ${cls}">${esc(t)}</span>`;
// small drawn icons (the same on every system, unlike symbol characters)
const ICON = { mode: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor"/></svg>',
  out: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l-5-5 5-5M5 12h11"/></svg>',
  bell: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg>',
  menu: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>' };

function toast(m, bad) {
  $$(".toast").forEach(t => t.remove());
  const t = document.createElement("div"); t.className = "toast" + (bad ? " err" : ""); t.textContent = m; document.body.appendChild(t);
  setTimeout(() => t.remove(), bad ? 6000 : 2600);
}
const fail = e => toast((e && e.message) || String(e), true);
// in-page dialogs: confirm → true/false, prompt → text or null
function dialog(msg, o = {}) {
  return new Promise(done => {
    const d = document.createElement("div"); d.className = "shade top";
    d.innerHTML = `<div class="dlg" role="dialog">${o.title ? `<h2>${esc(o.title)}</h2>` : ""}<p>${esc(msg)}</p>
      ${o.input !== undefined ? (o.area ? `<textarea rows="4">${esc(o.input)}</textarea>` : `<input value="${esc(o.input)}" ${o.type ? `type="${o.type}"` : ""}>`) : ""}
      <div class="row"><button class="btn" data-r="0">${esc(o.cancel || L("Cancel", "إلغاء"))}</button><button class="btn ${o.danger ? "danger" : "pri"}" data-r="1">${esc(o.ok || L("OK", "موافق"))}</button></div></div>`;
    const end = ok => { const i = $("input,textarea", d); d.remove(); done(o.input !== undefined ? (ok ? i.value : null) : ok); };
    d.addEventListener("click", e => { const b = e.target.closest("[data-r]"); if (b) end(b.dataset.r === "1"); else if (e.target === d) end(false); });
    d.addEventListener("keydown", e => { if (e.key === "Escape") end(false); if (e.key === "Enter" && e.target.tagName === "INPUT") end(true); });
    document.body.appendChild(d); ($("input,textarea", d) || $("[data-r='1']", d)).focus();
  });
}
const confirmIt = (m, o) => dialog(m, o);
const ask = (m, v = "", o = {}) => dialog(m, { ...o, input: v });
// a side panel with a form: fields → values; onSave(values) (throw to show an error)
function panel(title, html, onSave, o = {}) {
  closePanel();
  const sh = document.createElement("div"); sh.className = "shade"; sh.style.background = "rgba(10,12,28,.35)"; sh.id = "pshade";
  const p = document.createElement("form"); p.className = "panel" + (o.wide ? " wide" : ""); p.id = "panel"; p.noValidate = false;
  p.innerHTML = `<div class="ph"><h2>${esc(title)}</h2><button type="button" class="btn icon ghost" data-close>✕</button></div><div class="pb"><div id="perr"></div>${html}</div>
    <div class="pf">${o.extra || ""}<span class="sp"></span><button type="button" class="btn" data-close>${esc(onSave ? L("Cancel", "إلغاء") : L("Close", "إغلاق"))}</button>${onSave ? `<button class="btn pri" type="submit">${esc(o.saveLabel || L("Save", "حفظ"))}</button>` : ""}</div>`;
  sh.addEventListener("click", e => { if (e.target === sh) closePanel(); });
  p.addEventListener("click", e => { if (e.target.closest("[data-close]")) closePanel(); });
  p.addEventListener("submit", async e => {
    e.preventDefault(); if (!onSave) return;
    const b = $("button[type=submit]", p); b.disabled = true;
    try { const r = await onSave(readForm(p), p); if (r !== false) { closePanel(); if (!o.stay) route(); } }
    catch (er) { $("#perr", p).innerHTML = `<div class="banner err">${esc(er.message)}</div>`; $("#perr", p).scrollIntoView({ block: "nearest" }); }
    finally { b.disabled = false; }
  });
  document.body.append(sh, p);
  if (o.onOpen) o.onOpen(p);
  const f = $("input:not([type=hidden]):not([type=checkbox]),select,textarea", p); if (f && matchMedia("(pointer:fine)").matches) f.focus();
  return p;
}
function closePanel() { $$("#panel,#pshade").forEach(x => x.remove()); }
function readForm(f) {
  const v = {};
  $$("[name]", f).forEach(el => { if (el.type === "file") return; if (el.type === "checkbox") v[el.name] = el.checked; else if (el.type === "radio") { if (el.checked) v[el.name] = el.value; } else v[el.name] = el.value; });
  return v;
}
// fields: [{k, l, type, v, opts, req, span, help, ph, min, max, step, dis}]
const field = f => {
  const a = `name="${f.k}" ${f.req ? "required" : ""} ${f.ph ? `placeholder="${esc(f.ph)}"` : ""} ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} ${f.dis ? "disabled" : ""} ${f.dir ? `dir="${f.dir}"` : ""}`;
  if (f.type === "check") return `<label class="chk ${f.span ? "span" : ""}"><input type="checkbox" ${a} ${f.v ? "checked" : ""}> ${esc(f.l)}</label>`;
  if (f.type === "html") return `<div class="${f.span ? "span" : ""}">${f.html}</div>`;
  const ctl = f.type === "select" ? `<select ${a}>${(f.opts || []).map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(f.v ?? "") ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`
    : f.type === "area" ? `<textarea ${a} rows="${f.rows || 3}">${esc(f.v ?? "")}</textarea>`
    : `<input type="${f.type || "text"}" ${a} value="${esc(f.v ?? "")}" ${f.type === "number" ? `step="${f.step || "any"}" inputmode="decimal"` : ""}>`;
  return `<label class="f ${f.span ? "span" : ""}">${esc(f.l)}${ctl}${f.help ? `<span class="h">${esc(f.help)}</span>` : ""}</label>`;
};
const form = (fields, cols = 2) => `<div class="grid g${cols}">${fields.map(field).join("")}</div>`;

// ================================================================== the workspace (same sign-in as the app)
const SES = "berp.cloud.session";
const remembered = () => LS("berp.remember", true) !== false;
const box = () => { try { return remembered() ? localStorage : sessionStorage; } catch { return localStorage; } };
const getSes = () => { try { return JSON.parse(box().getItem(SES) || localStorage.getItem(SES) || sessionStorage.getItem(SES) || "null"); } catch { return null; } };
const putSes = v => { try { const b = box(); v == null ? (localStorage.removeItem(SES), sessionStorage.removeItem(SES)) : b.setItem(SES, JSON.stringify(v)); } catch { /* private mode */ } };
const cfg = () => { const c = LS("berp.cloud", null) || window.BEYOND_CLOUD_CONFIG || {}; return c.url && c.key ? { url: c.url.replace(/\/+$/, ""), key: c.key } : null; };
async function req(method, path, body, token, raw) {
  const c = cfg(); if (!c) throw new Error(L("This page is not connected to a workspace.", "هذه الصفحة غير متصلة بمساحة عمل."));
  let r;
  try { r = await fetch(c.url + path, { method, headers: { apikey: c.key, Authorization: "Bearer " + (token || c.key), ...(raw ? { "Content-Type": raw.type || "application/octet-stream" } : { "Content-Type": "application/json" }) },
    body: raw || (body === undefined ? undefined : JSON.stringify(body)) }); }
  catch { throw new Error(L("Cannot reach the workspace — check the internet connection.", "تعذر الوصول إلى مساحة العمل — تحقق من الاتصال بالإنترنت.")); }
  const text = await r.text(); let d = null; try { d = text ? JSON.parse(text) : null; } catch { d = { message: text }; }
  if (!r.ok) { const e = new Error(String((d && (d.message || d.msg || d.error_description || d.error)) || "HTTP " + r.status).replace(/^[a-z_]+: /, "")); e.status = r.status; e.code = d && (d.error_code || d.code); throw e; }
  return d;
}
const saveSes = s => putSes({ access: s.access_token, refresh: s.refresh_token, exp: Date.now() + ((s.expires_in || 3600) - 60) * 1000, email: ((s.user && s.user.email) || (getSes() || {}).email || "").toLowerCase() });
async function token() {
  const s = getSes(); if (!s) { const e = new Error(L("Sign in", "سجّل الدخول")); e.code = "signed_out"; throw e; }
  if (Date.now() < s.exp) return s.access;
  try { saveSes(await req("POST", "/auth/v1/token?grant_type=refresh_token", { refresh_token: s.refresh })); }
  catch (e) { if (e.status) putSes(null); e.code = "signed_out"; throw e; }
  return getSes().access;
}
const rpc = async (fn, p) => req("POST", "/rest/v1/rpc/" + fn, p || {}, await token());
const fnCall = async (name, body) => req("POST", "/functions/v1/" + name, body, await token());
const push = (event, id, extra) => fnCall("push", { action: "platform", event, id, ...(extra || {}) }).catch(() => null);
const myEmail = () => ((getSes() || {}).email || "").toLowerCase();
async function upload(bucket, file, folder = "staff") {
  const safe = (String(file.name || "file").normalize("NFKD").replace(/[^\w.\-]+/g, "_").replace(/^_+/, "").slice(-80)) || "file";
  const path = `${folder}/${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}-${safe}`;
  await req("POST", `/storage/v1/object/${bucket}/${path.split("/").map(encodeURIComponent).join("/")}`, undefined, await token(), file);
  return { path, name: String(file.name || safe).slice(0, 200), type: file.type || "", size: file.size || 0 };
}
async function fileUrl(bucket, path) {
  const c = cfg(), r = await fetch(`${c.url}/storage/v1/object/authenticated/${bucket}/${String(path).split("/").map(encodeURIComponent).join("/")}`,
    { headers: { apikey: c.key, Authorization: "Bearer " + await token() } });
  if (!r.ok) throw new Error(L("This file cannot be opened", "تعذر فتح هذا الملف") + ` (HTTP ${r.status})`);
  return URL.createObjectURL(await r.blob());
}
const download = (name, text, type = "text/csv") => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000); };
const csv = rows => "﻿" + rows.map(r => r.map(v => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }).join(",")).join("\n");

// ================================================================== the look (the team's choice; light / dark also per device)
const LOOK0 = { name: "BeyondERP", sub: "Owner console", accent: "#4f46e5", mode: "auto", side: "dark", density: "comfortable", corners: "rounded", logo: "" };
function applyLook() {
  const lk = { ...LOOK0, ...(S.look || {}) }, mine = LS("berp.console.mode", "");
  const mode = mine || lk.mode, dark = mode === "dark" || (mode === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  const r = document.documentElement;
  r.dataset.mode = dark ? "dark" : "light"; r.dataset.side = lk.side; r.dataset.density = lk.density; r.dataset.corners = lk.corners;
  r.style.setProperty("--accent", lk.accent || LOOK0.accent);
  r.lang = S.lang; r.dir = S.lang === "ar" ? "rtl" : "ltr";
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyLook);

// ================================================================== areas of the console, by permission
const can = area => S.me && (S.me.full || (area !== "_full" && !!(S.me.perms || {})[area]));
const NAV0 = () => [
  [L("Business", "الأعمال"), [["overview", "◧", L("Overview", "نظرة عامة"), "overview"], ["alerts", "🔔", L("Notifications", "الإشعارات"), "*"], ["customers", "▦", L("Customers", "العملاء"), "customers"],
    ["people", "☺", L("People", "الأشخاص"), "customers"], ["referrals", "🎁", L("Referrals", "الإحالات"), "customers"], ["cancellations", "✗", L("Cancellations & churn", "الإلغاءات والمغادرة"), "customers"]]],
  [L("Money", "المال"), [["payments", "⧗", L("Payments to verify", "مدفوعات للتحقق"), "billing"], ["ledger", "≣", L("All payments", "كل المدفوعات"), "billing"],
    ["methods", "💳", L("Payment methods", "طرق الدفع"), "settings"]]],
  [L("Support", "الدعم"), [["queue", "☰", L("Support queue", "قائمة الدعم"), "support"], ["support", "✉", L("All tickets", "كل البلاغات"), "support"], ["replies", "💬", L("Saved replies", "الردود المحفوظة"), "desk:replies"],
    ["bugs", "🐞", L("Bug reports", "بلاغات الأخطاء"), "desk:bugs"], ["help", "📖", L("Help & FAQ", "المساعدة والأسئلة الشائعة"), "desk:help"], ["solved", "✅", L("Shared solutions", "الحلول المنشورة"), "desk:help"], ["visits", "🚗", L("Support visits", "زيارات الدعم"), "desk:visits"],
    ["notices", "📣", L("Announcements", "الإعلانات"), "notices"]]],
  [L("Product", "المنتج"), [["plans", "◎", L("Plans", "الباقات"), "settings"], ["addons", "＋", L("Modules, AI & support", "الأقسام والذكاء الاصطناعي والدعم"), "settings"],
    ["menus", "☰", L("Menus by industry", "القوائم حسب النشاط"), "settings"], ["settings", "⚙", L("Rules & switches", "القواعد والمفاتيح"), "settings"],
    ["texts", "✎", L("Website", "الموقع"), "site"]]],
  [L("Console", "اللوحة"), [["team", "☷", L("Team", "الفريق"), "_full"], ["look", "🎨", L("Look of this console", "مظهر اللوحة"), "settings"],
    ["log", "⌚", L("Activity", "السجل"), "_full"], ["deleted", "⌫", L("Deleted companies", "الشركات المحذوفة"), "customers"]]]];
// what this team member may do on the support desk (full access: everything; members added before: everything)
const DESK_KEYS = ["reply", "notes", "manage", "close", "books", "contacts", "replies", "help", "bugs", "visits"];
const dcan = k => !!S.me && (S.me.full || !!(S.me.desk || {})[k]);
const navOk = a => a === "*" || (a.startsWith("desk:") ? dcan(a.slice(5)) : can(a));
const DESK_NAV = () => [[L("Support", "الدعم"), [["queue", "☰", L("Queue", "قائمة الانتظار"), "support"], ["support", "✉", L("All tickets", "كل البلاغات"), "support"],
    ["bugs", "🐞", L("Bug reports", "بلاغات الأخطاء"), "desk:bugs"], ["visits", "🚗", L("Support visits", "زيارات الدعم"), "desk:visits"], ["replies", "💬", L("Saved replies", "الردود المحفوظة"), "desk:replies"],
    ["notices", "📣", L("Announcements", "الإعلانات"), "notices"]], "support"],
  [L("Help center", "مركز المساعدة"), [["help", "📖", L("Help & FAQ", "المساعدة والأسئلة الشائعة"), "desk:help"], ["solved", "✅", L("Shared solutions", "الحلول المنشورة"), "desk:help"],
    ["texts", "✎", L("Website", "الموقع"), "site"]], "product"],
  [L("Customers", "العملاء"), [["customers", "▦", L("Customers", "العملاء"), "customers"], ["people", "☺", L("People", "الأشخاص"), "customers"]], "business"],
  [L("Me", "أنا"), [["alerts", "🔔", L("Notifications", "الإشعارات"), "*"]], "console"]];

// ================================================================== arranging the console: right-click, drag and drop, lock
// The team's arrangement (shared setting "console_layout") applies to everyone; a team member who cannot change the
// console's settings keeps their own arrangement on this device. {nav: {order: [[group, [page…]]…], names: {en: {}, ar: {}},
// gnames: {en: {}, ar: {}}, hidden: [page…]}, cols: {table: {order, hidden, names: {en, ar}}}, locked}
const GIDS = ["business", "money", "support", "product", "console"];
const teamLay = () => can("settings");
const lay = () => (teamLay() ? S.me && S.me.layout : LS("berp.console.layout", null) || (S.me && S.me.layout)) || {};
async function saveLay(next, msg) {
  if (teamLay()) { await rpc("console_setting_save", { p_key: "console_layout", p_value: next }); S.me.layout = next; }
  else LSset("berp.console.layout", next);
  toast(msg || (teamLay() ? L("Saved for the whole team.", "حُفظ للفريق كله.") : L("Saved on this device.", "حُفظ على هذا الجهاز.")));
  shell(); route();
}
const editLay = fn => { const y = JSON.parse(JSON.stringify(lay())); fn(y); return saveLay(y); };
const locked = () => !!lay().locked;
// the menu as arranged: [label, items, group id]; every page stays reachable (hidden ones too, by link)
const NAV = () => {
  if (DESK) return DESK_NAV();
  const base = NAV0(), nv = lay().nav || {}, names = (nv.names || {})[S.lang] || {}, gn = (nv.gnames || {})[S.lang] || {};
  const all = base.flatMap(([, it], gi) => it.map(i => [i, GIDS[gi]])), used = new Set();
  const out = (Array.isArray(nv.order) && nv.order.length ? nv.order : base.map(([, it], gi) => [GIDS[gi], it.map(i => i[0])]))
    .filter(([g]) => GIDS.includes(g)).map(([g, keys]) => [g, (keys || []).map(k => all.find(([i]) => i[0] === k)).filter(x => x && !used.has(x[0][0]) && used.add(x[0][0])).map(([i]) => i)]);
  all.forEach(([i, g]) => { if (used.has(i[0])) return; let grp = out.find(o => o[0] === g); if (!grp) out.push(grp = [g, []]); grp[1].push(i); });
  return out.map(([g, it]) => [gn[g] || base[GIDS.indexOf(g)][0], it.map(i => names[i[0]] ? [i[0], i[1], names[i[0]], i[3], i[2]] : i), g]);
};
const navHidden = () => (lay().nav || {}).hidden || [];
const navOrder = () => NAV().map(([, it, g]) => [g, it.map(i => i[0])]);
const editNav = fn => editLay(y => { y.nav = y.nav || {}; y.nav.order = navOrder(); fn(y.nav, y); });
const setName = (bag, k, v) => { bag[S.lang] = bag[S.lang] || {}; if (v) bag[S.lang][k] = v; else delete bag[S.lang][k]; };
function moveNav(key, toGroup, beforeKey) {
  return editNav(nv => { nv.order.forEach(o => { o[1] = o[1].filter(k => k !== key); });
    const grp = nv.order.find(o => o[0] === toGroup); if (!grp) return;
    const at = beforeKey ? grp[1].indexOf(beforeKey) : -1; if (at < 0) grp[1].push(key); else grp[1].splice(at, 0, key); });
}
// a small menu at the pointer: items [{l, ic, a, dis}] or "-"
function ctxMenu(e, items) {
  e.preventDefault(); closeCtx();
  const m = document.createElement("div"); m.className = "ctx"; m.id = "ctx"; m.setAttribute("role", "menu");
  m.innerHTML = items.filter(Boolean).map((it, i) => it === "-" ? "<hr>" : `<button type="button" role="menuitem" data-ci="${i}" ${it.dis ? "disabled" : ""}><span class="ic">${it.ic || ""}</span>${esc(it.l)}</button>`).join("");
  document.body.appendChild(m);
  const list = items.filter(Boolean), r = m.getBoundingClientRect();
  m.style.left = Math.max(6, Math.min(e.clientX, innerWidth - r.width - 6)) + "px"; m.style.top = Math.max(6, Math.min(e.clientY, innerHeight - r.height - 6)) + "px";
  m.addEventListener("click", ev => { const b = ev.target.closest("[data-ci]"); if (!b) return; closeCtx(); Promise.resolve(list[+b.dataset.ci].a()).catch(fail); });
  const f = $("button:not([disabled])", m); if (f) f.focus({ preventScroll: true });
}
const closeCtx = () => { const m = $("#ctx"); if (m) m.remove(); };
const lockItem = () => ({ l: locked() ? L("Unlock the arrangement", "فك قفل الترتيب") : L("Lock everything in place", "قفل كل شيء في مكانه"), ic: locked() ? "🔓" : "🔒",
  a: () => editLay(y => { y.locked = !y.locked; }) });
function navCtx(e, key) {
  if (DESK) return;
  if (locked()) return ctxMenu(e, [{ l: L("Open", "فتح"), ic: "↗", a: () => { location.hash = "#/" + key; } }, "-", lockItem()]);
  const nav = NAV(), gi = nav.findIndex(([, it]) => it.some(i => i[0] === key)), items = nav[gi][1], ii = items.findIndex(i => i[0] === key), it = items[ii];
  ctxMenu(e, [{ l: L("Open", "فتح"), ic: "↗", a: () => { location.hash = "#/" + key; } },
    { l: L("Rename…", "إعادة التسمية…"), ic: "✎", a: async () => { const v = await ask(L("Name in the menu (blank: the original)", "الاسم في القائمة (فارغ: الأصلي)"), it[2]); if (v === null) return;
      editNav(nv => { nv.names = nv.names || {}; setName(nv.names, key, v.trim() === (it[4] || it[2]) ? "" : v.trim()); }); } },
    { l: L("Move up", "تحريك لأعلى"), ic: "↑", dis: ii === 0 && gi === 0, a: () => ii > 0 ? moveNav(key, nav[gi][2], items[ii - 1][0]) : moveNav(key, nav[gi - 1][2]) },
    { l: L("Move down", "تحريك لأسفل"), ic: "↓", dis: ii === items.length - 1 && gi === nav.length - 1, a: () => ii < items.length - 2 ? moveNav(key, nav[gi][2], items[ii + 2][0]) : ii === items.length - 2 ? moveNav(key, nav[gi][2]) : moveNav(key, nav[gi + 1][2], (nav[gi + 1][1][0] || [])[0]) },
    ...nav.filter((_, j) => j !== gi).map(([gl, , g]) => ({ l: L(`Move to “${gl}”`, `نقل إلى «${gl}»`), ic: "⇢", a: () => moveNav(key, g) })),
    { l: L("Hide from the menu", "إخفاء من القائمة"), ic: "⊘", a: () => editNav(nv => { nv.hidden = [...new Set([...(nv.hidden || []), key])]; }) },
    "-", ...arrangeItems()]);
}
function groupCtx(e, g) {
  if (DESK) return;
  if (locked()) return ctxMenu(e, [lockItem()]);
  const nav = NAV(), gi = nav.findIndex(x => x[2] === g);
  ctxMenu(e, [{ l: L("Rename group…", "إعادة تسمية المجموعة…"), ic: "✎", a: async () => { const v = await ask(L("Group name (blank: the original)", "اسم المجموعة (فارغ: الأصلي)"), nav[gi][0]); if (v === null) return;
      editNav(nv => { nv.gnames = nv.gnames || {}; setName(nv.gnames, g, v.trim()); }); } },
    { l: L("Move group up", "تحريك المجموعة لأعلى"), ic: "↑", dis: gi === 0, a: () => editNav(nv => { const o = nv.order; [o[gi - 1], o[gi]] = [o[gi], o[gi - 1]]; }) },
    { l: L("Move group down", "تحريك المجموعة لأسفل"), ic: "↓", dis: gi === nav.length - 1, a: () => editNav(nv => { const o = nv.order; [o[gi + 1], o[gi]] = [o[gi], o[gi + 1]]; }) },
    "-", ...arrangeItems()]);
}
const arrangeItems = () => [
  { l: L(`Show hidden items (${navHidden().length})`, `إظهار العناصر المخفية (${navHidden().length})`), ic: "◉", dis: !navHidden().length, a: showHidden },
  { l: L("Reset the menu", "إعادة القائمة لأصلها"), ic: "↺", a: async () => { if (await confirmIt(L("Put the menu back as it came (names, order and hidden items)?", "إعادة القائمة كما كانت (الأسماء والترتيب والعناصر المخفية)؟"))) editLay(y => { delete y.nav; }); } },
  lockItem()];
function showHidden() {
  const all = NAV0().flatMap(([, it]) => it), hid = navHidden();
  panel(L("Hidden menu items", "عناصر القائمة المخفية"), `<p class="muted small">${esc(L("Hidden pages stay reachable by link. Tick to show again.", "الصفحات المخفية تبقى متاحة بالرابط. حدد لإظهارها مجدداً."))}</p>
    <div class="checks">${hid.map(k => { const i = all.find(x => x[0] === k); return i ? `<label class="chk"><input type="checkbox" name="h_${k}"> ${esc(i[2])}</label>` : ""; }).join("")}</div>`,
    v => { closePanel(); return editNav(nv => { nv.hidden = (nv.hidden || []).filter(k => !v["h_" + k]); }).then(() => false); }, { saveLabel: L("Show", "إظهار") });
}
// dragging menu items (and groups) to a new place
let dragNav = null;
document.addEventListener("dragstart", e => {
  const a = e.target.closest && e.target.closest(".nav a[data-nav],.nav-h[data-g]"); if (!a || locked()) return;
  dragNav = a.dataset.nav ? { key: a.dataset.nav } : { group: a.dataset.g }; e.dataTransfer.effectAllowed = "move"; try { e.dataTransfer.setData("text/plain", a.dataset.nav || a.dataset.g); } catch { /* old browsers */ }
  a.classList.add("dragging");
});
document.addEventListener("dragover", e => { if (!dragNav) return; const t = e.target.closest && e.target.closest(".nav a[data-nav],.nav-h[data-g]"); if (!t) return;
  e.preventDefault(); $$(".drop-at").forEach(x => x.classList.remove("drop-at")); t.classList.add("drop-at"); });
document.addEventListener("dragend", () => { dragNav = null; $$(".dragging,.drop-at").forEach(x => x.classList.remove("dragging", "drop-at")); });
document.addEventListener("drop", e => {
  if (!dragNav) return; const t = e.target.closest && e.target.closest(".nav a[data-nav],.nav-h[data-g]"); const d = dragNav; dragNav = null; if (!t) return; e.preventDefault();
  const nav = NAV(), groupOf = k => (nav.find(([, it]) => it.some(i => i[0] === k)) || [])[2];
  if (d.key) { if (t.dataset.nav === d.key) return; moveNav(d.key, t.dataset.nav ? groupOf(t.dataset.nav) : t.dataset.g, t.dataset.nav || ((nav.find(x => x[2] === t.dataset.g) || [, []])[1][0] || [])[0]); }
  else { const to = t.dataset.g || groupOf(t.dataset.nav); if (to === d.group) return;
    editNav(nv => { const o = nv.order, from = o.findIndex(x => x[0] === d.group), g = o.splice(from, 1)[0]; o.splice(o.findIndex(x => x[0] === to), 0, g); }); }
});

// ================================================================== the frame
function shell() {
  const lk = { ...LOOK0, ...(S.look || {}) }, b = S.me.badges || {};
  const badge = k => { const n = k === "payments" ? b.orders : k === "queue" ? b.tickets : k === "visits" ? b.visits : k === "alerts" ? unreadCount() : 0; return n ? `<span class="cnt">${n}</span>` : ""; };
  document.body.innerHTML = `<div class="app" id="app">
    <aside class="side"><div class="brand">${lk.logo ? `<img src="${esc(lk.logo)}" alt="">` : `<span class="mark">${esc(initials(lk.name))}</span>`}<div><b>${esc(lk.name)}</b><small>${esc(DESK ? CNAME() : lk.sub || CNAME())}</small></div></div>
      ${NAV().map(([h, items, g]) => { const ok = items.filter(i => navOk(i[3]) && (DESK || !navHidden().includes(i[0]))), dr = locked() || DESK ? "" : 'draggable="true"';
        return ok.length ? `<div class="nav-h" data-g="${g}" ${dr}>${esc(h)}</div><nav class="nav">${ok.map(([k, ic, l]) => `<a href="#/${k}" data-nav="${k}" ${dr}><span class="ic">${ic}</span>${esc(l)}<span class="cnt-slot" data-badge="${k}">${badge(k)}</span></a>`).join("")}</nav>` : ""; }).join("")}
      <div class="foot">${DESK ? (S.me.full ? `<a href="owner.html" style="color:inherit">⚙ ${esc(L("Owner console", "لوحة المالك"))}</a><br>` : "") : `<a href="support.html" style="color:inherit">🎧 ${esc(L("Support desk", "مكتب الدعم"))} ↗</a><br>`}${DESK ? "" : locked() ? `<button type="button" class="lockb" data-a="unlock" title="${esc(L("Unlock the arrangement", "فك قفل الترتيب"))}">🔒 ${esc(L("Arrangement locked", "الترتيب مقفل"))}</button><br>` : `<span class="hint">${esc(L("Right-click or drag to arrange", "انقر بالزر الأيمن أو اسحب للترتيب"))}</span><br>`}
        <a href="index.html" style="color:inherit">← ${esc(L("Open the ERP", "فتح النظام"))}</a><br>${esc(S.me.email)}</div></aside>
    <div class="main"><header class="top"><button class="btn icon ghost menu-btn" data-a="menu" aria-label="Menu">${ICON.menu}</button>
      <div style="min-width:0"><div class="ttl" id="ttl"></div><div class="crumb" id="crumb"></div></div><span class="sp"></span>
      <div class="bell" id="bell"><button class="btn icon" data-a="bell" aria-label="${esc(L("Notifications", "الإشعارات"))}">${ICON.bell}<span id="bellcnt"></span></button></div>
      <button class="btn icon" data-a="mode" title="${esc(L("Light / dark", "فاتح / داكن"))}">${ICON.mode}</button>
      <button class="btn" data-a="lang">${S.lang === "ar" ? "English" : "عربي"}</button>
      <button class="btn icon" data-a="signout" title="${esc(L("Sign out", "تسجيل الخروج"))}">${ICON.out}</button></header>
      <main class="content" id="view"></main></div>${dockBar(badge)}</div>`;
  drawBell(); applyDock();
}
// ================================================================== the bottom bar (phones; computers too if chosen on this device)
// A glass bar whose marker shows the page in use (never sliding across) — a glow capsule, a spotlight, a floating bubble or a liquid drop —
// or the classic bar; the same looks as the ERP's. Kept per device (On this device, on the Notifications page).
const DI = p => `<svg class="i" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
const DOCK_IC = { overview: DI('<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>'), payments: DI('<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18M7 15h3"/>'),
  queue: DI('<path d="M4 4h16v12H8l-4 4z"/><path d="M8 9h8M8 12h5"/>'), support: DI('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>'),
  customers: DI('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6 6 0 0 1 3.5 6"/>'),
  alerts: DI('<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>'), menu: DI('<path d="M4 6h16M4 12h16M4 18h16"/>') };
const DOCK_LAYERS = `<span class="bb-bg" aria-hidden="true"><b class="bb-drop"></b></span><span class="bb-ghost" aria-hidden="true"><i></i></span><span class="bb-ind" aria-hidden="true"><i></i></span>
  <svg class="bb-defs" width="0" height="0" aria-hidden="true" focusable="false"><filter id="bbgoo"><feGaussianBlur in="SourceGraphic" stdDeviation="7" result="b"/>
  <feColorMatrix in="b" mode="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 20 -8" result="g"/><feComposite in="SourceGraphic" in2="g" operator="atop"/></filter></svg>`;
const dockItems = () => {
  const all = NAV().flatMap(([, it]) => it), want = DESK ? ["queue", "support", "customers", "alerts"] : ["overview", "payments", "queue", "alerts"];
  return want.map(k => all.find(i => i[0] === k)).filter(i => i && navOk(i[3])).slice(0, 4);
};
function dockBar(badge) {
  const items = dockItems();
  return `<nav class="bottombar" style="--n:${items.length + 1}" aria-label="${esc(L("Quick navigation", "تنقل سريع"))}">${DOCK_LAYERS}
    ${items.map(([k, , l], i) => `<a href="#/${k}" data-bi="${i}" data-dk="${k}" title="${esc(l)}">${DOCK_IC[k] || DOCK_IC.overview}<span>${esc(DOCK_SHORT()[k] || l)}</span><span class="cnt-slot" data-badge="${k}">${badge(k)}</span></a>`).join("")}
    <button type="button" data-a="menu" data-bi="${items.length}">${DOCK_IC.menu}<span>${esc(L("Menu", "القائمة"))}</span></button></nav>`;
}
const DOCK_SHORT = () => ({ overview: L("Overview", "نظرة عامة"), payments: L("Payments", "المدفوعات"), queue: L("Queue", "الانتظار"),
  support: L("Tickets", "البلاغات"), customers: L("Customers", "العملاء"), alerts: L("Alerts", "الإشعارات") });
const DOCKS = () => [["halo", L("Glass: glow capsule", "زجاجي: كبسولة مضيئة")], ["spot", L("Glass: spotlight", "زجاجي: ضوء مسلّط")],
  ["bubble", L("Glass: floating bubble", "زجاجي: فقاعة عائمة")], ["liquid", L("Glass: liquid drop", "زجاجي: قطرة سائلة")], ["classic", L("Classic", "كلاسيكي")]];
const dockPref = () => { const d = LS("berp.console.dock", {}) || {};
  return { style: DOCKS().some(x => x[0] === d.style) ? d.style : "halo", where: d.where === "all" ? "all" : "phone",
    color: /^#[0-9a-f]{6}$/i.test(d.color || "") ? d.color : "" }; };
// the bottom bar's highlight: the console's main colour, or one chosen for it
const DOCK_COLORS = ["#6c5cff", "#3b82f6", "#14b8a6", "#22c55e", "#f59e0b", "#f43f5e", "#e879f9", "#0ea5e9"];
function applyDock() {
  const d = dockPref(), h = document.documentElement;
  DOCKS().forEach(([k]) => h.classList.toggle("dock-" + k, k === d.style));
  h.classList.toggle("dock-all", d.where === "all");
  if (d.color) h.style.setProperty("--dock-hl", d.color); else h.style.removeProperty("--dock-hl");
  dockSync();
}
function dockSync() {
  const bar = $(".bottombar"); if (!bar) return;
  const page = parse().parts[0] || (DESK ? "queue" : "overview"), app = $("#app");
  const key = { company: "customers", ticket: "queue", visit: "queue" }[page] || page;
  const on = app && app.classList.contains("menu") ? bar.querySelector("button[data-a=menu]") : bar.querySelector(`[data-dk="${key}"]`);
  bar.classList.toggle("bb-none", !on);
  if (on) { const b = bar.getBoundingClientRect(), r = on.getBoundingClientRect();
    bar.style.setProperty("--bi", on.dataset.bi); bar.style.setProperty("--bx", Math.round(r.left - b.left + r.width / 2) + "px"); }
  $$("[data-bi]", bar).forEach(el => el.classList.toggle("on", el === on));
  // a new tab: the marker is already there (placed while hidden) and rises into view once; the same tab again does nothing
  const cur = on ? String(on.dataset.bi) : "";
  if (cur !== (bar.dataset.cur || "")) {
    const prev = bar.dataset.cur || "";
    bar.dataset.cur = cur;
    // the tab left behind sinks away where it was (glow capsule), while the new one rises in
    if (prev && document.documentElement.classList.contains("dock-halo")) { bar.style.setProperty("--pi", prev);
      bar.classList.toggle("bb-leave", false); void bar.offsetWidth; bar.classList.toggle("bb-leave", true);
      clearTimeout(bar._leaveT); bar._leaveT = setTimeout(() => bar.classList.toggle("bb-leave", false), 450); }
    if (on) { bar.classList.toggle("bb-rise", false); void bar.offsetWidth; bar.classList.toggle("bb-rise", true); }
  }
}
const dockSelect = () => { const d = dockPref();
  return `<div class="grid g2" style="gap:8px;margin-top:10px"><label class="f">${esc(L("Bottom bar", "الشريط السفلي"))}<select data-change="dockset" data-k="style">${DOCKS().map(([v, l]) => `<option value="${v}" ${d.style === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
    <label class="f">${esc(L("Show the bottom bar", "إظهار الشريط السفلي"))}<select data-change="dockset" data-k="where">${[["phone", L("On phones", "على الهواتف")], ["all", L("On phones and computers", "على الهواتف والكمبيوتر")]].map(([v, l]) => `<option value="${v}" ${d.where === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
    <div class="f dock-colors" style="grid-column:1/-1"><span>${esc(L("Bottom bar highlight", "تمييز الشريط السفلي"))}</span><div class="row" style="gap:6px;flex-wrap:wrap;margin-top:5px">
      <button type="button" class="btn sm ${d.color ? "" : "pri"}" data-a="dockcol" data-v="">${esc(L("Main colour", "اللون الرئيسي"))}</button>
      ${DOCK_COLORS.map(c => `<button type="button" class="dock-sw ${d.color.toLowerCase() === c ? "on" : ""}" style="--c:${c}" data-a="dockcol" data-v="${c}" title="${c}"></button>`).join("")}
      <label class="dock-sw pick ${d.color && !DOCK_COLORS.includes(d.color.toLowerCase()) ? "on" : ""}" title="${esc(L("Any colour", "أي لون"))}"><input type="color" data-change="dockcolpick" value="${esc(d.color || "#6c5cff")}"></label></div></div></div>`; };
document.addEventListener("click", () => { setTimeout(dockSync, 30); setTimeout(dockSync, 320); }, true);
addEventListener("hashchange", () => setTimeout(dockSync, 40));
addEventListener("keydown", e => { if (e.key === "Escape") setTimeout(dockSync, 40); });
addEventListener("resize", () => dockSync());

// a touch on the bottom bar: a burst of light from the point touched, inside that button
document.addEventListener("pointerdown", e => {
  const b = e.target && e.target.closest && e.target.closest(".bottombar>a, .bottombar>button, .bottombar .bb-new>.btn");
  if (!b) return;
  const r = b.getBoundingClientRect();
  b.style.setProperty("--tx", Math.round(e.clientX - r.left - 3) + "px"); b.style.setProperty("--ty", Math.round(e.clientY - r.top - 3) + "px");
  b.classList.toggle("bb-tap", false); void b.offsetWidth; b.classList.toggle("bb-tap", true);
  clearTimeout(b._tapT); b._tapT = setTimeout(() => b.classList.toggle("bb-tap", false), 600);
}, { passive: true });
const unreadCount = () => S.alerts.filter(a => !a.read).length;
function drawBell() {
  const n = unreadCount(), c = $("#bellcnt"); if (c) c.innerHTML = n ? `<span class="cnt">${n > 99 ? "99+" : n}</span>` : "";
  const slot = $('[data-badge="alerts"]'); if (slot) slot.innerHTML = n ? `<span class="cnt">${n}</span>` : "";
  document.title = (n ? `(${n}) ` : "") + `${({ ...LOOK0, ...(S.look || {}) }).name} · ${CNAME()}`;
}

// ================================================================== notifications: worked out by the workspace, checked every minute
const AL_KIND = () => ({
  ticket: [L("Support ticket", "بلاغ دعم"), "✉"], visit: [L("Visit requested", "طلب زيارة"), "🚗"], visit_accepted: [L("Visit quote accepted", "قبول عرض الزيارة"), "🚗"],
  visit_paid: [L("Visit payment to check", "دفعة زيارة للتحقق"), "🚗"], visit_soon: [L("Visit in the next 2 days", "زيارة خلال يومين"), "🚗"],
  order: [L("Payment to verify", "دفعة للتحقق"), "⧗"], order_stale: [L("Order not paid after 3 days", "طلب لم يُدفع بعد 3 أيام"), "⧗"],
  trial_ending: [L("Trial ending, not subscribed", "تجربة تنتهي دون اشتراك"), "⏳"], trial_ended: [L("Trial ended without subscribing", "انتهت التجربة دون اشتراك"), "⌛"],
  payment_due: [L("Payment due (grace days)", "دفعة مستحقة (فترة سماح)"), "!"], lapsed: [L("Stopped paying", "توقف عن الدفع"), "✗"],
  renewal_soon: [L("Renewal coming up", "تجديد قريب"), "↻"], cancel: [L("Asked not to renew", "طلب عدم التجديد"), "✗"],
  new_company: [L("New company", "شركة جديدة"), "＋"], unpaid_company: [L("New company not subscribed", "شركة جديدة غير مشتركة"), "○"],
  over_limit: [L("More people than the plan allows", "أفراد أكثر مما تسمح الباقة"), "☺"] });
const alertLink = a => "#/" + a.link;
const alertHtml = a => { const k = AL_KIND()[a.kind] || [a.kind, "•"];
  return `<a class="al ${a.read ? "" : "unread"}" href="${alertLink(a)}" data-a="alopen" data-key="${esc(a.key)}"><span class="dot ${a.level}"></span><span class="b"><b>${k[1]} ${esc(k[0])}</b>
    <span>${esc(a.title)}</span>${a.amount != null ? ` · <b style="display:inline">${money(a.amount)}</b>` : ""}<br><small>${esc(a.detail || "")} · ${esc(ago(a.at))}</small></span>
    <button type="button" class="btn sm ghost x" data-a="aldismiss" data-key="${esc(a.key)}" title="${esc(L("Dismiss", "إخفاء"))}">✕</button></a>`; };
async function loadAlerts(first) {
  try { S.alerts = await rpc("console_alerts"); } catch { return; }
  const fresh = S.alerts.filter(a => !a.read && !S.seen.has(a.key));
  S.alerts.forEach(a => S.seen.add(a.key));
  drawBell();
  if (!first && fresh.length) {
    beep();
    if ("Notification" in window && Notification.permission === "granted") {
      for (const a of fresh.slice(0, 3)) { const k = AL_KIND()[a.kind] || [a.kind];
        const n = new Notification(`${k[0]} · ${a.title}`, { body: a.detail || "", tag: a.key, icon: "icon-192.png" });
        n.onclick = () => { window.focus(); location.hash = alertLink(a); n.close(); }; }
    } else toast(`🔔 ${fresh.length} ${L("new notification(s)", "إشعار جديد")}`);
  }
}
// alert sounds, the same set as the ERP: soft tones made in the browser, or the person's own clip; kept on this device
const SND_KEY = "berp.console.snd";
const SOUNDS = () => [["chime", L("Chime", "رنين")], ["glass", L("Glass", "زجاج")], ["pop", L("Pop", "فقاعة")], ["marimba", L("Marimba", "ماريمبا")],
  ["bell", L("Bell", "جرس")], ["soft", L("Soft", "هادئ")], ["pulse", L("Pulse", "نبض")]];
const TONES = {   // [frequency, start s, length s, type, level]
  chime: [[880, 0, .22, "sine", 1], [1320, .12, .3, "sine", .9]],
  glass: [[1568, 0, .5, "sine", .7], [2093, .06, .45, "sine", .45], [3136, .1, .3, "sine", .2]],
  pop: [[620, 0, .07, "triangle", 1], [930, .05, .08, "triangle", .7]],
  marimba: [[523, 0, .25, "sine", 1], [659, .11, .25, "sine", .9], [784, .22, .35, "sine", .9]],
  bell: [[1046, 0, .9, "sine", .8], [2093, 0, .5, "sine", .25], [1568, .02, .6, "sine", .2]],
  soft: [[440, 0, .4, "sine", .7], [554, .18, .45, "sine", .55]],
  pulse: [[740, 0, .09, "square", .35], [740, .16, .09, "square", .35], [988, .32, .14, "square", .35]],
};
const VIBES = { short: [120], double: [140, 70, 140], long: [450], pulse: [80, 60, 80, 60, 80] };
const sndPrefs = () => ({ sound: LS("berp.console.sound", true) ? "chime" : "none", volume: 70, vibrate: false, vibe: "double", quiet: false, quiet_from: "22:00", quiet_to: "07:00", ...LS(SND_KEY, {}) });
const sndSave = patch => LSset(SND_KEY, { ...LS(SND_KEY, {}), ...patch });
const sndOwn = () => LS("berp.console.sndfile", "");
let actx = null;
const audioCtx = () => { try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); if (actx.state === "suspended") actx.resume(); } catch { actx = null; } return actx; };
["pointerdown", "keydown"].forEach(ev => window.addEventListener(ev, () => { if (!actx) audioCtx(); }, { once: true, passive: true }));
function playSound(name, volume) {
  const vol = Math.max(0, Math.min(100, volume ?? 70)) / 100;
  if (!vol || name === "none") return;
  if (name === "custom") { const src = sndOwn(); if (src) { try { const a = new Audio(src); a.volume = vol; a.play().catch(() => {}); } catch { /* no audio */ } return; } name = "chime"; }
  const c = audioCtx(), tones = TONES[name] || TONES.chime; if (!c) return;
  const t0 = c.currentTime + .01;
  tones.forEach(([f, d, len, type, lvl]) => { const o = c.createOscillator(), g = c.createGain(); o.type = type; o.frequency.value = f;
    g.gain.setValueAtTime(.0001, t0 + d); g.gain.exponentialRampToValueAtTime(Math.max(.0002, .22 * vol * lvl), t0 + d + .015); g.gain.exponentialRampToValueAtTime(.0001, t0 + d + len);
    o.connect(g); g.connect(c.destination); o.start(t0 + d); o.stop(t0 + d + len + .05); });
}
const inQuiet = p => { if (!p.quiet) return false; const now = new Date(), m = now.getHours() * 60 + now.getMinutes(), hm = x => { const [h, mi] = String(x || "0:0").split(":").map(Number); return h * 60 + mi; };
  const a = hm(p.quiet_from), b = hm(p.quiet_to); return a <= b ? m >= a && m < b : m >= a || m < b; };
// one alert: the chosen sound and vibration, unless it is quiet time
function beep() { const p = sndPrefs(); if (inQuiet(p)) return; playSound(p.sound, p.volume);
  if (p.vibrate) try { navigator.vibrate && navigator.vibrate(VIBES[p.vibe] || VIBES.double); } catch { /* not supported */ } }
const markAlerts = async (keys, state) => { if (!keys.length) return; await rpc("console_alert_mark", { p_keys: keys, p_state: state });
  S.alerts = state === "dismissed" ? S.alerts.filter(a => !keys.includes(a.key)) : S.alerts.map(a => keys.includes(a.key) ? { ...a, read: state === "read" } : a); drawBell(); };

// ================================================================== tables: search, filters, sort, export
// cols: [{k, l, v: row => value for sort/filter/export, h: row => cell html, cls, num, date, nosort}]
// filters: [{k, l, type: "select" | "from" | "to" | "min" | "max", col: column key, opts: [[v, label]], test: (row, v) => bool}]
const DT = {};
function table(host, key, rows, o) {
  const st = DT[key] = DT[key] || { q: "", f: {}, sort: o.sort || null, dir: o.dir || "desc", n: 100, ...(LS("berp.console.dt." + key, {}) || {}) };
  st.n = st.n || 100;
  const colOf = k => o.cols.find(c => c.k === k);
  const val = (c, r) => c.v ? c.v(r) : r[c.k];
  // the columns as arranged (order, hidden, own names) — right-click a heading, or drag it
  const cl = (lay().cols || {})[key] || {}, cn = (cl.names || {})[S.lang] || {};
  const cols = [...(cl.order || []).map(colOf).filter(Boolean), ...o.cols.filter(c => !(cl.order || []).includes(c.k))]
    .filter(c => !(cl.hidden || []).includes(c.k)).map(c => cn[c.k] ? { ...c, l: cn[c.k] } : c);
  const editCols = fn => editLay(y => { y.cols = y.cols || {}; const c = y.cols[key] = y.cols[key] || {}; c.order = cols.map(x => x.k).concat(o.cols.map(x => x.k).filter(k => !cols.some(x => x.k === k))); fn(c); });
  const keep = () => LSset("berp.console.dt." + key, { q: st.q, f: st.f, sort: st.sort, dir: st.dir });
  const pass = r => {
    if (st.q) { const q = st.q.toLowerCase(); if (!(o.search || o.cols.map(c => c.k)).some(k => { const c = colOf(k); return String(c ? val(c, r) : r[k] ?? "").toLowerCase().includes(q); })) return false; }
    for (const fl of o.filters || []) {
      const v = st.f[fl.k]; if (v === undefined || v === "") continue;
      if (fl.test) { if (!fl.test(r, v)) return false; continue; }
      const c = colOf(fl.col || fl.k), x = c ? val(c, r) : r[fl.k];
      if (fl.type === "select" && String(x ?? "") !== String(v)) return false;
      if (fl.type === "from" && !(x && String(x).slice(0, 10) >= v)) return false;
      if (fl.type === "to" && !(x && String(x).slice(0, 10) <= v)) return false;
      if (fl.type === "min" && !(Number(x) >= Number(v))) return false;
      if (fl.type === "max" && !(Number(x) <= Number(v))) return false;
    }
    return true;
  };
  const draw = () => {
    let list = rows.filter(pass);
    if (st.sort && colOf(st.sort)) { const c = colOf(st.sort), m = st.dir === "asc" ? 1 : -1;
      list = list.slice().sort((a, b) => { let x = val(c, a), y = val(c, b); const ex = x == null || x === "", ey = y == null || y === "";
        if (ex && ey) return 0; if (ex) return 1; if (ey) return -1;
        if (c.num) return (Number(x) - Number(y)) * m; return String(x).localeCompare(String(y), undefined, { numeric: true }) * m; }); }
    const shown = list.slice(0, st.n);
    $(".dt-body", host).innerHTML = list.length ? `<div class="tbl"><table><thead><tr>${cols.map(c => `<th class="${c.nosort ? "" : "sort"} ${st.sort === c.k ? "on" : ""} ${c.num ? "num" : ""}" data-sort="${c.k}" ${locked() ? "" : 'draggable="true"'}>${esc(c.l)}${c.nosort ? "" : `<span class="ar">${st.sort === c.k ? (st.dir === "asc" ? "▲" : "▼") : "↕"}</span>`}</th>`).join("")}</tr></thead>
      <tbody>${shown.map((r, i) => `<tr class="${o.rowCls ? o.rowCls(r) : ""} ${o.onRow ? "click" : ""}" data-i="${rows.indexOf(r)}">${cols.map(c => `<td class="${c.num ? "num" : ""} ${c.cls || ""}">${c.h ? c.h(r) : esc(val(c, r) ?? "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      ${list.length > st.n ? `<div class="more"><button class="btn sm" data-more>${esc(L(`Show ${Math.min(100, list.length - st.n)} more of ${list.length - st.n}`, `عرض ${Math.min(100, list.length - st.n)} أخرى من ${list.length - st.n}`))}</button></div>` : ""}`
      : `<div class="empty">${esc(o.empty || L("Nothing matches.", "لا نتائج مطابقة."))}</div>`;
    $(".dt-count", host).textContent = L(`${list.length} of ${rows.length}`, `${list.length} من ${rows.length}`);
    host._list = list;
    if (o.after) o.after(list);
  };
  const fl = (o.filters || []).map(f => {
    const v = st.f[f.k] ?? "";
    if (f.type === "select") return `<label class="f">${esc(f.l)}<select data-f="${f.k}"><option value="">${esc(L("All", "الكل"))}</option>${f.opts.map(([x, l]) => `<option value="${esc(x)}" ${String(x) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    const t = f.type === "from" || f.type === "to" ? "date" : "number";
    return `<label class="f">${esc(f.l)}<input type="${t}" data-f="${f.k}" value="${esc(v)}" ${t === "number" ? `step="any"` : ""}></label>`;
  }).join("");
  host.innerHTML = `<div class="dt-bar"><label class="f dt-q">${esc(L("Search", "بحث"))}<input type="search" data-q value="${esc(st.q)}" placeholder="${esc(o.ph || L("Type to search…", "اكتب للبحث…"))}"></label>${fl}</div>
    <div class="dt-meta"><span class="dt-count"></span><span class="sp" style="flex:1"></span>${(o.filters || []).length ? `<button class="btn sm dt-ftog" data-ftog>${esc(L("Filters", "الفلاتر"))}${Object.values(st.f).filter(v => v !== "" && v != null).length ? ` (${Object.values(st.f).filter(v => v !== "" && v != null).length})` : ""}</button>` : ""}${o.tools || ""}<button class="btn sm" data-reset>${esc(L("Clear filters", "مسح الفلاتر"))}</button><button class="btn sm" data-csv>⬇ CSV</button></div><div class="dt-body"></div>`;
  host.oninput = e => { if (e.target.matches("[data-q]")) { st.q = e.target.value; st.n = 100; keep(); draw(); } };
  host.onchange = e => { const k = e.target.dataset.f; if (k) { st.f[k] = e.target.value; st.n = 100; keep(); draw(); } };
  host.onclick = e => {
    const th = e.target.closest("th.sort"); if (th) { const k = th.dataset.sort; st.dir = st.sort === k && st.dir === "desc" ? "asc" : st.sort === k ? "desc" : (colOf(k).num || colOf(k).date ? "desc" : "asc"); st.sort = k; keep(); draw(); return; }
    if (e.target.closest("[data-more]")) { st.n += 100; draw(); return; }
    if (e.target.closest("[data-ftog]")) { $(".dt-bar", host).classList.toggle("open"); return; }
    if (e.target.closest("[data-reset]")) { st.q = ""; st.f = {}; keep(); table(host, key, rows, o); return; }
    if (e.target.closest("[data-csv]")) { download(`${key}_${today()}.csv`, csv([cols.filter(c => !c.nocsv).map(c => c.l), ...host._list.map(r => cols.filter(c => !c.nocsv).map(c => val(c, r)))])); return; }
    const tr = e.target.closest("tr[data-i]"); if (tr && o.onRow && !e.target.closest("a,button,input,select")) o.onRow(rows[+tr.dataset.i]);
  };
  host.dataset.dt = key;
  const sortBy = (k, dir) => { st.sort = k; st.dir = dir; keep(); draw(); };
  const copy = async t => { try { await navigator.clipboard.writeText(t); toast(L("Copied.", "تم النسخ.")); } catch { ask("", t); } };
  host._dt = {
    colCtx: (e, k) => { const c = colOf(k), i = cols.findIndex(x => x.k === k), hid = cl.hidden || [];
      ctxMenu(e, [!c.nosort && { l: L("Sort ascending", "ترتيب تصاعدي"), ic: "▲", a: () => sortBy(k, "asc") }, !c.nosort && { l: L("Sort descending", "ترتيب تنازلي"), ic: "▼", a: () => sortBy(k, "desc") },
        ...(locked() ? ["-", lockItem()] : ["-",
        { l: L("Rename column…", "إعادة تسمية العمود…"), ic: "✎", a: async () => { const v = await ask(L("Column name (blank: the original)", "اسم العمود (فارغ: الأصلي)"), cols[i].l); if (v === null) return;
          editCols(x => { x.names = x.names || {}; setName(x.names, k, v.trim() === c.l ? "" : v.trim()); }); } },
        { l: L("Move left", "تحريك لليسار"), ic: "←", dis: S.lang === "ar" ? i === cols.length - 1 : i === 0, a: () => editCols(x => { const j = x.order.indexOf(k), t = j + (S.lang === "ar" ? 1 : -1); if (t < 0 || t >= x.order.length) return; [x.order[j], x.order[t]] = [x.order[t], x.order[j]]; }) },
        { l: L("Move right", "تحريك لليمين"), ic: "→", dis: S.lang === "ar" ? i === 0 : i === cols.length - 1, a: () => editCols(x => { const j = x.order.indexOf(k), t = j + (S.lang === "ar" ? -1 : 1); if (t < 0 || t >= x.order.length) return; [x.order[j], x.order[t]] = [x.order[t], x.order[j]]; }) },
        { l: L("Hide column", "إخفاء العمود"), ic: "⊘", dis: cols.length < 2, a: () => editCols(x => { x.hidden = [...new Set([...(x.hidden || []), k])]; }) },
        ...o.cols.filter(x => hid.includes(x.k)).map(x => ({ l: L(`Show “${x.l}”`, `إظهار «${x.l}»`), ic: "◉", a: () => editCols(y => { y.hidden = (y.hidden || []).filter(h => h !== x.k); }) })),
        { l: L("Reset columns", "إعادة الأعمدة لأصلها"), ic: "↺", a: () => editLay(y => { if (y.cols) delete y.cols[key]; }) }, lockItem()])]); },
    rowCtx: (e, tr, td) => { const r = rows[+tr.dataset.i];
      ctxMenu(e, [o.onRow && { l: L("Open", "فتح"), ic: "↗", a: () => o.onRow(r) },
        td && { l: L("Copy this cell", "نسخ هذه الخلية"), ic: "⧉", a: () => copy(td.innerText.trim()) },
        { l: L("Copy the row", "نسخ السطر"), ic: "⧉", a: () => copy(cols.map(c => String(val(c, r) ?? "")).join("\t")) }]); },
  };
  // drag a heading onto another to move the column there
  host.ondragstart = e => { const th = e.target.closest("th[data-sort]"); if (!th || locked()) return; host._drag = th.dataset.sort; e.dataTransfer.effectAllowed = "move"; try { e.dataTransfer.setData("text/plain", th.dataset.sort); } catch { /* old browsers */ } };
  host.ondragover = e => { if (host._drag && e.target.closest("th[data-sort]")) e.preventDefault(); };
  host.ondrop = e => { const th = e.target.closest("th[data-sort]"), k = host._drag; host._drag = null; if (!th || !k || th.dataset.sort === k) return; e.preventDefault();
    editCols(x => { x.order = x.order.filter(h => h !== k); x.order.splice(x.order.indexOf(th.dataset.sort), 0, k); }); };
  draw();
}

// ================================================================== words used in many places
const ST = () => ({ trial: [L("Free trial", "تجربة مجانية"), "warn"], active: [L("Paying", "مدفوع"), "ok"], past_due: [L("Payment due", "مستحق الدفع"), "warn"],
  expired: [L("Ended", "منتهي"), "bad"], unpaid: [L("Not subscribed", "غير مشترك"), "bad"], comped: [L("Free", "مجاني"), "info"],
  free: [L("Billing off", "الاشتراكات معطلة"), ""], suspended: [L("Suspended", "موقوفة"), "bad"], archived: [L("Archived", "مؤرشفة"), ""] });
const stChip = s => { const x = ST()[s] || [s || "—", ""]; return chip(x[0], x[1]); };
// the ERP's menu sections (as in the app's menu), which plans include or sell on their own
const SECTIONS = () => [["dashboard", L("Dashboard & calculators", "لوحة التحكم والحاسبات")], ["chat", L("Team chat", "محادثة الفريق")], ["team", L("Knowledge base & alerts", "قاعدة المعرفة والتنبيهات")],
  ["accounting", L("General accounting, loans", "المحاسبة العامة والقروض")], ["reports", L("Financial reports", "التقارير المالية")], ["tax", L("Taxation & e-invoicing", "الضرائب والفاتورة الإلكترونية")],
  ["sales", L("Sales & receivables", "المبيعات والمدينون")], ["purchasing", L("Procurement & payables", "المشتريات والدائنون")], ["stock", L("Inventory", "المخزون")],
  ["crm", L("Customers & deals", "العملاء والصفقات")], ["projects", L("Projects & tasks", "المشروعات والمهام")], ["contracts", L("Contracts, variations, certificates", "العقود والأوامر التغييرية والمستخلصات")],
  ["estimating", L("Estimating & tendering", "التسعير والعطاءات")], ["business_units", L("Business units", "وحدات الأعمال")], ["shop", L("Showroom & unit sales", "المعرض ومبيعات الوحدات")],
  ["equipment", L("Plant & equipment", "المعدات والآلات")], ["rentals", L("Equipment rental", "تأجير المعدات")], ["payroll", L("Human resources & payroll", "الموارد البشرية والرواتب")],
  ["assets", L("Assets & treasury", "الأصول والخزينة")], ["partners", L("Owners' equity", "حقوق الملكية")], ["care", L("Healthcare", "الرعاية الصحية")],
  ["import_export", L("Import & export", "الاستيراد والتصدير")], ["settings", L("Settings & automations", "الإعدادات والأتمتة")], ["users", L("Users & audit", "المستخدمون والتدقيق")]];
const secName = k => (SECTIONS().find(s => s[0] === k) || [k, k])[1];
// industries in the order the ERP lists them (erp/industries.py ORDER), then any added in the console
const IND_ORDER = ["general", "interior", "contracting", "development", "sales", "investment", "scaffolding", "healthcare", "homecare",
  "restaurant", "services", "manufacturing", "logistics"];
const industriesOf = cfg => { const rank = k => IND_ORDER.includes(k) ? IND_ORDER.indexOf(k) : 99;
  return Object.entries(cfg.core_by_industry || {}).sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0])); };
// sections → screens → tabs of the ERP, named as the ERP names them (its i18n.js). Tabs: as erp/db.py SUBTABS.
const TABS = () => ({ "shop.subscriptions": [["plans", L("Plans tab", "تبويب الخطط")]], "payroll.attendance": [["summary", L("Summary and productivity tab", "تبويب الملخص والإنتاجية")]],
  "reports.forecast": [["prices", L("Price list", "قائمة الأسعار")], ["items", L("Items and materials", "الأصناف والمواد")], ["expenses", L("Spending by account", "المصروفات حسب الحساب")],
    ["payroll", L("Payroll", "الرواتب")], ["depreciation", L("Depreciation and amortisation", "الإهلاك والاستهلاك")], ["loans", L("Loan instalments", "أقساط القروض")], ["sales", L("Sales", "المبيعات")]] });
const erpLabel = k => { const x = (window.I18N || {})[k]; return x ? (S.lang === "ar" ? x[1] : x[0]) : k; };
const TREE = () => { const sp = window.SUBPERM || {}, tabs = TABS();
  return SECTIONS().map(([m, l]) => ({ k: m, l, kids: Object.keys(sp).filter(k => k.split(".")[0] === m).map(k => ({ k, l: erpLabel(sp[k]), kids: (tabs[k] || []).map(([t, tl]) => ({ k: `${k}.${t}`, l: tl, kids: [] })) })) })); };
const keyName = k => { for (const s of TREE()) { if (s.k === k) return s.l; for (const c of s.kids) { if (c.k === k) return `${s.l} › ${c.l}`; for (const t of c.kids) if (t.k === k) return `${s.l} › ${c.l} › ${t.l}`; } } return k; };
// a picker over the whole tree. mode "off": a tick switches it off. mode "tri": as the plan / off / on (given back).
function treeHtml(val, mode) {
  const node = (n, lvl) => mode === "tri"
    ? `<div class="tn l${lvl}"><span>${esc(n.l)}</span><select name="t:${n.k}" class="tsel ${val[n.k] || ""}" data-change="tsel">${[["", L("As its plan", "حسب الباقة")], ["off", L("Off", "متوقف")], ["on", L("On (given back)", "متاح (استثناء)")]].map(([v, l]) => `<option value="${v}" ${(val[n.k] || "") === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`
    : `<label class="tn l${lvl} chk"><input type="checkbox" name="t:${n.k}" ${val[n.k] === "off" ? "checked" : ""}> ${esc(n.l)}</label>`;
  const open = s => [s, ...s.kids, ...s.kids.flatMap(c => c.kids)].some(n => val[n.k]);
  return `<div class="tree"><input type="search" class="tfind" placeholder="${esc(L("Find a module, screen or tab…", "ابحث عن قسم أو شاشة أو تبويب…"))}">
    ${TREE().map(sct => `<div class="tsec" data-find="${esc([sct.l, ...sct.kids.map(c => c.l), ...sct.kids.flatMap(c => c.kids.map(t => t.l))].join(" ").toLowerCase())}">${node(sct, 1)}
      ${sct.kids.length ? `<details ${open(sct) ? "open" : ""}><summary>${esc(L(`${sct.kids.length} screens`, `${sct.kids.length} شاشات`))}</summary>${sct.kids.map(c => node(c, 2) + c.kids.map(t => node(t, 3)).join("")).join("")}</details>` : ""}</div>`).join("")}</div>`;
}
const treeRead = (v, want) => Object.keys(v).filter(k => k.startsWith("t:") && (want === true ? v[k] === true : v[k] === want)).map(k => k.slice(2));
const treeWire = f => { const q = $(".tfind", f); if (q) q.addEventListener("input", () => { const t = q.value.trim().toLowerCase();
  $$(".tsec", f).forEach(x => { x.hidden = !!t && !x.dataset.find.includes(t); if (t && !x.hidden) { const d = $("details", x); if (d) d.open = true; } }); }); };
const ORDER_ST = () => ({ open: [L("Not paid yet", "لم يُدفع"), "warn"], submitted: [L("To verify", "للتحقق"), "info"], approved: [L("Approved", "معتمد"), "ok"],
  rejected: [L("Not accepted", "مرفوض"), "bad"], cancelled: [L("Cancelled", "ملغى"), ""] });
const TK_ST = () => ({ open: [L("Open", "مفتوح"), "warn"], answered: [L("Answered", "تم الرد"), "info"], waiting: [L("Waiting for customer", "بانتظار العميل"), "info"],
  resolved: [L("Resolved", "تم الحل"), "ok"], closed: [L("Closed", "مغلق"), ""] });
const PRIO = () => ({ low: [L("Low", "منخفضة"), ""], normal: [L("Normal", "عادية"), ""], high: [L("High", "مرتفعة"), "warn"], urgent: [L("Urgent", "عاجلة"), "bad"] });
const VIS_ST = () => ({ requested: [L("Requested", "مطلوبة"), "warn"], quoted: [L("Price sent", "أُرسل السعر"), "info"], accepted: [L("Accepted — to schedule", "مقبولة — للجدولة"), "warn"],
  scheduled: [L("Scheduled", "مجدولة"), "info"], done: [L("Done", "تمت"), "ok"], declined: [L("Declined by customer", "رفضها العميل"), ""], cancelled: [L("Cancelled", "ملغاة"), ""], rejected: [L("Not possible", "غير ممكنة"), "bad"] });
const PAY_ST = () => ({ none: [L("Not paid", "غير مدفوعة"), ""], submitted: [L("Payment to check", "دفعة للتحقق"), "warn"], paid: [L("Paid", "مدفوعة"), "ok"], waived: [L("No charge", "بدون رسوم"), "info"] });
const mapChip = (m, k) => { const x = m[k] || [k, ""]; return chip(x[0], x[1]); };
const KIND_ICON = { instapay: "⚡", wallet: "📱", bank: "🏦", card: "💳", cash: "💵", other: "•" };
const fileList = (bucket, files) => (files || []).length ? `<div class="files">${files.map(f => `<button type="button" class="file" data-a="file" data-b="${bucket}" data-p="${esc(f.path)}" data-t="${esc(f.type || "")}" title="${esc(f.name)}"><i class="prev"></i><span>${/^image\//.test(f.type) ? "🖼" : /^video\//.test(f.type) ? "🎬" : /^audio\//.test(f.type) ? "🎤" : "📄"} ${esc(f.name || "file")}</span></button>`).join("")}</div>` : "";
async function previews(view) {
  for (const b of $$(".file[data-p]", view)) { const t = b.dataset.t || "", box = $(".prev", b); if (!box || box.dataset.done || !/^(image|video|audio)\//.test(t)) continue; box.dataset.done = 1;
    try { const u = await fileUrl(b.dataset.b, b.dataset.p); b.dataset.url = u;
      if (/^audio/.test(t)) { const au = document.createElement("audio"); au.controls = true; au.src = u; au.preload = "metadata"; au.className = "voice"; b.replaceWith(au); }
      else box.outerHTML = /^image/.test(t) ? `<img src="${u}" alt="">` : `<video src="${u}" muted preload="metadata"></video>`; } catch { /* listed only */ } }
}

// ================================================================== routing
const parse = () => { const [p, q] = location.hash.replace(/^#\/?/, "").split("?"); return { parts: (p || (DESK ? "queue" : "overview")).split("/").filter(Boolean), q: Object.fromEntries(new URLSearchParams(q || "")) }; };
const setQ = q => { const { parts } = parse(); const s = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== "" && v != null)).toString(); location.hash = "#/" + parts.join("/") + (s ? "?" + s : ""); };
const PAGES = {};
let routing = 0;
async function route() {
  if (!S.me) return;
  const { parts, q } = parse(), page = parts[0];
  const item = NAV().flatMap(([, it]) => it).find(i => i[0] === page);
  S.H = {};
  $("#app") && $("#app").classList.remove("menu");
  $$(".nav a").forEach(a => a.classList.toggle("on", a.dataset.nav === page || (page === "company" && a.dataset.nav === "customers") || (page === "ticket" && a.dataset.nav === "queue") || (page === "visit" && a.dataset.nav === "visits")));
  dockSync();
  const view = $("#view"), mine = ++routing;
  if (item && !navOk(item[3])) { view.innerHTML = `<div class="banner err">${esc(L("Your team account does not have access to this.", "لا يملك حساب فريقك صلاحية الوصول إلى هذا."))}</div>`; return; }
  $("#ttl").textContent = item ? item[2] : ""; $("#crumb").textContent = "";
  view.innerHTML = `<div class="empty"><div class="boot-ring" style="margin:0 auto"></div></div>`;
  clearInterval(S.live);
  const fn = PAGES[page] || (DESK ? PAGES.queue : PAGES.overview);
  try { const box = document.createElement("div"); await fn(box, parts, q); if (mine === routing) { view.replaceChildren(...box.childNodes); previews(view); afterDraw(view); } }
  catch (e) { if (mine === routing) view.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; }
}
let afterHooks = [];
// sliders show their filled part (WebKit has no built-in fill): kept in step with the value wherever a slider appears
const syncRange = el => { const lo = +el.min || 0, hi = el.max === "" ? 100 : +el.max, v = +el.value;
  el.style.setProperty("--p", `${hi > lo ? Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100)) : 0}%`); };
document.addEventListener("input", e => { if (e.target.type === "range") syncRange(e.target); }, true);
new MutationObserver(() => $$("input[type=range]").forEach(syncRange)).observe(document.documentElement, { childList: true, subtree: true });
const afterDraw = view => { const h = afterHooks; afterHooks = []; h.forEach(f => f(view)); };
window.addEventListener("hashchange", route);

// ================================================================== clicks and changes anywhere
document.addEventListener("click", async e => {
  if (!e.target.closest("#ctx")) closeCtx();
  const app = $("#app");
  if (app && app.classList.contains("menu") && !e.target.closest(".side,[data-a=menu]")) { app.classList.remove("menu"); e.preventDefault(); return; }   // a tap outside the side menu closes it
  const pop = $("#alpop"); if (pop && !e.target.closest("#bell")) pop.remove();
  const el = e.target.closest("[data-a]"); if (!el) return;
  const a = el.dataset.a, G = GLOBAL[a] || S.H[a];
  if (!G) return;
  if (el.tagName === "BUTTON" || el.dataset.prevent) e.preventDefault();
  try { await G(el, e); } catch (er) { fail(er); }
});
document.addEventListener("input", e => { const el = e.target.closest("[data-input]"); if (el && S.H[el.dataset.input]) S.H[el.dataset.input](el, e); });
document.addEventListener("change", e => { const el = e.target.closest("[data-change]"); if (el && S.H[el.dataset.change]) Promise.resolve(S.H[el.dataset.change](el, e)).catch(fail); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { if ($("#ctx")) return closeCtx(); const app = $("#app"); if (app && app.classList.contains("menu")) return app.classList.remove("menu"); closePanel(); const p = $("#alpop"); if (p) p.remove(); } });
document.addEventListener("contextmenu", e => {
  if (!S.me || e.target.closest("input,textarea,select,[contenteditable],#ctx")) return;     // typing keeps the browser's own menu
  const a = e.target.closest(".nav a[data-nav]"); if (a) return navCtx(e, a.dataset.nav);
  const h = e.target.closest(".nav-h[data-g]"); if (h) return groupCtx(e, h.dataset.g);
  const host = e.target.closest("[data-dt]"); if (!host || !host._dt) return;
  const th = e.target.closest("th[data-sort]"); if (th) return host._dt.colCtx(e, th.dataset.sort);
  const tr = e.target.closest("tr[data-i]"); if (tr) return host._dt.rowCtx(e, tr, e.target.closest("td"));
});
addEventListener("resize", closeCtx); addEventListener("scroll", closeCtx, true);
const GLOBAL = {
  menu: () => $("#app").classList.toggle("menu"),
  unlock: () => editLay(y => { y.locked = false; }),
  mode: () => { const cur = document.documentElement.dataset.mode; LSset("berp.console.mode", cur === "dark" ? "light" : "dark"); applyLook(); },
  lang: () => { S.lang = S.lang === "ar" ? "en" : "ar"; LSset("berp.console.lang", S.lang); applyLook(); shell(); route(); },
  signout: async () => { if (!await confirmIt(L("Sign out of the console on this device? (The ERP on this device signs out too.)", "تسجيل الخروج من اللوحة على هذا الجهاز؟ (يُسجَّل الخروج من النظام أيضاً.)"))) return; putSes(null); location.reload(); },
  bell: el => {
    const old = $("#alpop"); if (old) { old.remove(); return; }
    const d = document.createElement("div"); d.className = "pop"; d.id = "alpop";
    const list = S.alerts.slice(0, 30);
    d.innerHTML = `<div class="row" style="padding:4px 6px 8px"><b>${esc(L("Notifications", "الإشعارات"))}</b><span class="sp"></span>
      <button class="btn sm" data-a="alreadall">${esc(L("Mark all read", "تعليم الكل كمقروء"))}</button><a class="btn sm" href="#/alerts">${esc(L("All", "الكل"))}</a></div>
      ${list.length ? list.map(alertHtml).join("") : `<div class="empty">${esc(L("All caught up.", "لا جديد."))}</div>`}`;
    el.parentElement.appendChild(d);
  },
  alopen: async (el, e) => { if (e.target.closest("[data-a=aldismiss]")) return; await markAlerts([el.dataset.key], "read"); const p = $("#alpop"); if (p) p.remove(); },
  aldismiss: async (el, e) => { e.preventDefault(); e.stopPropagation(); await markAlerts([el.dataset.key], "dismissed"); el.closest(".al").remove(); if (parse().parts[0] === "alerts") route(); },
  alreadall: async () => { await markAlerts(S.alerts.filter(a => !a.read).map(a => a.key), "read"); const p = $("#alpop"); if (p) p.remove(); if (parse().parts[0] === "alerts") route(); },
  file: async el => {
    const u = el.dataset.url || await fileUrl(el.dataset.b, el.dataset.p); el.dataset.url = u; const t = el.dataset.t || "";
    if (/^(image|video)\//.test(t)) {
      const d = document.createElement("div"); d.className = "shade top";
      d.innerHTML = `<div style="display:flex;flex-direction:column;gap:10px;align-items:center;max-width:100%">${/^image/.test(t) ? `<img src="${u}" style="max-width:min(1100px,100%);max-height:80vh;border-radius:10px">` : `<video src="${u}" controls autoplay style="max-width:min(1100px,100%);max-height:80vh;border-radius:10px"></video>`}
        <a class="btn" href="${u}" download="${esc(el.title)}">⬇ ${esc(L("Download", "تنزيل"))}</a></div>`;
      d.addEventListener("click", ev => { if (ev.target === d) d.remove(); }); document.body.appendChild(d);
    } else window.open(u, "_blank", "noopener");
  },
  copy: async el => { try { await navigator.clipboard.writeText(el.dataset.v); toast(L("Copied.", "تم النسخ.")); } catch { ask("", el.dataset.v); } },
};

// ================================================================== signing in
function signIn(msg) {
  document.body.innerHTML = `<div class="signin"><form class="card" id="sf"><div class="brand" style="padding:0 0 14px"><span class="mark">B</span><div><b>${esc(({ ...LOOK0, ...(S.look || {}) }).name)}</b><small class="muted">${esc(CNAME())}</small></div></div>
    ${msg ? `<div class="banner ${msg.ok ? "ok" : "err"}">${esc(msg.text || msg)}</div>` : ""}
    <div class="grid" style="gap:10px"><label class="f">${esc(L("Email", "البريد الإلكتروني"))}<input name="email" type="email" required autocomplete="username" dir="ltr"></label>
    <label class="f">${esc(L("Password", "كلمة المرور"))}<input name="password" type="password" required autocomplete="current-password"></label>
    <label class="f" id="codebox" hidden>${esc(L("Code from your authenticator app", "الرمز من تطبيق المصادقة"))}<input name="code" inputmode="numeric" autocomplete="one-time-code" dir="ltr"></label>
    <button class="btn pri" type="submit">${esc(L("Sign in", "تسجيل الدخول"))}</button>
    <p class="muted small" style="margin:0">${esc(L("Use the same account as in the ERP. Only people added to the team can open this console.", "استخدم نفس حساب النظام. لا يفتح هذه اللوحة إلا من أُضيف إلى الفريق."))}</p>
    <div class="row"><a href="index.html">← ${esc(L("Back to the ERP", "العودة إلى النظام"))}</a><span class="sp"></span><button type="button" class="btn sm" data-a="lang">${S.lang === "ar" ? "English" : "عربي"}</button></div></div></form></div>`;
  let factor = null;
  $("#sf").addEventListener("submit", async e => {
    e.preventDefault(); const v = readForm(e.target), b = $("button[type=submit]", e.target); b.disabled = true;
    try {
      if (!factor) {
        saveSes(await req("POST", "/auth/v1/token?grant_type=password", { email: v.email.trim(), password: v.password }));
        const u = await req("GET", "/auth/v1/user", undefined, await token());
        const f = (u.factors || []).find(x => x.factor_type === "totp" && x.status === "verified");
        if (f) { factor = f.id; $("#codebox").hidden = false; $("[name=code]").focus(); b.disabled = false; return; }
      } else {
        const tk = await token(), ch = await req("POST", `/auth/v1/factors/${factor}/challenge`, {}, tk);
        saveSes(await req("POST", `/auth/v1/factors/${factor}/verify`, { challenge_id: ch.id, code: String(v.code || "").replace(/\D/g, "") }, tk));
      }
      start();
    } catch (er) { b.disabled = false; toast(/invalid login/i.test(er.message) ? L("Wrong email or password.", "البريد أو كلمة المرور غير صحيحة.") : er.message, true); }
  });
}

// ================================================================== start
async function start() {
  applyLook();
  if (!cfg()) { document.body.innerHTML = `<div class="signin"><div class="card"><h1>${esc(CNAME())}</h1><p>${esc(L("This copy is not connected to a workspace. Open the ERP, connect it (Settings → Workspace), then come back.", "هذه النسخة غير متصلة بمساحة عمل. افتح النظام واربطه (الإعدادات ← مساحة العمل) ثم عُد."))}</p><a class="btn pri" href="index.html">${esc(L("Open the ERP", "فتح النظام"))}</a></div></div>`; return; }
  if (!getSes()) return signIn();
  try { S.me = await rpc("console_me"); }
  catch (e) {
    if (e.code === "signed_out" || e.status === 401) return signIn();
    putSes(null);
    return signIn(/platform team/.test(e.message) ? L("This account is not on the platform team. Ask the owner to add you (Team).", "هذا الحساب ليس ضمن فريق المنصة. اطلب من المالك إضافتك (الفريق).") : e.message);
  }
  S.look = S.me.look || null; applyLook(); shell();
  await loadAlerts(true);
  route();
  clearInterval(S.poll);
  S.poll = setInterval(async () => { if (document.hidden && !("Notification" in window && Notification.permission === "granted")) return;
    await loadAlerts(false); try { const me = await rpc("console_me"); S.me.badges = me.badges; for (const [k, n] of [["payments", me.badges.orders], ["queue", me.badges.tickets], ["visits", me.badges.visits]]) { const s = $(`[data-badge="${k}"]`); if (s) s.innerHTML = n ? `<span class="cnt">${n}</span>` : ""; } } catch { /* next time */ } }, 60000);
}

// ================================================================== overview
function revenueChart(series) {
  if (!series.length) return `<div class="empty">${esc(L("No data for this period.", "لا بيانات لهذه الفترة."))}</div>`;
  const W = 760, H = 250, pb = 26, max = Math.max(1, ...series.map(s => Math.max(+s.expected, +s.real)));
  const step = W / series.length, y = v => (H - pb) - v / max * (H - pb - 14), x = i => i * step + step / 2;
  const line = k => series.map((s, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(+s[k]).toFixed(1)}`).join(" ");
  const area = `${line("real")} L${x(series.length - 1).toFixed(1)} ${H - pb} L${x(0).toFixed(1)} ${H - pb} Z`;
  const every = Math.max(1, Math.ceil(series.length / 12));
  return `<div class="chart" id="revchart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${[.25, .5, .75, 1].map(f => `<line x1="0" x2="${W}" y1="${y(max * f)}" y2="${y(max * f)}" class="gl"/>`).join("")}
    <path d="${area}" class="area"/><path d="${line("expected")}" class="ln e"/><path d="${line("real")}" class="ln r"/>
    ${series.map((s, i) => `<circle cx="${x(i)}" cy="${y(+s.expected)}" r="3" class="pt e"/><circle cx="${x(i)}" cy="${y(+s.real)}" r="3.4" class="pt r"/>`).join("")}
    ${series.map((s, i) => `<g data-tip="${esc(s.period)}&#10;${esc(L("Expected", "المتوقع"))}: ${money(s.expected)}&#10;${esc(L("Received", "المحصّل"))}: ${money(s.real)}"><rect x="${x(i) - step / 2}" y="0" width="${step}" height="${H}" class="hit"/>${i % every === 0 ? `<text x="${x(i)}" y="${H - 8}">${esc(s.period.length > 7 ? s.period.slice(5) : s.period.slice(2))}</text>` : ""}</g>`).join("")}</svg><div class="tip" hidden></div></div>
    <div class="legend"><span class="e">● ${esc(L("Expected (periods falling due)", "المتوقع (فترات مستحقة)"))}</span><span class="r">● ${esc(L("Received", "المحصّل"))}</span></div>`;
}
function chartTips(view) {
  const c = $("#revchart", view); if (!c) return; const tip = $(".tip", c);
  const show = (g, cx) => { $$("g.on", c).forEach(o => o.classList.remove("on")); if (!g) { tip.hidden = true; return; } g.classList.add("on"); tip.hidden = false; tip.textContent = g.dataset.tip;
    const r = c.getBoundingClientRect(); tip.style.left = Math.min(r.width - tip.offsetWidth - 4, Math.max(4, cx - r.left - tip.offsetWidth / 2)) + "px"; };
  c.addEventListener("pointermove", e => show(e.target.closest("g[data-tip]"), e.clientX));
  c.addEventListener("pointerleave", () => show(null));
}
PAGES.overview = async (view, parts, q) => {
  const range = q.range || "12m", useDates = q.from && q.to;
  const back = { "12m": [11, "month"], "6m": [5, "month"], "90d": [89, "day"], "30d": [29, "day"], "7d": [6, "day"] }[range] || [11, "month"];
  const to = useDates ? q.to : today();
  const from = useDates ? q.from : (() => { const d = new Date(); if (back[1] === "month") { d.setDate(1); d.setMonth(d.getMonth() - back[0]); } else d.setDate(d.getDate() - back[0]); return d.toISOString().slice(0, 10); })();
  const by = useDates ? ((new Date(q.to) - new Date(q.from)) / 86400000 > 62 ? "month" : "day") : back[1];
  const [d, cos, pays] = await Promise.all([rpc("console_overview", { p_from: from, p_to: to, p_by: by }).catch(e => { if (!can("overview")) return null; throw e; }),
    can("customers") ? rpc("console_companies") : [], can("billing") ? rpc("console_payments").catch(() => []) : []]);
  const cur = (d && d.currency) || "EGP", c = (d && d.counts) || {};
  const due = cos.filter(x => x.state.ends && !["archived", "comped", "free"].includes(x.state.status)).sort((a, b) => String(a.state.ends).localeCompare(String(b.state.ends)));
  const soon = due.filter(x => daysTo(x.state.ends) >= 0).slice(0, 8), recent = pays.filter(p => p.status === "paid").slice(0, 8);
  S.H.range = el => setQ({ range: el.dataset.r });
  S.H.dates = () => { const f = $("[name=cfrom]").value, t = $("[name=cto]").value; if (f && t) setQ({ from: f, to: t }); };
  S.H.export = () => download(`revenue_${from}_${to}.csv`, csv([["period", "expected", "received"], ...d.series.map(s => [s.period, s.expected, s.real])]));
  S.H.pick = el => { const x = cos.find(cc => cc.id === el.value); $("#insight").innerHTML = x ? insight(x, cur) : insightEmpty(); };
  view.innerHTML = `<p class="hello">${esc(L(`Hello ${S.me.name} —`, `مرحباً ${S.me.name} —`))} <b>${esc(L("here's what's happening with the business today", "إليك ما يحدث في الأعمال اليوم"))}</b></p>
    ${S.me.billing_enabled === false ? `<div class="banner warn">${esc(L("Billing is switched off: the app is free for everyone. Switch it on in Rules & switches.", "الاشتراكات متوقفة: التطبيق مجاني للجميع. فعّلها من القواعد والمفاتيح."))} <a href="#/settings">${esc(L("Open", "فتح"))}</a></div>` : ""}
    ${d ? `<div class="card"><div class="row" style="margin-bottom:6px">
      ${[["12m", L("12 months", "12 شهراً")], ["6m", L("6 months", "6 أشهر")], ["90d", L("90 days", "90 يوماً")], ["30d", L("30 days", "30 يوماً")], ["7d", L("7 days", "7 أيام")]].map(([k, l]) => `<button class="btn sm ${!useDates && range === k ? "pri" : ""}" data-a="range" data-r="${k}">${esc(l)}</button>`).join("")}
      <span class="sp"></span><input type="date" name="cfrom" value="${esc(from)}" style="width:auto"> → <input type="date" name="cto" value="${esc(to)}" style="width:auto"><button class="btn sm" data-a="dates">${esc(L("Apply", "تطبيق"))}</button>
      <button class="btn sm" data-a="export">⬇ ${esc(L("Export", "تصدير"))}</button></div>${revenueChart(d.series)}</div>
    <div class="kpis">${kpi(L("Monthly recurring revenue", "الإيراد الشهري المتكرر"), money(d.mrr), "", esc(cur))}${kpi(L("Received this month", "المحصّل هذا الشهر"), money(d.received_this_month), "", esc(cur))}
      ${kpi(L("Paying", "مدفوع"), num((c.active || 0) + (c.past_due || 0)), "ok", c.past_due ? esc(L(`${c.past_due} payment due`, `${c.past_due} مستحق`)) : "", "#/customers?st=active")}
      ${kpi(L("On trial", "تجربة"), num(c.trial || 0), "", "", "#/customers?st=trial")}${kpi(L("Not paying", "غير مدفوع"), num((c.unpaid || 0) + (c.expired || 0)), (c.unpaid || 0) + (c.expired || 0) ? "warn" : "", "", "#/customers?st=expired")}
      ${kpi(L("Payments to verify", "مدفوعات للتحقق"), num(d.orders_waiting), d.orders_waiting ? "warn" : "", "", "#/payments")}
      ${kpi(L("Open support tickets", "بلاغات مفتوحة"), num(d.tickets_open), d.tickets_unread ? "warn" : "", d.tickets_unread ? esc(L(`${d.tickets_unread} unread`, `${d.tickets_unread} غير مقروء`)) : "", "#/support")}
      ${kpi(L("Companies · people", "الشركات · الأفراد"), `${num(d.companies)} · ${num(d.people)}`, "", esc(L(`${d.new_companies_30d} new in 30 days`, `${d.new_companies_30d} جديدة خلال 30 يوماً`)), "#/customers")}</div>` : ""}
    <div class="split"><div>
      <div class="card"><div class="row"><h2 style="margin:0">${esc(L("Next due", "الأقرب استحقاقاً"))}</h2><span class="sp"></span><a class="btn sm" href="#/customers">${esc(L("All", "الكل"))}</a></div>
        ${soon.length ? `<div class="list">${soon.map(x => `<a class="li" href="#/company/${x.id}">${avatar(x.name)}<span class="who"><b>${esc(x.name)}</b><small class="muted">${esc(x.created_by || "")}</small></span>${stChip(x.state.status)}
          <span class="amt"><b>${esc(day(x.state.ends))}</b><small class="muted">${esc(L(`in ${daysTo(x.state.ends)} days`, `خلال ${daysTo(x.state.ends)} يوم`))}</small></span></a>`).join("")}</div>` : `<div class="empty">${esc(L("Nothing falling due.", "لا شيء مستحق."))}</div>`}</div>
      ${recent.length ? `<div class="card"><div class="row"><h2 style="margin:0">${esc(L("Latest payments", "أحدث المدفوعات"))}</h2><span class="sp"></span><a class="btn sm" href="#/ledger">${esc(L("All", "الكل"))}</a></div>
        <div class="list">${recent.map(p => `<a class="li" href="${p.company_id ? "#/company/" + p.company_id : "#/ledger"}">${avatar(p.company || "?")}<span class="who"><b>${esc(p.company || "")}</b><small class="muted">${esc(p.plan)} · ${esc(p.months)} ${esc(L("mo", "شهر"))}</small></span>
          <span class="amt"><b>${money(p.amount)} ${esc(p.currency)}</b><small class="muted">${esc(day(p.paid_at || p.created_at))}</small></span></a>`).join("")}</div></div>` : ""}</div>
      <div><div class="card"><h2>${esc(L("Company insight", "نظرة على شركة"))}</h2>${cos.length ? `<select data-change="pick"><option value="">${esc(L("Search for a company…", "ابحث عن شركة…"))}</option>${cos.slice().sort((a, b) => a.name.localeCompare(b.name)).map(x => `<option value="${x.id}">${esc(x.name)} — ${esc(x.created_by || "")}</option>`).join("")}</select>` : ""}
        <div id="insight">${insightEmpty()}</div></div>
        <div class="card"><h2>${esc(L("Waiting for you", "بانتظارك"))}</h2><div class="list">${S.alerts.filter(a => !a.read).slice(0, 6).map(a => alertHtml(a)).join("") || `<div class="empty">${esc(L("All caught up.", "لا جديد."))}</div>`}</div></div></div></div>`;
  afterHooks.push(chartTips);
};
const insightEmpty = () => `<div class="empty">📊<br><b>${esc(L("Select a company", "اختر شركة"))}</b><br>${esc(L("See where it stands, its use and its support.", "اعرف وضعها واستخدامها ودعمها."))}</div>`;
const insight = (x, cur) => `<div class="kpis" style="grid-template-columns:repeat(2,minmax(0,1fr))">${kpi(L("Status", "الحالة"), stChip(x.state.status))}${kpi(L("Next due", "الاستحقاق التالي"), esc(day(x.state.ends)))}
  ${kpi(L("People", "الأفراد"), num(x.people))}${kpi(L("Paid in total", "إجمالي المدفوع"), money(x.paid_total), "", esc(cur))}${kpi(L("Last activity", "آخر نشاط"), esc(ago(x.last_change)))}${kpi(L("Open tickets", "بلاغات مفتوحة"), num(x.tickets_open), x.tickets_open ? "warn" : "")}</div>
  <a class="btn pri" href="#/company/${x.id}">${esc(L("Open the company", "فتح الشركة"))} →</a>`;

// ================================================================== notifications page (and what to be told about)
PAGES.alerts = async view => {
  await loadAlerts(true);
  const kinds = AL_KIND(), mute = S.me.notify || {};
  S.H.allread = () => GLOBAL.alreadall();
  S.H.dismissall = async () => { const keys = (view._list || $("#altbl")._list || []).map(a => a.key); if (!keys.length || !await confirmIt(L(`Dismiss ${keys.length} notification(s)?`, `إخفاء ${keys.length} إشعار؟`))) return; await markAlerts(keys, "dismissed"); route(); };
  S.H.mute = async el => { const m = { ...(S.me.notify || {}) }; if (el.checked) delete m[el.dataset.k]; else m[el.dataset.k] = false; S.me.notify = await rpc("console_my_notify", { p: m }); loadAlerts(true); };
  S.H.perm = async () => { if (!("Notification" in window)) return toast(L("This browser cannot show notifications.", "هذا المتصفح لا يدعم الإشعارات."), true); await Notification.requestPermission(); route(); };
  soundHandlers();
  S.H.dockset = el => { LSset("berp.console.dock", { ...dockPref(), [el.dataset.k]: el.value }); applyDock(); };
  const dockColor = c => { LSset("berp.console.dock", { ...dockPref(), color: c }); applyDock();
    const d = dockPref(), box = $(".dock-colors", view); if (!box) return;
    $$("[data-a=dockcol]", box).forEach(b => b.classList.toggle(b.classList.contains("dock-sw") ? "on" : "pri", (b.dataset.v || "") === d.color.toLowerCase()));
    const pk = $(".dock-sw.pick", box); if (pk) pk.classList.toggle("on", !!d.color && !DOCK_COLORS.includes(d.color.toLowerCase())); };
  S.H.dockcol = el => dockColor(el.dataset.v);
  S.H.dockcolpick = el => dockColor(el.value);
  view.innerHTML = `<div class="split"><div class="card"><div id="altbl"></div></div>
    <div><div class="card"><h2>${esc(L("On this device", "على هذا الجهاز"))}</h2>
      <p class="muted small">${esc(L("While this console is open (even in another tab), new notifications pop up on the screen. Phone and computer notifications for tickets, payments and visits also arrive when it is closed, if notifications are on in the ERP (Profile → Notifications).", "طالما اللوحة مفتوحة (حتى في تبويب آخر) تظهر الإشعارات الجديدة على الشاشة. وتصل إشعارات البلاغات والمدفوعات والزيارات للهاتف والكمبيوتر حتى وهي مغلقة إذا فُعّلت الإشعارات في النظام (الملف الشخصي ← الإشعارات)."))}</p>
      ${"Notification" in window ? (Notification.permission === "granted" ? chip(L("Screen notifications on", "إشعارات الشاشة مفعّلة"), "ok") : `<button class="btn pri" data-a="perm">${esc(L("Turn on screen notifications", "تفعيل إشعارات الشاشة"))}</button>`) : ""}
      ${soundCard()}${dockSelect()}</div>
    <div class="card"><h2>${esc(L("Tell me about", "أبلغني عن"))}</h2><div class="grid" style="gap:6px">${Object.entries(kinds).map(([k, [l, ic]]) => `<label class="chk"><input type="checkbox" data-change="mute" data-k="${k}" ${mute[k] === false ? "" : "checked"}> ${ic} ${esc(l)}</label>`).join("")}</div>
      <p class="muted small">${esc(L("Only what your access covers is shown. Warning times are set in Rules & switches.", "يظهر فقط ما تشمله صلاحياتك. تُضبط أوقات التنبيه من القواعد والمفاتيح."))}</p></div></div></div>`;
  afterHooks.push(v => table($("#altbl", v), "alerts", S.alerts, {
    sort: "at", dir: "desc", search: ["title", "detail", "kindl"], tools: `<button class="btn sm" data-a="allread">${esc(L("Mark all read", "تعليم الكل كمقروء"))}</button><button class="btn sm" data-a="dismissall">${esc(L("Dismiss shown", "إخفاء المعروض"))}</button>`,
    cols: [{ k: "kindl", l: L("What", "ماذا"), v: a => (kinds[a.kind] || [a.kind])[0], h: a => `${(kinds[a.kind] || ["", "•"])[1]} <b>${esc((kinds[a.kind] || [a.kind])[0])}</b>${a.read ? "" : " " + chip(L("New", "جديد"), "info")}` },
      { k: "title", l: L("About", "بخصوص"), h: a => `<a href="${alertLink(a)}" data-a="alopen" data-key="${esc(a.key)}">${esc(a.title)}</a><span class="sub">${esc(a.detail || "")}</span>` },
      { k: "at", l: L("When", "متى"), date: true, h: a => esc(dt(a.at)) },
      { k: "x", l: "", nosort: true, nocsv: true, h: a => `<button class="btn sm ghost" data-a="aldismiss" data-key="${esc(a.key)}">✕</button>` }],
    filters: [{ k: "kind", l: L("Kind", "النوع"), type: "select", opts: Object.entries(kinds).map(([k, v]) => [k, v[0]]), test: (a, v) => a.kind === v },
      { k: "read", l: L("Read", "المقروء"), type: "select", opts: [["new", L("New", "جديد")], ["read", L("Read", "مقروء")]], test: (a, v) => (v === "new") !== !!a.read },
      { k: "from", l: L("From", "من"), type: "from", col: "at" }, { k: "to", l: L("To", "إلى"), type: "to", col: "at" }],
    rowCls: a => a.read ? "" : "unread" }));
};

// the sound part of "On this device": pick a sound or upload one, volume, vibration and quiet hours
const soundCard = () => { const p = sndPrefs(), own = !!sndOwn();
  const opts = [...SOUNDS(), ["custom", L("My sound", "صوتي")], ["none", L("No sound", "بدون صوت")]];
  return `<h3 style="margin-top:14px">${esc(L("Sound", "الصوت"))}</h3>
    <div class="sndgrid">${opts.map(([k, l]) => `<button type="button" class="btn sm ${p.sound === k ? "pri" : ""}" data-a="sndpick" data-k="${k}">${k === "none" ? "🔕" : k === "custom" ? "🎵" : "♪"} ${esc(l)}</button>`).join("")}</div>
    <div class="row" style="gap:8px;margin-top:8px;flex-wrap:wrap;align-items:center">
      <label class="btn sm">⬆ ${esc(own ? L("Replace my sound", "استبدال صوتي") : L("Upload a sound", "رفع صوت"))}<input type="file" id="sndfile" accept="audio/*" hidden></label>
      ${own ? `<button type="button" class="btn sm danger" data-a="sndownx">${esc(L("Remove my sound", "إزالة صوتي"))}</button>` : ""}
      <button type="button" class="btn sm" data-a="sndtest">▶ ${esc(L("Test", "تجربة"))}</button></div>
    <label class="small" style="display:block;margin-top:8px">${esc(L("Volume", "مستوى الصوت"))} <b id="sndvolv">${p.volume}%</b>
      <input type="range" min="0" max="100" step="5" value="${p.volume}" data-change="sndvol" data-input="sndvolshow" aria-label="${esc(L("Volume", "مستوى الصوت"))}"></label>
    ${"vibrate" in navigator ? `<label class="chk" style="margin-top:6px"><input type="checkbox" data-change="sndvib" ${p.vibrate ? "checked" : ""}> ${esc(L("Vibrate (phones)", "اهتزاز (الهواتف)"))}</label>` : ""}
    <label class="chk" style="margin-top:6px"><input type="checkbox" data-change="sndquiet" ${p.quiet ? "checked" : ""}> ${esc(L("No sound during quiet hours", "بدون صوت خلال ساعات الهدوء"))}</label>
    <div class="row" style="gap:8px;margin-top:6px;align-items:center"><input type="time" value="${esc(p.quiet_from)}" data-change="sndfrom" style="max-width:130px"> ${S.lang === "ar" ? "←" : "→"} <input type="time" value="${esc(p.quiet_to)}" data-change="sndto" style="max-width:130px"></div>
    <p class="muted small">${esc(L("Kept on this device. Notifications still arrive silently during quiet hours.", "يُحفظ على هذا الجهاز. تصل الإشعارات بصمت خلال ساعات الهدوء."))}</p>`; };
// the page is drawn off screen and then moved into #view, so these look things up on the live page, not in `view`
function soundHandlers() {
  const redraw = () => route();
  S.H.sndpick = el => { const k = el.dataset.k; if (k === "custom" && !sndOwn()) { const f = $("#sndfile"); if (f) f.click(); return; } sndSave({ sound: k }); playSound(k, sndPrefs().volume); redraw(); };
  S.H.sndtest = () => playSound(sndPrefs().sound === "none" ? "chime" : sndPrefs().sound, sndPrefs().volume);
  S.H.sndownx = () => { LSset("berp.console.sndfile", null); if (sndPrefs().sound === "custom") sndSave({ sound: "chime" }); redraw(); };
  S.H.sndvolshow = el => { const v = $("#sndvolv"); if (v) v.textContent = el.value + "%"; };
  S.H.sndvol = el => { S.H.sndvolshow(el); sndSave({ volume: +el.value }); playSound(sndPrefs().sound, +el.value); };
  S.H.sndvib = el => sndSave({ vibrate: el.checked });
  S.H.sndquiet = el => sndSave({ quiet: el.checked });
  S.H.sndfrom = el => sndSave({ quiet_from: el.value });
  S.H.sndto = el => sndSave({ quiet_to: el.value });
  afterHooks.push(v => { const f = $("#sndfile", v); if (!f) return; f.addEventListener("change", () => { const file = f.files[0]; if (!file) return;
    if (!/^audio\//.test(file.type)) return toast(L("Choose a sound file.", "اختر ملف صوت."), true);
    if (file.size > 290000) return toast(L("The sound is too long. Choose a clip under about 250 KB.", "الصوت طويل جداً. اختر مقطعاً أقل من 250 كيلوبايت تقريباً."), true);
    const r = new FileReader(); r.onload = () => { try { localStorage.setItem("berp.console.sndfile", JSON.stringify(r.result)); } catch { return toast(L("This device has no room to keep the sound.", "لا توجد مساحة على هذا الجهاز لحفظ الصوت."), true); }
      sndSave({ sound: "custom" }); playSound("custom", sndPrefs().volume); toast(L("Saved on this device.", "حُفظ على هذا الجهاز.")); redraw(); }; r.readAsDataURL(file); }); });
}

// ================================================================== customers (companies)
const lastPay = x => x.last_payment || {};
PAGES.customers = async (view, parts, q) => {
  const rows = await rpc("console_companies");
  const plans = [...new Set(rows.map(x => x.state.plan_name).filter(Boolean))];
  if (q.st) { DT.customers = { ...(DT.customers || {}), f: { st: q.st, arch: "live" }, q: "" }; }
  const counts = rows.reduce((a, x) => { a[x.state.status] = (a[x.state.status] || 0) + 1; return a; }, {});
  view.innerHTML = `<div class="kpis">${Object.entries(ST()).filter(([k]) => counts[k]).map(([k, [l]]) => kpi(l, num(counts[k]), "", "", `#/customers?st=${k}`)).join("")}</div><div class="card"><div id="ctbl"></div></div>`;
  afterHooks.push(v => table($("#ctbl", v), "customers", rows, {
    sort: "ends", dir: "asc", ph: L("Company, owner email…", "الشركة، بريد المالك…"), search: ["name", "owner"],
    onRow: x => { location.hash = "#/company/" + x.id; },
    rowCls: x => ["archived", "suspended"].includes(x.state.status) ? "dim" : "",
    cols: [{ k: "name", l: L("Company", "الشركة"), h: x => `<b>${esc(x.name)}</b><span class="sub">${esc(L("since", "منذ"))} ${esc(day(x.created_at))}</span>` },
      { k: "owner", l: L("Owner", "المالك"), v: x => x.created_by, h: x => `<span dir="ltr">${esc(x.created_by || "")}</span>` },
      { k: "status", l: L("Status", "الحالة"), v: x => (ST()[x.state.status] || [x.state.status])[0], h: x => stChip(x.state.status) + (x.cancel_note ? `<span class="sub">✗ ${esc(x.cancel_note)}</span>` : "") },
      { k: "plan", l: L("Plan", "الباقة"), v: x => x.state.plan_name || "" },
      { k: "industry", l: L("Industry", "النشاط"), v: x => x.state.industry || "" },
      { k: "ends", l: L("Next due", "الاستحقاق التالي"), date: true, v: x => x.state.ends ? String(x.state.ends).slice(0, 10) : "", h: x => x.state.ends ? `${esc(day(x.state.ends))}<span class="sub ${daysTo(x.state.ends) < 0 ? "neg" : daysTo(x.state.ends) <= 7 ? "" : ""}">${esc(daysTo(x.state.ends) < 0 ? L(`${-daysTo(x.state.ends)} days ago`, `منذ ${-daysTo(x.state.ends)} يوم`) : L(`in ${daysTo(x.state.ends)} days`, `خلال ${daysTo(x.state.ends)} يوم`))}</span>` : "—" },
      { k: "people", l: L("People", "الأفراد"), num: true, h: x => `${x.people}${x.state.users_allowed ? " / " + x.state.users_allowed : ""}` },
      { k: "lpa", l: L("Last paid", "آخر دفعة"), num: true, v: x => lastPay(x).amount ?? "", h: x => lastPay(x).amount != null ? `${money(lastPay(x).amount)}<span class="sub">${esc(day(lastPay(x).date))}</span>` : "—" },
      { k: "lpd", l: L("Last paid on", "تاريخ آخر دفعة"), date: true, v: x => lastPay(x).date ? String(lastPay(x).date).slice(0, 10) : "" },
      { k: "paid_total", l: L("Paid in total", "إجمالي المدفوع"), num: true, h: x => money(x.paid_total) },
      { k: "last_change", l: L("Last activity", "آخر نشاط"), date: true, h: x => esc(ago(x.last_change)) },
      { k: "tickets_open", l: L("Tickets", "البلاغات"), num: true, h: x => x.tickets_open ? `<b class="neg">${x.tickets_open}</b>` : "" }],
    filters: [{ k: "st", l: L("Status", "الحالة"), type: "select", opts: Object.entries(ST()).map(([k, v]) => [k, v[0]]), test: (x, v) => x.state.status === v || (v === "active" && x.state.status === "past_due" && false) },
      { k: "arch", l: L("Show", "عرض"), type: "select", opts: [["live", L("Not archived", "غير مؤرشفة")], ["archived", L("Archived only", "المؤرشفة فقط")]], test: (x, v) => (v === "archived") === (x.state.status === "archived") },
      { k: "plan", l: L("Plan", "الباقة"), type: "select", opts: plans.map(p => [p, p]) },
      { k: "industry", l: L("Industry", "النشاط"), type: "select", opts: [...new Set(rows.map(x => x.state.industry).filter(Boolean))].map(k => [k, k]) },
      { k: "over", l: L("Users", "المستخدمون"), type: "select", opts: [["full", L("All seats taken", "كل المقاعد مشغولة")], ["over", L("More than allowed", "أكثر من المسموح")]],
        test: (x, v) => x.state.users_allowed != null && (v === "over" ? x.people > x.state.users_allowed : x.people >= x.state.users_allowed) },
      { k: "duefrom", l: L("Due from", "مستحق من"), type: "from", col: "ends" }, { k: "dueto", l: L("Due to", "مستحق حتى"), type: "to", col: "ends" },
      { k: "pfrom", l: L("Last paid from", "آخر دفعة من"), type: "from", col: "lpd" }, { k: "pto", l: L("Last paid to", "آخر دفعة حتى"), type: "to", col: "lpd" },
      { k: "cfrom", l: L("Joined from", "انضمت من"), type: "from", test: (x, v) => String(x.created_at).slice(0, 10) >= v }, { k: "cto", l: L("Joined to", "انضمت حتى"), type: "to", test: (x, v) => String(x.created_at).slice(0, 10) <= v },
      { k: "minpaid", l: L("Paid at least", "دفع على الأقل"), type: "min", col: "paid_total" }, { k: "maxpaid", l: L("Paid at most", "دفع على الأكثر"), type: "max", col: "paid_total" }] }));
};

// ================================================================== one company: everything about it, every control
PAGES.company = async (view, parts) => {
  const id = parts[1];
  const [x, cfgAll, visits] = await Promise.all([rpc("console_company", { p_company: id }), rpc("billing_admin_overview").then(r => r.config).catch(() => null),
    can("support") ? rpc("console_visits", { p_status: "" }).then(v => v.filter(r => r.company_id === id)).catch(() => []) : []]);
  if (!x) throw new Error(L("This company no longer exists.", "هذه الشركة لم تعد موجودة."));
  $("#ttl").textContent = x.name; $("#crumb").textContent = L("Customers", "العملاء");
  const st = x.state, b = x.billing || {}, ov = b.overrides || {}, free = !!b.comped, appr = can("approve"), owner = S.me.full, cfg = cfgAll || {};
  const days = cfg.trial_days || 15, cur = cfg.currency || st.currency || "EGP", u = x.use || {};
  const setCo = async (p, msg) => { await rpc("billing_set_company", { p_company: id, p }); toast(msg || L("Saved.", "تم الحفظ.")); route(); };
  const askDays = async (m, d) => { const v = await ask(m, String(d), { type: "number" }); if (v === null) return 0; const n = Math.round(+v); if (!(n >= 1 && n <= 3650)) { toast(L("Enter 1 to 3650 days.", "أدخل من 1 إلى 3650 يوماً."), true); return 0; } return n; };
  Object.assign(S.H, {
    pay: () => payPanel(x, cfg),
    trial: async () => { const n = await askDays(L(`New free trial for ${x.name}, starting now. How many days?`, `تجربة مجانية جديدة لـ ${x.name} تبدأ الآن. كم يوماً؟`), days); if (n) setCo({ trial: "restart", days: n }); },
    extend: async () => { const paid = ["active", "past_due"].includes(st.billing_status || st.status);
      const n = await askDays(paid ? L("Add days to the paid period. How many?", "إضافة أيام إلى الفترة المدفوعة. كم يوماً؟") : L("Add days to the free trial. How many?", "إضافة أيام إلى التجربة المجانية. كم يوماً؟"), 7); if (n) setCo({ trial: "extend", days: n }); },
    stoptrial: async () => { if (await confirmIt(L("End the free trial now? Without a paid period it becomes read only.", "إنهاء التجربة الآن؟ بدون فترة مدفوعة تصبح للعرض فقط."))) setCo({ trial: "stop" }); },
    free: async () => { if (await confirmIt(free ? L("Charge this company again? It follows its trial or paid period from now on.", "إعادة احتساب الاشتراك لهذه الشركة؟") : L("Make this company free of charge? It uses everything with no subscription until you switch this off.", "جعل هذه الشركة مجانية؟ تستخدم كل شيء دون اشتراك حتى تلغي ذلك."))) setCo({ comped: !free }); },
    disc: async () => { const v = await ask(L("Standing discount on every order for this company (%). 0 removes it.", "خصم دائم على كل طلب لهذه الشركة (%). 0 يلغيه."), String(b.discount_pct || 0), { type: "number" }); if (v === null) return;
      const n = +v; if (!(n >= 0 && n <= 100)) return toast(L("Enter 0 to 100.", "أدخل من 0 إلى 100."), true); setCo({ discount_pct: n }); },
    dates: () => panel(L("Dates and plan", "التواريخ والباقة"), form([
      { k: "trial_ends", l: L("Trial ends (blank: automatic)", "نهاية التجربة (فارغ: تلقائي)"), type: "date", v: day(b.trial_ends) === "—" ? "" : day(b.trial_ends) },
      { k: "paid_until", l: L("Paid until", "مدفوع حتى"), type: "date", v: day(b.paid_until) === "—" ? "" : day(b.paid_until) },
      { k: "plan", l: L("Plan in effect", "الباقة الحالية"), type: "select", v: b.plan || "", opts: [["", "—"], ...(cfg.plans || []).map(p => [p.code, p.name])] },
      { k: "extra_users", l: L("Extra users paid for", "مستخدمون إضافيون مدفوعون"), type: "number", v: b.extra_users || 0, min: 0 },
      { k: "clear_cancel", l: L("Clear its request not to renew", "إلغاء طلب عدم التجديد"), type: "check", v: false, span: 1 }]),
      v => setCo({ trial_ends: v.trial_ends ? v.trial_ends + "T23:59:59Z" : "", paid_until: v.paid_until ? v.paid_until + "T23:59:59Z" : "", plan: v.plan, extra_users: +v.extra_users || 0, clear_cancel: v.clear_cancel })),
    access: () => accessPanel(x, cfg, setCo),
    menus: () => menusPanel(x, cfg, setCo),
    industry: () => { const inds = industriesOf(cfg);
      panel(L("Industry", "النشاط"), `<p class="muted small">${esc(L("Its industry's starter modules are included in every plan. The app sets this when an industry template is applied; you can change it here.", "الأقسام المبدئية لنشاطها مشمولة في كل باقة. يضبطه النظام عند تطبيق قالب النشاط، ويمكنك تغييره هنا."))}</p>
        ${form([{ k: "industry", l: L("Industry", "النشاط"), type: "select", v: b.industry || "", span: 1, opts: [["", L("Not known", "غير معروف")], ...inds.map(([k, d]) => [k, S.lang === "ar" && d.name_ar ? d.name_ar : d.name || k])] }], 1)}`,
        v => setCo({ industry: v.industry })); },
    deal: () => dealPanel(x, cfg, setCo),
    suspend: async () => { if (b.suspended) { if (await confirmIt(L("Lift the suspension?", "رفع الإيقاف؟"))) setCo({ suspended: false }); return; }
      const why = await ask(L("Suspend this company: everyone can still view and export, nobody can save. Reason (they see it):", "إيقاف هذه الشركة: يبقى العرض والتصدير متاحين ولا يمكن الحفظ. السبب (يظهر لهم):"), ""); if (why === null) return; setCo({ suspended: true, suspend_note: why }); },
    rename: async () => { const n = await ask(L("New name", "الاسم الجديد"), x.name); if (n && n.trim() !== x.name) { await rpc("console_company_edit", { p_company: id, p: { name: n } }); toast(L("Saved.", "تم الحفظ.")); route(); } },
    archive: async () => { const on = st.status !== "archived";
      if (!await confirmIt(on ? L("Archive this company? It becomes read only and leaves the main lists; nothing is deleted and you can bring it back any time.", "أرشفة هذه الشركة؟ تصبح للعرض فقط وتخرج من القوائم الرئيسية؛ لا يُحذف شيء ويمكن إعادتها في أي وقت.") : L("Bring this company back from the archive?", "إعادة هذه الشركة من الأرشيف؟"))) return;
      await rpc("console_company_archive", { p_company: id, p_archived: on }); toast(L("Saved.", "تم الحفظ.")); route(); },
    backup: async () => { toast(L("Preparing the backup…", "جارٍ تجهيز النسخة الاحتياطية…"));
      const bk = await rpc("console_company_backup", { p_company: id });
      download(`backup_${x.name.replace(/[^\w؀-ۿ-]+/g, "_")}_${today()}.json`, JSON.stringify(bk), "application/json");
      toast(L("Backup downloaded. Keep it somewhere safe.", "تم تنزيل النسخة الاحتياطية. احفظها في مكان آمن.")); },
    del: async () => {
      const n = await ask(L(`Delete "${x.name}" for good — its books, people and settings. This cannot be undone except from a backup file. Type the company's name to confirm:`, `حذف «${x.name}» نهائياً — دفاترها وأفرادها وإعداداتها. لا يمكن التراجع إلا من ملف نسخة احتياطية. اكتب اسم الشركة للتأكيد:`), "", { danger: true, ok: L("Delete for good", "حذف نهائي") });
      if (n === null) return; await rpc("console_company_delete", { p_company: id, p_name: n }); toast(L("Deleted. Its payments stay in your records.", "تم الحذف. تبقى مدفوعاتها في سجلاتك.")); location.hash = "#/customers"; },
    owner: async () => { const people = x.people.filter(p => p.active && !p.pending).map(p => p.email);
      panel(L("Hand the company to another owner", "تسليم الشركة لمالك آخر"), form([{ k: "owner", l: L("New owner (an active member)", "المالك الجديد (عضو نشط)"), type: "select", v: x.created_by, opts: people.map(e => [e, e]), span: 1 }], 1),
        async v => { await rpc("console_company_edit", { p_company: id, p: { owner: v.owner } }); toast(L("Saved.", "تم الحفظ.")); }); },
    member: el => memberPanel(x, x.people.find(p => p.email === el.dataset.e)),
  });
  const ent = st.modules === null || st.modules === undefined ? L("Every module", "كل الأقسام") : st.modules.map(secName).join(" · ");
  view.innerHTML = `<div class="card"><div class="row">${avatar(x.name)}<div><h1 style="margin:0">${esc(x.name)}</h1><span class="muted small" dir="ltr">${esc(x.created_by || "")}</span> · <span class="muted small">${esc(L("since", "منذ"))} ${esc(day(x.created_at))}</span></div>
      ${stChip(st.status)}${free ? chip(L("Free of charge", "مجانية"), "info") : ""}${+b.discount_pct ? chip(L(`Discount ${b.discount_pct}%`, `خصم ${b.discount_pct}%`), "ok") : ""}<span class="sp"></span>
      <a class="btn" href="#/customers">← ${esc(L("Customers", "العملاء"))}</a></div>
      ${b.suspended ? `<div class="banner err" style="margin-top:10px">${esc(L("Suspended", "موقوفة"))}${b.suspend_note ? ": " + esc(b.suspend_note) : ""}</div>` : ""}
      ${b.cancel_note ? `<div class="banner warn" style="margin-top:10px">✗ ${esc(L("Asked not to renew", "طلب عدم التجديد"))}: ${esc(b.cancel_note)} · ${esc(dt(b.cancel_at))}</div>` : ""}
      ${appr ? `<div class="row" style="margin-top:12px"><b class="small muted">${esc(L("Subscription", "الاشتراك"))}</b>
        <button class="btn pri sm" data-a="pay">${esc(L("Record payment", "تسجيل دفعة"))}</button><button class="btn sm" data-a="trial">↻ ${esc(L("Restart trial…", "إعادة التجربة…"))}</button>
        <button class="btn sm" data-a="extend">+ ${esc(L("Extend…", "تمديد…"))}</button>${(st.billing_status || st.status) === "trial" ? `<button class="btn sm" data-a="stoptrial">■ ${esc(L("Stop trial", "إيقاف التجربة"))}</button>` : ""}
        <button class="btn sm" data-a="free">${esc(free ? L("Charge again", "إعادة الاحتساب") : L("Make free", "جعلها مجانية"))}</button><button class="btn sm" data-a="disc">% ${esc(L("Discount…", "خصم…"))}</button>
        <button class="btn sm" data-a="dates">${esc(L("Dates & plan…", "التواريخ والباقة…"))}</button></div>
      <div class="row" style="margin-top:8px"><b class="small muted">${esc(L("Access", "الوصول"))}</b><button class="btn sm" data-a="access">${esc(L("What it can use…", "ما يمكنها استخدامه…"))}</button>
        <button class="btn sm" data-a="menus">☰ ${esc(L("Menus, screens & tabs…", "القوائم والشاشات والتبويبات…"))}</button><button class="btn sm" data-a="industry">◎ ${esc(L("Industry…", "النشاط…"))}</button>
        ${(cfg.plans || []).some(p => p.contact) ? `<button class="btn sm" data-a="deal">★ ${esc(L("Enterprise deal…", "اتفاق المؤسسات…"))}</button>` : ""}
        <button class="btn sm ${b.suspended ? "" : "danger"}" data-a="suspend">${esc(b.suspended ? L("Lift suspension", "رفع الإيقاف") : L("Suspend…", "إيقاف…"))}</button></div>
      <div class="row" style="margin-top:8px"><b class="small muted">${esc(L("Company", "الشركة"))}</b><button class="btn sm" data-a="rename">${esc(L("Rename…", "إعادة التسمية…"))}</button>
        <button class="btn sm" data-a="owner">${esc(L("Change owner…", "تغيير المالك…"))}</button>
        <button class="btn sm" data-a="archive">${esc(st.status === "archived" ? L("Bring back from archive", "إعادة من الأرشيف") : L("Archive…", "أرشفة…"))}</button>
        ${owner ? `<button class="btn sm" data-a="backup">⬇ ${esc(L("Download backup", "تنزيل نسخة احتياطية"))}</button>${st.status === "archived" ? `<button class="btn sm danger" data-a="del">${esc(L("Delete for good…", "حذف نهائي…"))}</button>` : ""}` : ""}</div>` : ""}</div>
    <div class="kpis">${kpi(L("Plan", "الباقة"), esc(st.plan_name || "—"))}${kpi(L("Next due", "الاستحقاق التالي"), esc(day(st.ends)), daysTo(st.ends) !== null && daysTo(st.ends) < 0 ? "bad" : "", st.ends ? esc(L(`${daysTo(st.ends)} days`, `${daysTo(st.ends)} يوم`)) : "")}
      ${kpi(L("Users", "المستخدمون"), `${st.users ?? x.people.filter(p => p.active && !p.pending).length}${st.users_allowed ? " / " + st.users_allowed : ""}`, st.users_allowed && st.users > st.users_allowed ? "bad" : "")}
      ${kpi(L("Changes (30 days)", "التعديلات (30 يوماً)"), num(u.changes_30d), "", esc(L(`${num(u.changes)} in total · ${u.devices} device(s)`, `${num(u.changes)} إجمالاً · ${u.devices} جهاز`)))}${kpi(L("Last activity", "آخر نشاط"), esc(ago(u.last_change)))}
      ${kpi(L("Paid in total", "إجمالي المدفوع"), money(x.payments.filter(p => p.status === "paid").reduce((s, p) => s + +p.amount, 0)), "", esc(cur))}</div>
    <div class="card"><h2>${esc(L("What it can use", "ما يمكنها استخدامه"))}</h2><div class="small">${esc(ent)}</div>
      <div class="row" style="margin-top:6px">${chip(st.ai ? L("AI assistant: on", "المساعد الذكي: متاح") : L("AI assistant: off", "المساعد الذكي: غير متاح"), st.ai ? "ok" : "")}${chip(st.support !== false ? L("Online support: included", "الدعم الإلكتروني: مشمول") : L("Online support: not included", "الدعم الإلكتروني: غير مشمول"), st.support !== false ? "ok" : "")}
      ${Object.keys(ov).length ? chip(L("Your overrides apply", "تنطبق استثناءاتك"), "warn") : ""}
      ${chip(L("Industry: ", "النشاط: ") + (((cfg.core_by_industry || {})[b.industry] || {})[S.lang === "ar" ? "name_ar" : "name"] || b.industry || L("not known", "غير معروف")), b.industry ? "info" : "")}
      ${(st.deny || []).length ? chip(L(`${st.deny.length} switched off`, `${st.deny.length} متوقف`), "warn") : ""}</div>
      ${(st.deny || []).length ? `<div class="small muted" style="margin-top:6px">⊘ ${st.deny.map(k => esc(keyName(k))).join(" · ")}</div>` : ""}</div>
    <div class="split"><div>
      <div class="card"><h2>${esc(L("People", "الأفراد"))}</h2><div class="tbl"><table><thead><tr><th>${esc(L("Email", "البريد"))}</th><th>${esc(L("Role", "الدور"))}</th><th></th></tr></thead><tbody>
        ${x.people.map(p => `<tr class="${p.active ? "" : "dim"}"><td dir="ltr">${esc(p.email)}</td><td>${p.email === x.created_by ? chip(L("Owner", "المالك"), "ok") : p.super ? chip(L("Super administrator", "مسؤول أعلى"), "ok") : p.is_admin ? chip(L("Administrator", "مسؤول")) : chip(L("User", "مستخدم"))}
          ${p.pending ? chip(L("Asked to join", "طلب الانضمام"), "warn") : !p.active ? chip(L("Inactive", "غير نشط")) : ""}</td><td>${appr ? `<button class="btn sm" data-a="member" data-e="${esc(p.email)}">${esc(L("Manage…", "إدارة…"))}</button>` : ""}</td></tr>`).join("")}</tbody></table></div></div>
      <div class="card"><h2>${esc(L("Payments", "المدفوعات"))}</h2><div id="cpay"></div></div></div>
      <div><div class="card"><h2>${esc(L("Orders", "الطلبات"))}</h2>${x.orders.length ? `<div class="list">${x.orders.map(o => `<a class="li" href="#/payments?ref=${esc(o.ref)}">${mapChip(ORDER_ST(), o.status)}<span class="who"><b dir="ltr">${esc(o.ref)}</b><small class="muted">${esc(day(o.created_at))}</small></span><span class="amt"><b>${money(o.amount)}</b></span></a>`).join("")}</div>` : `<div class="empty">${esc(L("No orders.", "لا طلبات."))}</div>`}</div>
      <div class="card"><h2>${esc(L("Support", "الدعم"))}</h2>${x.tickets.length ? `<div class="list">${x.tickets.map(t => `<a class="li" href="#/ticket/${t.id}">${mapChip(TK_ST(), t.status)}<span class="who"><b>${esc(t.ref)}</b><small class="muted">${esc(t.subject)}</small></span><span class="amt"><small class="muted">${esc(ago(t.updated_at))}</small></span></a>`).join("")}</div>` : `<div class="empty">${esc(L("No tickets.", "لا بلاغات."))}</div>`}
        ${visits.length ? `<h3 style="margin-top:12px">${esc(L("Visits", "الزيارات"))}</h3><div class="list">${visits.map(v => `<a class="li" href="#/visit/${v.id}">${mapChip(VIS_ST(), v.status)}<span class="who"><b>${esc(v.ref)}</b><small class="muted">${esc(v.reason)}</small></span><span class="amt"><b>${v.price != null ? money(v.price) : "—"}</b></span></a>`).join("")}</div>` : ""}</div></div></div>`;
  afterHooks.push(v => table($("#cpay", v), "cpay", x.payments, { sort: "created_at", dir: "desc", search: ["reference", "plan"],
    cols: [{ k: "created_at", l: L("Date", "التاريخ"), date: true, h: p => esc(day(p.paid_at || p.created_at)) }, { k: "plan", l: L("Plan", "الباقة") },
      { k: "period", l: L("Period", "المدة"), v: p => `${day(p.period_start)} → ${day(p.period_end)}` }, { k: "amount", l: L("Amount", "المبلغ"), num: true, h: p => `${money(p.amount)} ${esc(p.currency)}` },
      { k: "reference", l: L("Reference", "المرجع"), h: p => `<span dir="ltr">${esc(p.reference || "")}</span>` }, { k: "status", l: L("Status", "الحالة") }] }));
};
function payPanel(x, cfg) {
  const sold = Object.keys(cfg.module_prices || {});
  panel(`${x.name} — ${L("Record payment", "تسجيل دفعة")}`, form([
    { k: "plan", l: L("Plan", "الباقة"), type: "select", v: x.state.plan || cfg.trial_plan, opts: (cfg.plans || []).map(p => [p.code, `${p.name} — ${money(p.price)}${p.hidden ? " · " + L("hidden", "مخفية") : ""}`]) },
    { k: "months", l: L("Months", "عدد الأشهر"), type: "number", v: 1, min: 1, max: 120, req: 1 },
    { k: "extra_users", l: L("Extra users", "مستخدمون إضافيون"), type: "number", v: x.state.extra_users || 0, min: 0 },
    { k: "amount", l: L("Amount received (blank: list price; 0 for free)", "المبلغ المستلم (فارغ: سعر القائمة؛ 0 للمجاني)"), type: "number", min: 0 },
    { k: "reference", l: L("Reference (transfer number)", "المرجع (رقم التحويل)") }, { k: "note", l: L("Note", "ملاحظة") },
    ...(sold.length ? [{ type: "html", span: 1, html: `<b class="small">${esc(L("Modules added on top of the plan", "أقسام مضافة على الباقة"))}</b><div class="checks">${sold.map(k => `<label class="chk"><input type="checkbox" name="m_${k}" ${((x.state.addons || {}).modules || []).includes(k) ? "checked" : ""}> ${esc(secName(k))}</label>`).join("")}</div>` }] : []),
    { k: "ai", l: L("AI assistant add-on", "إضافة المساعد الذكي"), type: "check", v: !!(x.state.addons || {}).ai },
    { k: "support", l: L("Online support add-on", "إضافة الدعم الإلكتروني"), type: "check", v: !!(x.state.addons || {}).support }]),
    async v => { await rpc("console_record_payment", { p: { company: x.id, plan: v.plan, months: +v.months, extra_users: +v.extra_users || 0, amount: v.amount === "" ? "" : +v.amount,
      reference: v.reference, note: v.note, modules: sold.filter(k => v["m_" + k]), ai: !!v.ai, support: !!v.support } }); toast(L("Payment recorded.", "تم تسجيل الدفعة.")); });
}
function accessPanel(x, cfg, setCo) {
  const ov = (x.billing || {}).overrides || {}, list = Array.isArray(ov.modules) ? ov.modules : [];
  const tri = (k, l) => ({ k, l, type: "select", v: ov[k] === true ? "on" : ov[k] === false ? "off" : "", opts: [["", L("As its plan", "حسب الباقة")], ["on", L("Always on", "متاح دائماً")], ["off", L("Off", "غير متاح")]] });
  panel(L("What this company can use", "ما يمكن لهذه الشركة استخدامه"), `<p class="muted small">${esc(L("Overrides beat the plan, the trial and the add-ons. Leave “As its plan” to follow them.", "الاستثناءات تتقدم على الباقة والتجربة والإضافات. اترك «حسب الباقة» لاتباعها."))}</p>
    ${form([{ k: "mods", l: L("Modules", "الأقسام"), type: "select", v: ov.modules === "all" ? "all" : list.length ? "list" : "", opts: [["", L("As its plan", "حسب الباقة")], ["all", L("Every module", "كل الأقسام")], ["list", L("The plan's, plus these:", "أقسام الباقة، بالإضافة إلى:")]] },
      { k: "users", l: L("Users allowed (blank: as its plan)", "المستخدمون المسموح بهم (فارغ: حسب الباقة)"), type: "number", v: ov.users ?? "", min: 1 },
      tri("ai", L("AI assistant", "المساعد الذكي")), tri("support", L("Online support", "الدعم الإلكتروني")),
      tri("perms", L("User permissions (menus)", "صلاحيات المستخدمين (القوائم)")), tri("notify_perms", L("Notification permissions", "صلاحيات الإشعارات")),
      { type: "html", span: 1, html: `<div class="checks">${SECTIONS().map(([k, l]) => `<label class="chk"><input type="checkbox" name="s_${k}" ${list.includes(k) ? "checked" : ""}> ${esc(l)}</label>`).join("")}</div>` }])}`,
    v => { const o = {}; for (const k of ["deny", "allow"]) if (ov[k]) o[k] = ov[k]; if (v.mods === "all") o.modules = "all"; if (v.mods === "list") o.modules = SECTIONS().map(s => s[0]).filter(k => v["s_" + k]);
      if (v.users !== "") o.users = +v.users; for (const k of ["ai", "support", "perms", "notify_perms"]) if (v[k]) o[k] = v[k] === "on"; return setCo({ overrides: o }); });
}
// what one company sees, down to each screen and tab: switched off for it, or given back when its plan switches it off
function menusPanel(x, cfg, setCo) {
  const ov = (x.billing || {}).overrides || {}, val = {};
  (ov.deny || []).forEach(k => { val[k] = "off"; }); (ov.allow || []).forEach(k => { val[k] = "on"; });
  const plan = (cfg.plans || []).find(p => p.code === x.state.plan) || {};
  panel(`${x.name} — ${L("Menus, screens & tabs", "القوائم والشاشات والتبويبات")}`, `<p class="muted small">${esc(L("Only for this company — other companies of the same people are set on their own pages. “Off” hides it from everyone in this company (even when it is free or billing is off). “On” gives back what its plan switches off. Modules its plan does not include are added in “What it can use”.", "لهذه الشركة فقط — شركات نفس الأشخاص الأخرى تُضبط من صفحاتها. «متوقف» يخفيه عن الجميع في هذه الشركة (حتى لو كانت مجانية أو الاشتراكات متوقفة). «متاح» يعيد ما توقفه الباقة. الأقسام غير المشمولة في الباقة تُضاف من «ما يمكنها استخدامه»."))}</p>
    ${(plan.deny || []).length ? `<div class="banner warn small">${esc(L("Its plan switches off: ", "توقف الباقة: "))}${plan.deny.map(k => esc(keyName(k))).join(" · ")}</div>` : ""}
    ${treeHtml(val, "tri")}`,
    v => setCo({ overrides: { ...ov, deny: treeRead(v, "off"), allow: treeRead(v, "on") } }), { wide: true, onOpen: treeWire });
  S.H.tsel = el => { el.className = "tsel " + el.value; };
}
// an Enterprise customer: the plan, the users and companies agreed, what it uses, and the price agreed
function dealPanel(x, cfg, setCo) {
  const ov = (x.billing || {}).overrides || {}, ent = (cfg.plans || []).filter(p => p.contact), st = x.state;
  panel(`${x.name} — ${L("Enterprise deal", "اتفاق المؤسسات")}`, `<p class="muted small">${esc(L("Everything agreed with the customer in one go. The users number is a hard limit; the companies number applies to the owner's account (every company they own).", "كل ما اتُّفق عليه مع العميل مرة واحدة. عدد المستخدمين حد لا يُتجاوز؛ وعدد الشركات ينطبق على حساب المالك (كل الشركات التي يملكها)."))}</p>
    ${form([{ k: "plan", l: L("Plan", "الباقة"), type: "select", v: ent.some(p => p.code === st.plan) ? st.plan : ent[0].code, opts: ent.map(p => [p.code, p.name]) },
      { k: "users", l: L("Users agreed (this company)", "المستخدمون المتفق عليهم (هذه الشركة)"), type: "number", v: ov.users ?? Math.max(st.users || 1, 1), min: 1, req: 1 },
      { k: "companies", l: L(`Companies ${x.created_by || "the owner"} may own (blank: the general rule)`, `الشركات التي يملكها ${x.created_by || "المالك"} (فارغ: القاعدة العامة)`), type: "number", min: 0, v: "" },
      { k: "mods", l: L("Modules", "الأقسام"), type: "select", v: ov.modules === "all" || !ov.modules ? "all" : "plan", opts: [["all", L("Every module", "كل الأقسام")], ["plan", L("As the plan", "حسب الباقة")]] },
      { k: "ai", l: L("AI assistant", "المساعد الذكي"), type: "check", v: ov.ai !== false }, { k: "support", l: L("Online support", "الدعم الإلكتروني"), type: "check", v: ov.support !== false },
      { k: "months", l: L("Months paid for", "الأشهر المدفوعة"), type: "number", v: 12, min: 1, max: 120, req: 1 },
      { k: "amount", l: L(`Agreed amount for the period (${cfg.currency || ""})`, `المبلغ المتفق عليه للفترة (${cfg.currency || ""})`), type: "number", min: 0, req: 1 },
      { k: "reference", l: L("Reference (contract or transfer number)", "المرجع (رقم العقد أو التحويل)") }, { k: "note", l: L("Note", "ملاحظة") }])}`,
    async v => {
      await rpc("console_record_payment", { p: { company: x.id, plan: v.plan, months: +v.months, amount: +v.amount, reference: v.reference, note: v.note || "Enterprise deal" } });
      const o = { ...ov, users: +v.users, ai: !!v.ai, support: !!v.support }; if (v.mods === "all") o.modules = "all"; else delete o.modules;
      await rpc("billing_set_company", { p_company: x.id, p: { overrides: o } });
      if (v.companies !== "" && x.created_by) await rpc("console_person_save", { p: { email: x.created_by, max_companies: +v.companies } });
      toast(L("Deal saved.", "تم حفظ الاتفاق."));
    }, { saveLabel: L("Save the deal", "حفظ الاتفاق") });
}
function memberPanel(x, p) {
  const act = async (q, msg) => { await rpc("console_member_set", { p_company: x.id, p_email: p.email, p: q }); toast(msg || L("Saved.", "تم الحفظ.")); closePanel(); route(); };
  const acct = async (action, msg) => { const r = await fnCall("manage-user", { platform: true, action, email: p.email, redirect: location.origin + location.pathname.replace(/owner\.html$/, "") });
    if (r.link) { await ask(L("A one-time link to set a new password. Send it to them (it works once):", "رابط لمرة واحدة لتعيين كلمة مرور جديدة. أرسله لهم (يعمل مرة واحدة):"), r.link); return; } toast(msg); };
  Object.assign(S.H, {
    madmin: () => act({ is_admin: !p.is_admin }), mactive: () => act({ active: !p.active }), mapprove: () => act({ approve: true, active: true }),
    mremove: async () => { if (await confirmIt(L(`Remove ${p.email} from ${x.name}? Their sign-in account stays.`, `إزالة ${p.email} من ${x.name}؟ يبقى حساب الدخول.`), { danger: true })) act({ remove: true }); },
    mmfa: async () => { if (await confirmIt(L("Turn off their two-step sign-in? They sign in with the password and set it up again.", "إيقاف التحقق بخطوتين لديهم؟ يدخلون بكلمة المرور ويعيدون إعداده."))) acct("mfa_off", L("Two-step sign-in turned off.", "تم إيقاف التحقق بخطوتين.")); },
    mlink: () => acct("reset_link"),
    mban: async () => { if (await confirmIt(L(`Block ${p.email} everywhere? They cannot sign in or reach any company until you unblock them (People).`, `حظر ${p.email} من كل مكان؟ لا يمكنهم الدخول أو الوصول لأي شركة حتى ترفع الحظر (الأشخاص).`), { danger: true })) {
      await rpc("console_person_save", { p: { email: p.email, blocked: true } }); await acct("ban", L("Blocked.", "تم الحظر.")); closePanel(); } },
  });
  panel(p.email, `<div class="grid" style="gap:8px">
    <button type="button" class="btn" data-a="madmin">${esc(p.is_admin ? L("Make a regular user", "جعله مستخدماً عادياً") : L("Make an administrator", "جعله مسؤولاً"))}</button>
    ${p.pending ? `<button type="button" class="btn pri" data-a="mapprove">${esc(L("Approve the request to join", "قبول طلب الانضمام"))}</button>` : `<button type="button" class="btn" data-a="mactive">${esc(p.active ? L("Deactivate in this company", "إيقافه في هذه الشركة") : L("Activate in this company", "تفعيله في هذه الشركة"))}</button>`}
    <button type="button" class="btn danger" data-a="mremove">${esc(L("Remove from this company", "إزالته من هذه الشركة"))}</button>
    <hr style="border:0;border-top:1px solid var(--line);width:100%"><b class="small muted">${esc(L("Their sign-in account (everywhere)", "حساب الدخول (في كل مكان)"))}</b>
    <button type="button" class="btn" data-a="mlink">${esc(L("Get a password reset link", "الحصول على رابط إعادة تعيين كلمة المرور"))}</button>
    <button type="button" class="btn" data-a="mmfa">${esc(L("Turn off two-step sign-in", "إيقاف التحقق بخطوتين"))}</button>
    <button type="button" class="btn danger" data-a="mban">${esc(L("Block this person", "حظر هذا الشخص"))}</button></div>`, null);
}

// ================================================================== people (everyone using the workspace)
PAGES.people = async view => {
  const rows = await rpc("console_people");
  view.innerHTML = `<div class="card"><div id="ptbl"></div></div>`;
  afterHooks.push(v => table($("#ptbl", v), "people", rows, { sort: "last", dir: "desc", search: ["email", "cos", "note"],
    onRow: p => personPanel(p), rowCls: p => p.blocked ? "dim" : "",
    cols: [{ k: "email", l: L("Person", "الشخص"), h: p => `<b dir="ltr">${esc(p.email)}</b>${p.staff ? " " + chip(L("Team", "الفريق"), "info") : ""}${p.blocked ? " " + chip(L("Blocked", "محظور"), "bad") : ""}${p.note ? `<span class="sub">${esc(p.note)}</span>` : ""}` },
      { k: "owns", l: L("Owns", "يملك"), num: true }, { k: "member_of", l: L("Member of", "عضو في"), num: true },
      { k: "cos", l: L("Companies", "الشركات"), v: p => (p.companies || []).map(c => c.name).join(", "), h: p => (p.companies || []).map(c => `<a href="#/company/${c.id}">${esc(c.name)}</a>${c.owner ? " ★" : ""}`).join(", ") },
      { k: "max_companies", l: L("Company limit", "حد الشركات"), num: true, h: p => p.max_companies == null ? `<span class="muted">${esc(L("default", "الافتراضي"))}</span>` : esc(p.max_companies) },
      { k: "last", l: L("Last activity", "آخر نشاط"), date: true, h: p => esc(ago(p.last)) }],
    filters: [{ k: "blk", l: L("Show", "عرض"), type: "select", opts: [["blocked", L("Blocked", "المحظورون")], ["ok", L("Not blocked", "غير المحظورين")], ["owners", L("Owners of a company", "مالكو شركات")]],
      test: (p, v) => v === "blocked" ? p.blocked : v === "ok" ? !p.blocked : p.owns > 0 },
      { k: "minown", l: L("Owns at least", "يملك على الأقل"), type: "min", col: "owns" }, { k: "from", l: L("Active since", "نشط منذ"), type: "from", col: "last" }] }));
};
function personPanel(p) {
  panel(p.email, `${(p.companies || []).length ? `<div class="list" style="margin-bottom:12px">${p.companies.map(c => `<a class="li" href="#/company/${c.id}">${avatar(c.name)}<span class="who"><b>${esc(c.name)}</b><small class="muted">${esc(c.owner ? L("Owner", "المالك") : c.admin ? L("Administrator", "مسؤول") : L("User", "مستخدم"))}</small></span></a>`).join("")}</div>` : ""}
    ${form([{ k: "max_companies", l: L("How many companies they may own (blank: the general rule)", "عدد الشركات المسموح بامتلاكها (فارغ: القاعدة العامة)"), type: "number", v: p.max_companies ?? "", min: 0, span: 1 },
      { k: "note", l: L("Private note about this person", "ملاحظة خاصة عن هذا الشخص"), type: "area", v: p.note || "", span: 1 },
      { k: "blocked", l: L("Blocked — cannot sign in or reach any company", "محظور — لا يمكنه الدخول أو الوصول لأي شركة"), type: "check", v: p.blocked, span: 1 }], 1)}`,
    async v => { await rpc("console_person_save", { p: { email: p.email, max_companies: v.max_companies, note: v.note, blocked: !!v.blocked } });
      if (!!v.blocked !== !!p.blocked) await fnCall("manage-user", { platform: true, action: v.blocked ? "ban" : "unban", email: p.email }).catch(e => toast(e.message, true));
      toast(L("Saved.", "تم الحفظ.")); });
}

// ================================================================== payments to verify (orders)
PAGES.payments = async (view, parts, q) => {
  const rows = await rpc("console_orders", { p_status: "" }), appr = can("approve");
  if (q.ref) DT.orders = { ...(DT.orders || {}), q: q.ref, f: {} };
  else if (!DT.orders) DT.orders = { f: { st: q.s || "submitted" }, q: "" };
  Object.assign(S.H, {
    ok: async el => { const o = rows.find(r => r.id === +el.dataset.id);
      if (!await confirmIt(L(`Approve ${o.ref} (${money(o.amount)} ${o.currency})? Its companies are activated at once.`, `اعتماد ${o.ref} (${money(o.amount)} ${o.currency})؟ تُفعَّل شركاته فوراً.`))) return;
      const r = await rpc("console_order_decide", { p_order: o.id, p_approve: true, p_note: "" }); push("order", r.id); toast(L("Activated.", "تم التفعيل.")); route(); },
    no: async el => { const why = await ask(L("Why is it not accepted? The customer sees this.", "لماذا لم يُقبل؟ يرى العميل هذا السبب."), "", { area: 1 }); if (!why) return;
      const r = await rpc("console_order_decide", { p_order: +el.dataset.id, p_approve: false, p_note: why }); push("order", r.id); toast(L("Saved.", "تم الحفظ.")); route(); },
    amt: async el => { const v = await ask(L("New total for this order (a discount or an agreed price; 0 is allowed). The customer sees it.", "الإجمالي الجديد لهذا الطلب (خصم أو سعر متفق عليه؛ يُسمح بـ 0). يراه العميل."), el.dataset.v, { type: "number" });
      if (v === null || v === "") return; if (!(+v >= 0)) return toast(L("Enter 0 or more.", "أدخل 0 أو أكثر."), true);
      await rpc("console_order_amount", { p_order: +el.dataset.id, p_amount: +v, p_note: "" }); toast(L("Saved.", "تم الحفظ.")); route(); } });
  view.innerHTML = `<div class="card"><div id="otbl"></div></div><div id="ocards"></div>`;
  afterHooks.push(v => table($("#otbl", v), "orders", rows, { sort: "submitted_at", dir: "desc", search: ["ref", "email", "companies", "proof_ref"],
    cols: [{ k: "ref", l: L("Order", "الطلب"), h: o => `<b dir="ltr">${esc(o.ref)}</b><span class="sub">${esc(dt(o.created_at))}</span>` },
      { k: "email", l: L("From", "من"), h: o => `<span dir="ltr">${esc(o.email)}</span>` },
      { k: "companies", l: L("Companies", "الشركات"), v: o => (o.lines || []).map(l => l.name).join(", "), h: o => (o.lines || []).map(l => `<a href="#/company/${l.company_id}">${esc(l.name)}</a>`).join(", ") },
      { k: "plan", l: L("Plan", "الباقة"), v: o => `${o.plan} · ${o.months === 12 ? L("year", "سنة") : L("month", "شهر")}` },
      { k: "amount", l: L("Amount", "المبلغ"), num: true, h: o => `<b>${money(o.amount)}</b> ${esc(o.currency)}${o.proof_amount != null && Math.abs(+o.proof_amount - +o.amount) > .005 ? `<span class="sub neg">⚠ ${esc(L("sent", "أُرسل"))} ${money(o.proof_amount)}</span>` : ""}` },
      { k: "method_name", l: L("Method", "الطريقة"), h: o => `${KIND_ICON[o.method_kind] || ""} ${esc(o.method_name || "")}` },
      { k: "st", l: L("Status", "الحالة"), v: o => o.status, h: o => mapChip(ORDER_ST(), o.status) },
      { k: "submitted_at", l: L("Sent", "أُرسل"), date: true, h: o => esc(o.submitted_at ? dt(o.submitted_at) : "—") },
      { k: "x", l: "", nosort: true, nocsv: true, h: o => `<a class="btn sm" href="#/payments?ref=${esc(o.ref)}" data-a="openo" data-id="${o.id}" data-prevent="1">${esc(L("Open", "فتح"))}</a>` }],
    filters: [{ k: "st", l: L("Status", "الحالة"), type: "select", opts: Object.entries(ORDER_ST()).map(([k, v]) => [k, v[0]]), test: (o, v) => o.status === v },
      { k: "method", l: L("Method", "الطريقة"), type: "select", opts: [...new Set(rows.map(o => o.method_name).filter(Boolean))].map(m => [m, m]), test: (o, v) => o.method_name === v },
      { k: "from", l: L("From", "من"), type: "from", test: (o, v) => String(o.created_at).slice(0, 10) >= v }, { k: "to", l: L("To", "إلى"), type: "to", test: (o, v) => String(o.created_at).slice(0, 10) <= v },
      { k: "min", l: L("Amount from", "المبلغ من"), type: "min", col: "amount" }, { k: "max", l: L("Amount to", "المبلغ حتى"), type: "max", col: "amount" }],
    after: list => { const show = list.filter(o => ["submitted", "open"].includes(o.status)).slice(0, 10);
      const one = q.ref ? list.filter(o => o.ref === q.ref) : show;
      $("#ocards").innerHTML = one.map(o => orderCard(o, appr)).join(""); previews($("#ocards")); } }));
  S.H.openo = el => { location.hash = "#/payments?ref=" + rows.find(r => r.id === +el.dataset.id).ref; };
};
const orderCard = (o, appr) => `<div class="card"><div class="row"><h2 style="margin:0" dir="ltr">${esc(o.ref)}</h2>${mapChip(ORDER_ST(), o.status)}<span class="muted" dir="ltr">${esc(o.email)}</span><span class="sp"></span><b style="font-size:1.25em">${money(o.amount)} ${esc(o.currency)}</b></div>
  <div class="tbl" style="margin:10px 0"><table><tbody>${(o.lines || []).map(l => `<tr><td><a href="#/company/${l.company_id}">${esc(l.name)}</a>${l.extra_users ? ` +${l.extra_users} ${esc(L("users", "مستخدمين"))}` : ""}${(l.modules || []).length ? `<span class="sub">+ ${esc(l.modules.map(secName).join(", "))}</span>` : ""}${l.ai ? `<span class="sub">+ ${esc(L("AI assistant", "المساعد الذكي"))}</span>` : ""}${l.support ? `<span class="sub">+ ${esc(L("Online support", "الدعم الإلكتروني"))}</span>` : ""}</td>
    <td class="num">${l.pct !== 100 ? `<span class="muted">× ${l.pct}%</span> ` : ""}${+l.discount ? chip(`−${l.discount}%`, "ok") + " " : ""}${l.list_amount != null && +l.list_amount !== +l.amount ? `<s class="muted">${money(l.list_amount)}</s> ` : ""}${money(l.amount)}</td></tr>`).join("")}</tbody></table></div>
  ${o.discount && o.discount.pct ? `<div class="small">🎁 ${esc(o.discount.source === "friend" ? L(`Invited by a customer: ${o.discount.pct}% off their first subscription`, `مدعو من عميل: خصم ${o.discount.pct}% على اشتراكه الأول`) : L(`Referral reward: ${o.discount.pct}% off`, `مكافأة إحالة: خصم ${o.discount.pct}%`))}</div>` : ""}
  <div class="small">${o.kind === "upgrade" ? chip(L("Upgrade: the difference for the days left", "ترقية: الفرق عن الأيام المتبقية"), "info") : `${esc(o.plan)} · ${o.months === 12 ? esc(L("1 year", "سنة")) : esc(L("1 month", "شهر"))}`} · ${KIND_ICON[o.method_kind] || ""} ${esc(o.method_name || "")}
    ${o.submitted_at ? ` · ${esc(L("sent", "أُرسل"))} ${esc(dt(o.submitted_at))}` : ""}${o.proof_ref ? ` · ${esc(L("reference", "المرجع"))} <b dir="ltr">${esc(o.proof_ref)}</b>` : ""}${o.proof_amount != null ? ` · ${esc(L("amount sent", "المبلغ المرسل"))} <b>${money(o.proof_amount)}</b>` : ""}</div>
  ${o.customer_note ? `<p class="small">💬 ${esc(o.customer_note)}</p>` : ""}${o.review_note ? `<p class="small muted">${esc(L("Decision", "القرار"))}: ${esc(o.review_note)} · ${esc(o.reviewed_by || "")}</p>` : ""}
  ${fileList("billing-proofs", o.proof)}
  ${appr && ["submitted", "open", "rejected"].includes(o.status) ? `<div class="row"><button class="btn pri" data-a="ok" data-id="${o.id}">✓ ${esc(L("Approve and activate", "اعتماد وتفعيل"))}</button>
    <button class="btn" data-a="amt" data-id="${o.id}" data-v="${o.amount}">${esc(L("Change amount…", "تعديل المبلغ…"))}</button>${o.status !== "rejected" ? `<button class="btn danger" data-a="no" data-id="${o.id}">${esc(L("Not accepted…", "رفض…"))}</button>` : ""}</div>` : ""}</div>`;

// ================================================================== referrals: the programme's rules and every referral
PAGES.referrals = async view => {
  const d = await rpc("console_referrals"), cfg = d.settings || {}, rows = d.rows, ed = can("settings"), appr = can("approve");
  const ST = () => ({ joined: [L("Signed up", "سجّل"), "warn"], paid: [L("Subscribed", "اشترك"), "ok"], void: [L("Voided", "أُلغيت"), "bad"] });
  const RW = () => ({ available: [L("Reward ready", "مكافأة جاهزة"), "ok"], used: [L("Reward used", "استُخدمت"), "info"], void: [L("Reward voided", "أُلغيت"), "bad"] });
  Object.assign(S.H, {
    rsave: async () => { const v = readForm($("#rf")); await rpc("console_referral_settings", { p: { on: !!v.on, friend_pct: +v.friend_pct || 0, first_pct: +v.first_pct || 0, upgrade_pct: +v.upgrade_pct || 0 } });
      toast(L("Saved.", "تم الحفظ.")); route(); },
    rvoid: async el => { const on = el.dataset.v === "1";
      if (!await confirmIt(on ? L("Void this referral? Its reward is cancelled unless it was already used.", "إلغاء هذه الإحالة؟ تُلغى مكافأتها ما لم تكن استُخدمت.") : L("Bring this referral back?", "استعادة هذه الإحالة؟"), { danger: on })) return;
      await rpc("console_referral_set", { p_id: +el.dataset.id, p_void: on }); toast(L("Saved.", "تم الحفظ.")); route(); } });
  const paid = rows.filter(r => r.status === "paid").length, used = rows.filter(r => r.reward && r.reward.status === "used").length;
  view.innerHTML = `<div class="kpis">${kpi(L("Signed up through a link", "سجّلوا من رابط"), num(rows.filter(r => r.status !== "void").length))}${kpi(L("Became paying customers", "أصبحوا عملاء دافعين"), num(paid), paid ? "ok" : "")}
      ${kpi(L("Rewards waiting", "مكافآت بانتظار الاستخدام"), num(rows.filter(r => r.reward && r.reward.status === "available").length))}${kpi(L("Rewards used", "مكافآت مستخدمة"), num(used))}</div>
    <form id="rf" class="card" onsubmit="return false"><h2>${esc(L("The programme", "البرنامج"))}</h2>${form([
      { k: "on", l: L("Referral program on (customers see their link and page; new links can be claimed)", "برنامج الإحالة مفعّل (يرى العملاء رابطهم وصفحتهم ويمكن استخدام الروابط الجديدة)"), type: "check", v: cfg.on !== false, span: 1, dis: !ed },
      { k: "friend_pct", l: L("Invited person: % off their first subscription", "المدعو: خصم % على اشتراكه الأول"), type: "number", v: cfg.friend_pct, min: 0, max: 100, dis: !ed },
      { k: "first_pct", l: L("Reward: % off the sharer's first subscription", "المكافأة: خصم % على أول اشتراك لصاحب الرابط"), type: "number", v: cfg.first_pct, min: 0, max: 100, dis: !ed },
      { k: "upgrade_pct", l: L("Reward: % off an upgrade", "المكافأة: خصم % على الترقية"), type: "number", v: cfg.upgrade_pct, min: 0, max: 100, dis: !ed }], 3)}
      <p class="muted small">${esc(L("Rules: a reward is earned only when the invited person completes a payment; one reward per payment and discounts never add up (the biggest applies, including a company's standing discount); renewals keep rewards for later; people who already paid, the sharer's own team and their own link cannot be claimed.", "القواعد: تُكتسب المكافأة فقط عند إتمام المدعو دفعة؛ مكافأة واحدة لكل دفعة ولا تُجمع الخصومات (يُطبق الأكبر، بما فيه خصم الشركة الدائم)؛ تنتظر المكافآت عند التجديد؛ ولا يمكن استخدام الرابط لمن دفع سابقاً أو لفريق صاحب الرابط أو لرابطه نفسه."))}</p>
      ${ed ? `<button class="btn pri" data-a="rsave">${esc(L("Save", "حفظ"))}</button>` : ""}</form>
    <div class="card"><div id="rtbl"></div></div>`;
  afterHooks.push(v => table($("#rtbl", v), "referrals", rows, { sort: "joined_at", dir: "desc", search: ["referrer", "referred", "code", "order_ref"],
    rowCls: r => r.status === "void" ? "dim" : "",
    cols: [{ k: "referrer", l: L("Shared by", "صاحب الرابط"), h: r => `<span dir="ltr">${esc(r.referrer)}</span><span class="sub" dir="ltr">${esc(r.code)}</span>` },
      { k: "referred", l: L("Invited person", "المدعو"), h: r => `<span dir="ltr">${esc(r.referred)}</span>` },
      { k: "status", l: L("Status", "الحالة"), h: r => mapChip(ST(), r.status) },
      { k: "joined_at", l: L("Signed up", "التسجيل"), date: true, h: r => esc(day(r.joined_at)) },
      { k: "paid_at", l: L("First payment", "أول دفعة"), date: true, h: r => r.paid_at ? `${esc(day(r.paid_at))}<span class="sub" dir="ltr">${esc(r.order_ref || "")}</span>` : "—" },
      { k: "reward", l: L("Reward", "المكافأة"), v: r => r.reward ? r.reward.status : "", h: r => r.reward ? mapChip(RW(), r.reward.status) + (r.reward.status === "used" ? `<span class="sub">${r.reward.pct}% · ${esc(r.reward.order_ref || "")}</span>` : "") : "—" },
      { k: "act", l: "", nosort: true, nocsv: true, h: r => appr ? `<button class="btn sm ${r.status === "void" ? "" : "danger"}" data-a="rvoid" data-id="${r.id}" data-v="${r.status === "void" ? 0 : 1}">${esc(r.status === "void" ? L("Restore", "استعادة") : L("Void", "إلغاء"))}</button>` : "" }],
    filters: [{ k: "st", l: L("Status", "الحالة"), type: "select", opts: Object.entries(ST()).map(([k, v]) => [k, v[0]]), test: (r, v) => r.status === v },
      { k: "from", l: L("Signed up from", "التسجيل من"), type: "from", col: "joined_at" }, { k: "to", l: L("Signed up to", "التسجيل حتى"), type: "to", col: "joined_at" }],
    empty: L("No referrals yet.", "لا إحالات بعد.") }));
};

// ================================================================== every payment (the revenue ledger)
PAGES.ledger = async view => {
  const rows = await rpc("console_payments");
  const cur = (rows[0] || {}).currency || "";
  view.innerHTML = `<div class="kpis" id="lk"></div><div class="card"><div id="ltbl"></div></div>`;
  afterHooks.push(v => table($("#ltbl", v), "ledger", rows, { sort: "date", dir: "desc", search: ["company", "owner", "reference", "plan", "note"],
    cols: [{ k: "date", l: L("Date", "التاريخ"), date: true, v: p => String(p.paid_at || p.created_at).slice(0, 10) },
      { k: "company", l: L("Company", "الشركة"), h: p => p.company_id ? `<a href="#/company/${p.company_id}">${esc(p.company || "")}</a>` : `${esc(p.company || "")} ${chip(L("deleted", "محذوفة"))}` },
      { k: "plan", l: L("Plan", "الباقة") }, { k: "months", l: L("Months", "الأشهر"), num: true },
      { k: "period_end", l: L("Paid until", "مدفوع حتى"), date: true, v: p => p.period_end ? String(p.period_end).slice(0, 10) : "" },
      { k: "amount", l: L("Amount", "المبلغ"), num: true, h: p => money(p.amount) },
      { k: "provider", l: L("How", "الطريقة"), v: p => p.provider === "paymob" ? L("Card", "بطاقة") : L("Manual", "يدوي") },
      { k: "reference", l: L("Reference", "المرجع"), h: p => `<span dir="ltr">${esc(p.reference || "")}</span>` },
      { k: "status", l: L("Status", "الحالة") }],
    filters: [{ k: "st", l: L("Status", "الحالة"), type: "select", opts: [...new Set(rows.map(p => p.status))].map(s => [s, s]), test: (p, v) => p.status === v },
      { k: "plan", l: L("Plan", "الباقة"), type: "select", opts: [...new Set(rows.map(p => p.plan))].map(s => [s, s]) },
      { k: "from", l: L("From", "من"), type: "from", col: "date" }, { k: "to", l: L("To", "إلى"), type: "to", col: "date" },
      { k: "min", l: L("Amount from", "المبلغ من"), type: "min", col: "amount" }, { k: "max", l: L("Amount to", "المبلغ حتى"), type: "max", col: "amount" },
      { k: "zero", l: L("Free (0)", "مجانية (0)"), type: "select", opts: [["paid", L("Paid only", "المدفوعة فقط")], ["zero", L("0 only", "0 فقط")]], test: (p, v) => v === "zero" ? +p.amount === 0 : +p.amount > 0 }],
    after: list => { const paid = list.filter(p => p.status === "paid"), sum = paid.reduce((s, p) => s + +p.amount, 0);
      $("#lk").innerHTML = kpi(L("Total of the list", "إجمالي القائمة"), money(sum), "", esc(cur)) + kpi(L("Payments", "المدفوعات"), num(paid.length)) +
        kpi(L("Average", "المتوسط"), money(paid.length ? sum / paid.length : 0), "", esc(cur)) + kpi(L("Companies", "الشركات"), num(new Set(paid.map(p => p.company_id || p.company)).size)); } }));
};

// ================================================================== support tickets
PAGES.support = async view => {
  const rows = await rpc("console_tickets", { p_status: "all" });
  if (!DT.tickets) DT.tickets = { f: { st: "notclosed" }, q: "" };
  view.innerHTML = `<div class="card"><div id="ttbl"></div></div>`;
  afterHooks.push(v => table($("#ttbl", v), "tickets", rows, { sort: "updated_at", dir: "desc", search: ["ref", "subject", "email", "company_name", "app_user", "assignee"],
    onRow: t => { location.hash = "#/ticket/" + t.id; }, rowCls: t => t.unread ? "unread" : "",
    cols: [{ k: "ref", l: L("Ticket", "البلاغ"), h: t => `<b>${esc(t.ref)}</b>${t.unread ? " " + chip(L("New", "جديد"), "info") : ""}<span class="sub">${t.messages} ${esc(L("messages", "رسائل"))}${t.files ? ` · ${t.files} 📎` : ""}</span>` },
      { k: "subject", l: L("Subject", "الموضوع"), h: t => `${esc(t.subject)}<span class="sub" dir="ltr">${esc(t.app_user || "")} ${esc(t.email)}</span>` },
      { k: "company_name", l: L("Company", "الشركة") }, { k: "kind", l: L("Category", "التصنيف") },
      { k: "priority", l: L("Priority", "الأولوية"), v: t => ["low", "normal", "high", "urgent"].indexOf(t.priority), num: true, h: t => mapChip(PRIO(), t.priority) },
      { k: "st", l: L("Status", "الحالة"), v: t => t.status, h: t => mapChip(TK_ST(), t.status) + (t.assignee ? `<span class="sub">→ ${esc(t.assignee)}</span>` : "") },
      { k: "created_at", l: L("Opened", "فُتح"), date: true, h: t => esc(day(t.created_at)) }, { k: "updated_at", l: L("Updated", "آخر تحديث"), date: true, h: t => esc(ago(t.updated_at)) }],
    filters: [{ k: "st", l: L("Status", "الحالة"), type: "select", opts: [["notclosed", L("Not closed", "غير مغلقة")], ...Object.entries(TK_ST()).map(([k, v]) => [k, v[0]])], test: (t, v) => v === "notclosed" ? t.status !== "closed" : t.status === v },
      { k: "prio", l: L("Priority", "الأولوية"), type: "select", opts: Object.entries(PRIO()).map(([k, v]) => [k, v[0]]), test: (t, v) => t.priority === v },
      { k: "mine", l: L("Assigned", "مسند"), type: "select", opts: [["me", L("To me", "إليّ")], ["none", L("Nobody", "لا أحد")]], test: (t, v) => v === "me" ? (t.assignee || "").toLowerCase() === myEmail() : !t.assignee },
      { k: "unread", l: L("Unread", "غير مقروءة"), type: "select", opts: [["1", L("Unread only", "غير المقروءة فقط")]], test: t => t.unread },
      { k: "from", l: L("Opened from", "فُتح من"), type: "from", col: "created_at" }, { k: "to", l: L("Opened to", "فُتح حتى"), type: "to", col: "created_at" }] }));
};
// the queue: tickets waiting for the team first, then the plan's support priority, then whoever waited longest
const CHAT = () => ({ none: [L("No chat yet", "لا محادثة بعد"), ""], live: [L("Live chat", "محادثة مباشرة"), "ok"], ended: [L("Chat ended", "انتهت المحادثة"), ""] });
const rankChip = r => r.rank > 0 ? chip(`${"★".repeat(Math.min(r.rank, 5))} ${r.plan_name || r.plan || ""}`, r.rank >= 2 ? "bad" : "warn") : chip(r.plan_name || r.plan || L("Trial / none", "تجربة / بدون"), "");
PAGES.queue = async view => {
  const d = await rpc("console_queue"), rows = d.rows.map((r, i) => ({ ...r, qi: i + 1 })), now = Date.now();
  S.replies = d.replies; S.team = d.team;
  const team = rows.filter(r => r.turn === "team"), late = rows.filter(r => r.due_at && new Date(r.due_at) < now && !r.first_reply_at);
  if (!DT.queue) DT.queue = { f: { view: "team" }, q: "" };
  view.innerHTML = `<div class="kpis">${kpi(L("Waiting for us", "بانتظارنا"), num(team.length), team.length ? "warn" : "ok")}${kpi(L("Live chats", "محادثات مباشرة"), num(rows.filter(r => r.chat === "live").length))}
      ${kpi(L("Past the reply target", "تجاوزت موعد الرد"), num(late.length), late.length ? "bad" : "")}${kpi(L("Nobody assigned", "بلا مسؤول"), num(rows.filter(r => !r.assignee).length))}</div>
    <div class="card"><div id="qtbl"></div><p class="muted small">${esc(L("Order: tickets waiting for the team first, then the plan's support priority (Plans), then the longest wait. It refreshes every 20 seconds.",
      "الترتيب: البلاغات التي تنتظر الفريق أولاً، ثم أولوية الدعم في الباقة (الباقات)، ثم الأطول انتظاراً. يتحدّث كل 20 ثانية."))}</p></div>`;
  afterHooks.push(v => table($("#qtbl", v), "queue", rows, { sort: "qi", dir: "asc", search: ["ref", "subject", "email", "company_name", "app_user", "assignee", "plan_name"],
    onRow: t => { location.hash = "#/ticket/" + t.id; }, rowCls: t => (t.unread ? "unread " : "") + (t.due_at && new Date(t.due_at) < now && !t.first_reply_at ? "neg" : ""),
    cols: [{ k: "qi", l: "#", num: true, h: t => `<b>${t.qi}</b>` },
      { k: "rank", l: L("Priority", "الأولوية"), num: true, h: t => rankChip(t) },
      { k: "ref", l: L("Ticket", "البلاغ"), h: t => `<b>${esc(t.ref)}</b> ${esc(t.subject)}${t.unread ? " " + chip(L("New", "جديد"), "info") : ""}<span class="sub">${esc(t.company_name || "")} · <span dir="ltr">${esc(t.app_user || t.email)}</span></span>` },
      { k: "waiting_since", l: L("Waiting", "ينتظر"), date: true, h: t => t.turn === "team" ? `<b>${esc(ago(t.waiting_since || t.updated_at))}</b>` : `<span class="muted">${esc(L("Customer's turn", "دور العميل"))}</span>` },
      { k: "due_at", l: L("Reply due", "موعد الرد"), date: true, h: t => t.first_reply_at ? `<span class="muted">${esc(L("Answered", "تم الرد"))}</span>` : t.due_at ? esc(dt(t.due_at)) : "—" },
      { k: "chat", l: L("Chat", "المحادثة"), h: t => mapChip(CHAT(), t.chat) }, { k: "st", l: L("Status", "الحالة"), v: t => t.status, h: t => mapChip(TK_ST(), t.status) },
      { k: "assignee", l: L("Assigned", "المسؤول"), h: t => t.assignee ? esc(t.assignee.split("@")[0]) : `<span class="muted">—</span>` }],
    filters: [{ k: "view", l: L("Show", "عرض"), type: "select", opts: [["team", L("Waiting for us", "بانتظارنا")], ["live", L("Live chats", "المحادثات المباشرة")], ["mine", L("Assigned to me", "المسندة إليّ")], ["none", L("Nobody assigned", "بلا مسؤول")], ["late", L("Past the reply target", "تجاوزت موعد الرد")]],
        test: (t, v) => v === "team" ? t.turn === "team" : v === "live" ? t.chat === "live" : v === "mine" ? (t.assignee || "").toLowerCase() === myEmail() : v === "none" ? !t.assignee : v === "late" ? !!(t.due_at && new Date(t.due_at) < now && !t.first_reply_at) : true },
      { k: "plan", l: L("Plan", "الباقة"), type: "select", opts: [...new Map(rows.map(r => [r.plan || "", r.plan_name || r.plan || L("Trial / none", "تجربة / بدون")])).entries()], test: (t, v) => (t.plan || "") === v }] }));
  S.live = setInterval(() => { if (!document.hidden && parse().parts[0] === "queue" && !$("#panel")) route(); }, 20000);
};

// a voice note from the microphone: the first click starts, the second sends it to `done(file)` (3 minutes at most)
async function recordVoice(btn, done) {
  if (btn._rec) { btn._rec.stop(); return; }
  let stream; try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { return toast(L("The microphone is not available.", "الميكروفون غير متاح."), true); }
  const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(t => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
  const rec = btn._rec = new MediaRecorder(stream, type ? { mimeType: type } : {}), parts = [], label = btn.innerHTML; let left = 180;
  rec.ondataavailable = e => { if (e.data.size) parts.push(e.data); };
  const tick = setInterval(() => { left--; btn.innerHTML = `■ ${esc(L("Stop and send", "إيقاف وإرسال"))} ${180 - left}s`; if (left <= 0) rec.stop(); }, 1000);
  rec.onstop = () => { clearInterval(tick); stream.getTracks().forEach(t => t.stop()); btn._rec = null; btn.innerHTML = label; btn.classList.remove("danger");
    const mime = (rec.mimeType || "audio/webm").split(";")[0], ext = mime.includes("mp4") ? "m4a" : "webm";
    if (parts.length) done(new File(parts, `voice-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.${ext}`, { type: mime })); };
  rec.start(1000); btn.classList.add("danger"); btn.innerHTML = `■ ${esc(L("Stop and send", "إيقاف وإرسال"))} 0s`;
}

PAGES.ticket = async (view, parts) => {
  const id = +parts[1];
  let tk = await rpc("support_get", { p_ticket: id });
  const [cust, q, accAll] = await Promise.all([rpc("console_ticket_customer", { p_ticket: id }).catch(() => null), S.replies ? null : rpc("console_queue").catch(() => null),
    tk.company_id ? rpc("support_access_list", { p_company: tk.company_id }).catch(() => []) : []]);
  const acc = (accAll || []).filter(a => a.on);
  if (q) S.replies = q.replies;
  $("#ttl").textContent = `${tk.ref} — ${tk.subject}`; $("#crumb").textContent = L("Support queue", "قائمة الدعم");
  const first = String(tk.app_user || tk.email || "").split(/[@\s]/)[0];
  const send = async (body, files, internal) => {
    const up = []; for (const f of files) { if (f.size > 50 * 1048576) throw new Error(L(`${f.name} is larger than 50 MB.`, `${f.name} أكبر من 50 ميجابايت.`)); up.push(await upload("support-files", f)); }
    tk = await rpc("support_reply", { p_ticket: id, p_body: body, p_files: up, p_internal: internal });
    if (!internal) push("ticket", id, { text: (body || L("Sent a file", "أرسل ملفاً")).slice(0, 140) });
    draw(); };
  Object.assign(S.H, {
    reply: async el => { const body = $("#rbody").value, internal = $("#rint").checked, files = [...$("#rfiles").files];
      if (!body.trim() && !files.length) return toast(L("Write a reply or add a file.", "اكتب رداً أو أضف ملفاً."), true);
      el.disabled = true; try { await send(body, files, internal); $("#rbody").value = ""; $("#rfiles").value = ""; } finally { el.disabled = false; } },
    voice: el => recordVoice(el, f => send("", [f], $("#rint").checked).catch(fail)),
    canned: el => { const r = (S.replies || [])[+el.value]; el.value = ""; if (!r) return; const t = $("#rbody"); t.value = (t.value ? t.value + "\n" : "") + String(r.body || "").replace(/\{name\}/g, first); t.focus(); },
    tset: async el => { tk = await rpc("console_ticket_set", { p_ticket: id, p: { [el.name]: el.value } }); toast(L("Saved.", "تم الحفظ.")); draw(); },
    mine: async () => { tk = await rpc("console_ticket_set", { p_ticket: id, p: { assignee: myEmail() } }); draw(); },
    chatend: async () => {
      const d = document.createElement("div"); d.className = "shade top";
      d.innerHTML = `<div class="dlg"><h2>${esc(L("End the chat?", "إنهاء المحادثة؟"))}</h2><p>${esc(L("The chat closes on the customer's screen. Writing again later starts it again.", "تُغلق المحادثة على شاشة العميل. الكتابة لاحقاً تعيد فتحها."))}</p>
        <label class="chk"><input type="checkbox" id="cesolved" checked> ${esc(L("The issue is solved — mark the ticket solved", "تم حل المشكلة — تعليم البلاغ كمحلول"))}</label>
        <div class="row"><button class="btn" data-r="0">${esc(L("Cancel", "إلغاء"))}</button><button class="btn pri" data-r="1">${esc(L("End the chat", "إنهاء المحادثة"))}</button></div></div>`;
      document.body.appendChild(d);
      d.addEventListener("click", async e => { const b = e.target.closest("[data-r]"); if (!b && e.target !== d) return; const ok = b && b.dataset.r === "1", solved = $("#cesolved", d).checked; d.remove();
        if (!ok) return; try { tk = await rpc("support_chat_end", { p_ticket: id, p_solved: solved }); push("ticket", id, { text: L("The chat has ended.", "انتهت المحادثة.") }); draw(); } catch (er) { fail(er); } }); },
    tvisit: () => visitPanel({ ticket_id: id, company_id: tk.company_id }),
    tclose: async () => { const h = await rpc("console_help").catch(() => ({ categories: [] })), answered = (tk.messages || []).some(m => m.staff && !m.internal && m.kind !== "event");
      panel(L(`Close ${tk.ref}`, `إغلاق ${tk.ref}`), form([
        { k: "reply", l: answered ? L("Last message to the customer (optional)", "رسالة أخيرة للعميل (اختيارية)") : L("Your answer to the customer (required: every report gets an answer)", "ردك على العميل (مطلوب: كل بلاغ يحصل على رد)"), type: "area", rows: 3, span: 1, req: !answered },
        { k: "share", l: L("Share it in Help & FAQ so others can solve it themselves", "نشره في المساعدة والأسئلة الشائعة ليحلها الآخرون بأنفسهم"), type: "check", span: 1 },
        { type: "html", span: 1, html: `<div class="banner warn small">${esc(L("Only the title and the resolution below are published — write them without names, companies, figures or anything private. Nothing else from the ticket is shared.", "يُنشر العنوان والحل أدناه فقط — اكتبهما دون أسماء أو شركات أو أرقام أو أي شيء خاص. لا يُنشر أي شيء آخر من البلاغ."))}</div>` },
        { k: "category", l: L("Category", "التصنيف"), type: "select", opts: [["", "—"], ...h.categories.map(c => [c.key, (S.lang === "ar" && c.name_ar) || c.name])] },
        { k: "title", l: L("Title (the problem, in general words)", "العنوان (المشكلة بعبارة عامة)"), v: "" },
        { k: "title_ar", l: L("Title (Arabic)", "العنوان (عربي)"), v: "", dir: "rtl" }, { type: "html", html: "" },
        { k: "resolution", l: L("How it was resolved", "كيف حُلّت"), type: "area", rows: 4 }, { k: "resolution_ar", l: L("How it was resolved (Arabic)", "كيف حُلّت (عربي)"), type: "area", rows: 4, dir: "rtl" }]),
        async v => { tk = await rpc("console_ticket_close", { p_ticket: id, p: v }); if ((v.reply || "").trim()) push("ticket", id, { text: v.reply.slice(0, 140) }); toast(v.share ? L("Closed and shared.", "أُغلق ونُشر.") : L("Closed.", "أُغلق.")); }, { saveLabel: L("Close the ticket", "إغلاق البلاغ") }); },
    accend: async el => { if (!await confirmIt(L("End this access to the customer's books now?", "إنهاء هذا الوصول إلى دفاتر العميل الآن؟"))) return; await rpc("support_access_end", { p_id: +el.dataset.id }); route(); } });
  const who = m => m.staff ? (m.name ? `${m.name} (${m.email.split("@")[0]})` : m.email) : (tk.app_user && m.email === tk.email ? tk.app_user : m.email);
  const evt = b => { b = String(b || ""); const until = b.includes(":") ? dt(b.slice(b.indexOf(":") + 1)) : "";
    return b === "chat_ended_solved" ? L("Chat ended, ticket solved", "انتهت المحادثة وحُل البلاغ") : b === "chat_ended" ? L("Chat ended", "انتهت المحادثة")
      : b.startsWith("access_fix") ? L(`The customer let support view and fix the books until ${until}`, `سمح العميل للدعم بعرض الدفاتر وإصلاحها حتى ${until}`)
      : b.startsWith("access_view") ? L(`The customer let support view the books until ${until}`, `سمح العميل للدعم بعرض الدفاتر حتى ${until}`)
      : b === "access_ended" ? L("Access to the books ended", "انتهى الوصول إلى الدفاتر") : b; };
  const msg = (m, extra = "") => m.kind === "event" ? `<div class="evt">— ${esc(evt(m.body))}${m.staff && m.name ? " · " + esc(m.name) : ""} · ${esc(dt(m.created_at))} —</div>`
    : `<div class="msg ${m.staff ? "staff" : ""} ${m.internal ? "internal" : ""}"><div class="h"><b>${esc(who(m))}</b>${m.internal ? chip(L("Internal note", "ملاحظة داخلية"), "warn") : ""}<span class="muted">${esc(dt(m.created_at))}</span></div>
    ${m.body ? `<div class="t" dir="auto">${esc(m.body)}</div>` : ""}${fileList("support-files", m.files)}${extra}</div>`;
  const thread = () => msg({ email: tk.email, body: tk.body, files: tk.files, created_at: tk.created_at }, `<div class="muted small">${esc(tk.kind)}${tk.company_name ? " · " + esc(tk.company_name) : ""}</div>`) + (tk.messages || []).map(m => msg(m)).join("");
  const head = () => `<a class="btn" href="#/queue">← ${esc(L("Queue", "قائمة الانتظار"))}</a>${mapChip(TK_ST(), tk.status)}${mapChip(PRIO(), tk.priority)}${mapChip(CHAT(), tk.chat)}
    ${cust ? rankChip(cust.priority) : ""}<span class="sp"></span>${dcan("manage") && (tk.assignee || "").toLowerCase() !== myEmail() ? `<button class="btn" data-a="mine">✋ ${esc(L("Assign to me", "إسناد إليّ"))}</button>` : ""}
    ${tk.chat === "live" && dcan("manage") ? `<button class="btn danger" data-a="chatend">⏹ ${esc(L("End the chat", "إنهاء المحادثة"))}</button>` : ""}
    ${tk.status !== "closed" && dcan("close") ? `<button class="btn" data-a="tclose">✓ ${esc(L("Close…", "إغلاق…"))}</button>` : `${chip(tk.shared ? L("Shared as a solution", "منشور كحل") : L("Not shared", "غير منشور"), tk.shared ? "ok" : "")}`}`;
  const draw = () => { const h = $("#thead"), t = $("#thr"); if (!h || !t) return; h.innerHTML = head(); const end = t.scrollHeight - t.scrollTop - t.clientHeight < 60;
    t.innerHTML = thread(); previews(t); if (end) t.scrollTop = t.scrollHeight; };
  const diag = Object.entries(tk.diag || {}).filter(([, v]) => v), log = (tk.log || []).slice().reverse(), co = cust && cust.company, pr = cust && cust.priority;
  view.innerHTML = `<div class="row" id="thead" style="margin-bottom:12px;flex-wrap:wrap;gap:6px">${head()}</div>
    <div class="split"><div><div class="card thread" id="thr" style="max-height:62vh;overflow:auto">${thread()}</div>
      <div class="card"><div class="row" style="gap:8px;margin-bottom:6px"><h3 style="margin:0">${esc(L("Reply", "رد"))}</h3><span class="sp"></span>
        ${(S.replies || []).length ? `<select data-change="canned" style="max-width:260px"><option value="">💬 ${esc(L("Saved replies…", "الردود المحفوظة…"))}</option>${S.replies.map((r, i) => `<option value="${i}">${esc(r.title)}</option>`).join("")}</select>` : dcan("replies") ? `<a class="small" href="#/replies">💬 ${esc(L("Add saved replies", "إضافة ردود محفوظة"))}</a>` : ""}</div>
        <textarea id="rbody" rows="4" dir="auto" placeholder="${esc(L("Write to the customer… (Ctrl + Enter sends)", "اكتب للعميل… (Ctrl + Enter للإرسال)"))}"></textarea>
        <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap"><input type="file" id="rfiles" multiple accept="image/*,video/*,audio/*,application/pdf,.txt,.xlsx,.zip">
          ${navigator.mediaDevices && window.MediaRecorder ? `<button type="button" class="btn" data-a="voice">🎤 ${esc(L("Voice note", "رسالة صوتية"))}</button>` : ""}</div>
        <div class="row" style="margin-top:8px"><label class="chk"><input type="checkbox" id="rint"> ${esc(L("Internal note (the customer does not see it)", "ملاحظة داخلية (لا يراها العميل)"))}</label><span class="sp"></span><button class="btn pri" data-a="reply">${esc(L("Send", "إرسال"))}</button></div>
        <p class="muted small" style="margin:6px 0 0">${esc(L("Your answer opens a chat at the bottom right of the customer's screen; they see your first name only. End the chat when the issue is done.", "يفتح ردك محادثة أسفل يمين شاشة العميل؛ يرى اسمك الأول فقط. أنهِ المحادثة عند انتهاء المشكلة."))}</p></div></div>
    <div>${acc.length ? `<div class="card"><h3>🔑 ${esc(L("Access to the customer's books", "الوصول إلى دفاتر العميل"))}</h3>${acc.map(a => `<div class="banner ${a.mode === "fix" ? "warn" : "info"} small" style="margin-bottom:6px">
        ${esc(L(`${a.agent_name} (${a.agent}) may ${a.mode === "fix" ? "view and fix" : "view"} until ${dt(a.expires_at)}.`, `يمكن لـ${a.agent_name} (${a.agent}) ${a.mode === "fix" ? "العرض والإصلاح" : "العرض"} حتى ${dt(a.expires_at)}.`))}
        <button class="btn sm" data-a="accend" data-id="${a.id}">${esc(L("End", "إنهاء"))}</button></div>`).join("")}
        ${acc.some(a => a.agent === myEmail()) ? `<a class="btn pri" href="index.html" target="_blank" rel="noopener">↗ ${esc(L("Open their books", "فتح دفاترهم"))}</a>
          <p class="muted small">${esc(L(`In the ERP, choose “${tk.company_name || ""}” in the company list. Changes are signed with your name; when the time is up you are out.`, `في النظام اختر «${tk.company_name || ""}» من قائمة الشركات. التغييرات تُوقّع باسمك؛ وعند انتهاء المدة تخرج تلقائياً.`))}</p>` : ""}</div>`
      : tk.company_id && tk.status !== "closed" ? `<div class="card"><p class="muted small" style="margin:0">🔑 ${esc(L("To look into their books, ask the customer to let you in (🔑 in their chat, or on the ticket).", "للاطلاع على دفاترهم، اطلب من العميل السماح لك بالدخول (🔑 في محادثته أو في البلاغ)."))}</p></div>` : ""}
      ${cust ? `<div class="card"><h3>${esc(L("Customer", "العميل"))}</h3><p class="small" style="margin:0 0 8px"><b dir="ltr">${esc(tk.app_user || "")} ${esc(tk.email)}</b></p>
        <div class="small">${pr ? `<div><b>${esc(L("Support priority", "أولوية الدعم"))}</b>: ${pr.rank} · ${esc(pr.plan_name || pr.plan || L("Trial / none", "تجربة / بدون"))}${pr.reply_hours ? ` · ${esc(L(`first reply within ${pr.reply_hours} h`, `أول رد خلال ${pr.reply_hours} ساعة`))}` : ""}</div>` : ""}
          ${co ? `<div><b>${esc(L("Company", "الشركة"))}</b>: ${can("customers") ? `<a href="#/company/${co.id}">${esc(co.name)}</a>` : esc(co.name)} · ${esc(co.status || "")}${co.paid_until ? ` · ${esc(L("paid until", "مدفوعة حتى"))} ${esc(day(co.paid_until))}` : co.trial_ends ? ` · ${esc(L("trial until", "تجربة حتى"))} ${esc(day(co.trial_ends))}` : ""}</div>
            <div><b>${esc(L("People", "الأشخاص"))}</b>: ${num(co.users)} · ${esc(L("admins", "المسؤولون"))}: <span dir="ltr">${esc((co.admins || []).join(", "))}</span></div>` : ""}</div>
        ${(cust.tickets || []).length ? `<h4 style="margin:12px 0 4px">${esc(L("Earlier tickets", "بلاغات سابقة"))}</h4><div class="small">${cust.tickets.map(o => `<div><a href="#/ticket/${o.id}">${esc(o.ref)}</a> ${esc(o.subject)} ${mapChip(TK_ST(), o.status)}</div>`).join("")}</div>` : ""}
        ${(cust.visits || []).length ? `<h4 style="margin:12px 0 4px">${esc(L("Visits", "الزيارات"))}</h4><div class="small">${cust.visits.map(o => `<div><a href="#/visit/${o.id}">${esc(o.ref)}</a> ${mapChip(VIS_ST(), o.status)}</div>`).join("")}</div>` : ""}</div>` : ""}
      <div class="card"><h3>${esc(L("Ticket", "البلاغ"))}</h3><div class="grid" style="gap:10px">
        <label class="f">${esc(L("Status", "الحالة"))}<select name="status" data-change="tset">${Object.entries(TK_ST()).map(([k, v]) => `<option value="${k}" ${k === tk.status ? "selected" : ""}>${esc(v[0])}</option>`).join("")}</select></label>
        <label class="f">${esc(L("Priority", "الأولوية"))}<select name="priority" data-change="tset">${Object.entries(PRIO()).map(([k, v]) => `<option value="${k}" ${k === tk.priority ? "selected" : ""}>${esc(v[0])}</option>`).join("")}</select></label>
        <label class="f">${esc(L("Assigned to", "مسند إلى"))}<select name="assignee" data-change="tset"><option value="">—</option>${(((q || {}).team) || S.team || []).concat(tk.assignee && !((q || {}).team || S.team || []).some(m => m.email === tk.assignee) ? [{ email: tk.assignee }] : [])
          .map(m => `<option value="${esc(m.email)}" ${m.email === tk.assignee ? "selected" : ""}>${esc(m.name || m.email)}</option>`).join("")}</select></label></div>
      <p class="small"><b>${esc(L("Opened", "فُتح"))}</b>: ${esc(dt(tk.created_at))}${tk.first_reply_at ? `<br><b>${esc(L("First reply", "أول رد"))}</b>: ${esc(dt(tk.first_reply_at))}` : ""}</p>
      ${tk.company_id && dcan("visits") ? `<button class="btn" data-a="tvisit">🚗 ${esc(L("Arrange a visit for this…", "ترتيب زيارة لهذا…"))}</button>` : ""}</div>
      ${diag.length ? `<div class="card"><h3>${esc(L("Technical details", "التفاصيل الفنية"))}</h3><div class="small">${diag.map(([k, v]) => `<div><b>${esc(k)}</b>: ${esc(v)}</div>`).join("")}</div></div>` : ""}
      ${log.length ? `<div class="card"><h3>${esc(L("What happened before the report", "ما حدث قبل البلاغ"))} <span class="muted">(${log.length})</span></h3><div class="tbl log"><table><tbody>${log.map(a => `<tr class="${a.t === "error" || (a.t === "api" && (a.s >= 400 || a.s === "err")) ? "neg" : ""}"><td class="muted" dir="ltr">${esc(a.at || "")}</td><td>${esc(a.t)}</td><td dir="ltr">${esc(a.t === "api" ? `${a.m} ${a.p} → ${a.s}${a.ms != null ? ` · ${a.ms} ms` : ""}${a.e ? ` · ${a.e}` : ""}` : a.t === "screen" ? a.h : a.m || "")}</td></tr>`).join("")}</tbody></table></div></div>` : ""}</div></div>`;
  if (q) S.team = q.team;
  afterHooks.push(v => { if (!dcan("manage")) $$("[data-change=tset]", v).forEach(x => { x.disabled = true; });
    const ri = $("#rint", v); if (ri && !dcan("reply")) { ri.checked = true; ri.disabled = true; $("#rbody", v).placeholder = L("Internal note for the team (you cannot write to the customer)", "ملاحظة داخلية للفريق (لا يمكنك الكتابة للعميل)"); }
    if (ri && !dcan("reply") && !dcan("notes")) { const c = ri.closest(".card"); if (c) c.innerHTML = `<p class="muted" style="margin:0">👁 ${esc(L("You can read this ticket. Replying is not part of your access.", "يمكنك قراءة هذا البلاغ. الرد ليس ضمن صلاحياتك."))}</p>`; }
    const t = $("#thr", v); if (t) t.scrollTop = t.scrollHeight;
    const ta = $("#rbody", v); if (ta) ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("[data-a=reply]").click(); } }); });
  // new messages appear by themselves while the ticket is open
  S.live = setInterval(async () => { if (document.hidden || parse().parts[0] !== "ticket" || +parse().parts[1] !== id) return;
    try { const n = await rpc("support_get", { p_ticket: id }); if (n.messages.length !== tk.messages.length || n.status !== tk.status || n.chat !== tk.chat) {
      if (n.messages.slice(tk.messages.length).some(m => m.kind !== "event" && !m.staff)) beep();
      const access = n.messages.slice(tk.messages.length).some(m => m.kind === "event" && /^access_/.test(m.body));   // let in or out: the side panel changes too
      tk = n; if (access && !($("#rbody") || {}).value) route(); else draw(); } } catch { /* next time */ } }, 5000);
};

// bug reports: every one, open and closed; each is answered and closed (shared or not) from its ticket
PAGES.bugs = async view => {
  const rows = await rpc("console_bugs");
  if (!DT.bugs) DT.bugs = { f: { st: "open" }, q: "" };
  view.innerHTML = `<div class="kpis">${kpi(L("Open", "مفتوحة"), num(rows.filter(r => r.status !== "closed").length), rows.some(r => r.status !== "closed") ? "warn" : "ok")}
      ${kpi(L("With errors recorded", "بها أخطاء مسجلة"), num(rows.filter(r => r.errors > 0 && r.status !== "closed").length))}${kpi(L("Closed", "مغلقة"), num(rows.filter(r => r.status === "closed").length))}
      ${kpi(L("Shared as solutions", "منشورة كحلول"), num(rows.filter(r => r.shared).length), "ok")}</div><div class="card"><div id="btbl"></div></div>`;
  afterHooks.push(v => table($("#btbl", v), "bugs", rows, { sort: "created_at", dir: "desc", search: ["ref", "subject", "email", "company_name", "version", "screen"],
    onRow: t => { location.hash = "#/ticket/" + t.id; }, rowCls: t => t.unread ? "unread" : "",
    cols: [{ k: "ref", l: L("Report", "البلاغ"), h: t => `<b>${esc(t.ref)}</b> ${esc(t.subject)}<span class="sub">${esc(t.company_name || "")} · <span dir="ltr">${esc(t.email)}</span></span>` },
      { k: "errors", l: L("Errors", "الأخطاء"), num: true, h: t => t.errors ? chip(String(t.errors), "bad") : "—" }, { k: "files", l: "📎", num: true },
      { k: "version", l: L("Version", "الإصدار"), h: t => esc(t.version || "—") }, { k: "screen", l: L("Screen", "الشاشة"), h: t => `<span dir="ltr">${esc(t.screen || "—")}</span>` },
      { k: "st", l: L("Status", "الحالة"), v: t => t.status, h: t => mapChip(TK_ST(), t.status) + (t.status === "closed" ? " " + chip(t.shared ? L("Shared", "منشور") : L("Private", "خاص"), t.shared ? "ok" : "") : "") },
      { k: "assignee", l: L("Assigned", "المسؤول"), h: t => t.assignee ? esc(t.assignee.split("@")[0]) : "—" }, { k: "created_at", l: L("Reported", "أُبلغ"), date: true, h: t => esc(dt(t.created_at)) }],
    filters: [{ k: "st", l: L("Status", "الحالة"), type: "select", opts: [["open", L("Not closed", "غير مغلقة")], ["closed", L("Closed", "مغلقة")]], test: (t, v) => v === "open" ? t.status !== "closed" : t.status === "closed" }] }));
};

// the answer's light formatting, as the app shows it (erp/static/app.js, helpMd)
const HELP_FORMAT = () => L("Formatting: ## heading · - list · 1. numbered list · > note · | a | b | table (then |---|---|) · **bold** · *italic* · "
  + "[text](#/screen) · ![caption](help/picture.jpg or clip.webm) on its own line, from the site's help/ folder.",
  "التنسيق: ## عنوان · - قائمة · 1. قائمة مرقمة · > ملاحظة · | أ | ب | جدول (ثم |---|---|) · **عريض** · *مائل* · "
  + "[نص](#/شاشة) · ![وصف](help/picture.jpg أو clip.webm) في سطر مستقل، من مجلد help/ في الموقع.");

// Help & FAQ: categories and questions in both languages (what customers read in the app's Help & FAQ)
PAGES.help = async view => {
  const d = await rpc("console_help"), cats = d.categories, catName = k => { const c = cats.find(x => x.key === k); return c ? ((S.lang === "ar" && c.name_ar) || c.name) : "—"; };
  const faqEdit = f => panel(f ? L("Edit question", "تعديل السؤال") : L("New question", "سؤال جديد"), form([
      { k: "category", l: L("Category", "التصنيف"), type: "select", v: f ? f.category || "" : "", opts: [["", "—"], ...cats.map(c => [c.key, (S.lang === "ar" && c.name_ar) || c.name])] },
      { k: "sort", l: L("Order", "الترتيب"), type: "number", v: f ? f.sort : 0 },
      { k: "q", l: L("Question", "السؤال"), v: f ? f.q : "", req: 1 }, { k: "q_ar", l: L("Question (Arabic)", "السؤال (عربي)"), v: f ? f.q_ar : "", dir: "rtl" },
      { k: "a", l: L("Answer", "الإجابة"), type: "area", rows: 7, v: f ? f.a : "", req: 1, help: HELP_FORMAT() },
      { k: "a_ar", l: L("Answer (Arabic)", "الإجابة (عربي)"), type: "area", rows: 7, v: f ? f.a_ar : "", dir: "rtl" },
      { k: "published", l: L("Published (customers see it)", "منشور (يراه العملاء)"), type: "check", v: f ? f.published : true, span: 1 }]),
    async v => { await rpc("console_help_faq", { p: { ...v, id: f ? f.id : null, sort: +v.sort || 0 } }); toast(L("Saved.", "تم الحفظ.")); },
    { wide: true, extra: f ? `<button type="button" class="btn danger" data-a="faqdel" data-id="${f.id}">${esc(L("Delete", "حذف"))}</button>` : "" });
  const catEdit = c => panel(c ? c.name : L("New category", "تصنيف جديد"), form([
      { k: "key", l: L("Code (letters, never changes)", "الكود (حروف، لا يتغير)"), v: c ? c.key : "", req: 1, dir: "ltr", dis: !!c },
      { k: "icon", l: L("Icon (an emoji)", "الأيقونة (رمز تعبيري)"), v: c ? c.icon : "" }, { k: "name", l: L("Name", "الاسم"), v: c ? c.name : "", req: 1 },
      { k: "name_ar", l: L("Name (Arabic)", "الاسم (عربي)"), v: c ? c.name_ar : "", dir: "rtl" }, { k: "sort", l: L("Order", "الترتيب"), type: "number", v: c ? c.sort : 0 }]),
    async v => { await rpc("console_help_category", { p: { ...v, key: c ? c.key : v.key.trim().toLowerCase().replace(/[^a-z0-9_-]/g, ""), sort: +v.sort || 0 } }); toast(L("Saved.", "تم الحفظ.")); },
    { extra: c ? `<button type="button" class="btn danger" data-a="catdel" data-k="${esc(c.key)}">${esc(L("Delete", "حذف"))}</button>` : "" });
  Object.assign(S.H, { faqnew: () => faqEdit(null), faqedit: el => faqEdit(d.faq.find(f => f.id === +el.dataset.id)), catnew: () => catEdit(null), catedit: el => catEdit(cats.find(c => c.key === el.dataset.k)),
    faqdel: async el => { if (await confirmIt(L("Delete this question?", "حذف هذا السؤال؟"), { danger: 1 })) { closePanel(); await rpc("console_help_faq", { p: { id: +el.dataset.id, delete: true } }); route(); } },
    catdel: async el => { if (await confirmIt(L("Delete this category? Its questions stay, without a category.", "حذف هذا التصنيف؟ تبقى أسئلته دون تصنيف."), { danger: 1 })) { closePanel(); await rpc("console_help_category", { p: { key: el.dataset.k, delete: true } }); route(); } } });
  view.innerHTML = `<div class="split" style="grid-template-columns:minmax(0,1fr) 320px"><div class="card"><div class="row" style="margin-bottom:10px"><h3 style="margin:0">${esc(L("Questions", "الأسئلة"))} <span class="muted">(${d.faq.length})</span></h3><span class="sp"></span>
        <button class="btn pri" data-a="faqnew">＋ ${esc(L("New question", "سؤال جديد"))}</button></div>
      ${d.faq.length ? `<div class="tbl"><table><thead><tr><th>${esc(L("Question", "السؤال"))}</th><th>${esc(L("Category", "التصنيف"))}</th><th>${esc(L("Arabic", "العربية"))}</th><th></th></tr></thead><tbody>${d.faq.map(f => `<tr>
        <td><b dir="auto">${esc(f.q)}</b>${f.published ? "" : " " + chip(L("Draft", "مسودة"), "warn")}<span class="sub" dir="auto">${esc(String(f.a).slice(0, 110))}</span></td><td>${esc(catName(f.category))}</td>
        <td>${f.q_ar && f.a_ar ? chip("✓", "ok") : chip(L("Missing", "ناقصة"), "warn")}</td><td class="r"><button class="btn sm" data-a="faqedit" data-id="${f.id}">${esc(L("Edit", "تعديل"))}</button></td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">${esc(L("No questions yet. Add the ones customers ask most; they appear in the app's Help & FAQ and in the suggestions while someone writes a report.", "لا أسئلة بعد. أضف أكثر ما يسأله العملاء؛ تظهر في المساعدة والأسئلة الشائعة في النظام وفي الاقتراحات أثناء كتابة البلاغ."))}</div>`}</div>
    <div class="card"><div class="row" style="margin-bottom:10px"><h3 style="margin:0">${esc(L("Categories", "التصنيفات"))}</h3><span class="sp"></span><button class="btn sm" data-a="catnew">＋</button></div>
      ${cats.map(c => `<div class="row" style="padding:6px 0;border-bottom:1px solid var(--line)"><span>${esc(c.icon || "•")} ${esc((S.lang === "ar" && c.name_ar) || c.name)}</span><span class="sp"></span>
        <span class="muted small">${d.faq.filter(f => f.category === c.key).length}</span><button class="btn sm" data-a="catedit" data-k="${esc(c.key)}">${esc(L("Edit", "تعديل"))}</button></div>`).join("")}</div></div>`;
};

// solutions shared from closed tickets (only a title and a resolution; never anything about the customer)
PAGES.solved = async view => {
  const d = await rpc("console_help"), cats = d.categories;
  const edit = x => panel(x.title, form([
      { k: "category", l: L("Category", "التصنيف"), type: "select", v: x.category || "", opts: [["", "—"], ...cats.map(c => [c.key, (S.lang === "ar" && c.name_ar) || c.name])] },
      { k: "published", l: L("Published (customers see it)", "منشور (يراه العملاء)"), type: "check", v: x.published },
      { k: "title", l: L("Title", "العنوان"), v: x.title, req: 1 }, { k: "title_ar", l: L("Title (Arabic)", "العنوان (عربي)"), v: x.title_ar, dir: "rtl" },
      { k: "resolution", l: L("How it was resolved", "كيف حُلّت"), type: "area", rows: 6, v: x.resolution, req: 1 }, { k: "resolution_ar", l: L("How it was resolved (Arabic)", "كيف حُلّت (عربي)"), type: "area", rows: 6, v: x.resolution_ar, dir: "rtl" }]),
    async v => { await rpc("console_help_solved", { p: { ...v, id: x.id } }); toast(L("Saved.", "تم الحفظ.")); },
    { wide: true, extra: `<button type="button" class="btn danger" data-a="soldel" data-id="${x.id}">${esc(L("Delete", "حذف"))}</button>` });
  Object.assign(S.H, { soledit: el => edit(d.solved.find(x => x.id === +el.dataset.id)),
    soldel: async el => { if (await confirmIt(L("Delete this shared solution?", "حذف هذا الحل المنشور؟"), { danger: 1 })) { closePanel(); await rpc("console_help_solved", { p: { id: +el.dataset.id, delete: true } }); route(); } } });
  view.innerHTML = `<div class="card"><p class="muted small" style="margin-top:0">${esc(L("Shared when a ticket is closed (Close… → Share it). Only the title and the resolution are public; the ticket stays private and is shown here for the team only.", "تُنشر عند إغلاق البلاغ (إغلاق… ← نشره). العنوان والحل فقط علنيان؛ يبقى البلاغ خاصاً ويظهر هنا للفريق فقط."))}</p>
    ${d.solved.length ? `<div class="tbl"><table><thead><tr><th>${esc(L("Title", "العنوان"))}</th><th>${esc(L("Category", "التصنيف"))}</th><th>${esc(L("From ticket", "من البلاغ"))}</th><th>${esc(L("Shared", "النشر"))}</th><th></th></tr></thead><tbody>
      ${d.solved.map(x => `<tr><td><b dir="auto">${esc(x.title)}</b>${x.kind === "bug" ? " " + chip(L("Bug", "خلل"), "") : ""}<span class="sub" dir="auto">${esc(String(x.resolution).slice(0, 120))}</span></td>
        <td>${esc(((cats.find(c => c.key === x.category) || {}).name) || "—")}</td><td>${x.ticket_id ? `<a href="#/ticket/${x.ticket_id}">${esc(x.ticket_ref || "")}</a>` : "—"}</td>
        <td>${x.published ? chip(L("Published", "منشور"), "ok") : chip(L("Hidden", "مخفي"), "")}</td><td class="r"><button class="btn sm" data-a="soledit" data-id="${x.id}">${esc(L("Edit", "تعديل"))}</button></td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${esc(L("Nothing shared yet.", "لم يُنشر شيء بعد."))}</div>`}</div>`;
};

// saved replies the team picks from in a ticket ({name}: the customer's first name)
PAGES.replies = async view => {
  const list = (await rpc("console_queue")).replies || [];
  S.replies = list;
  const save = async next => { S.replies = await rpc("console_replies_save", { p: next }); toast(L("Saved.", "تم الحفظ.")); route(); };
  const edit = i => panel(i == null ? L("New saved reply", "رد محفوظ جديد") : list[i].title, form([
    { k: "title", l: L("Title (what the team picks)", "العنوان (ما يختاره الفريق)"), v: i == null ? "" : list[i].title, req: 1, span: 1 },
    { k: "body", l: L("Text — {name} becomes the customer's first name", "النص — {name} يصبح الاسم الأول للعميل"), type: "area", rows: 8, v: i == null ? "" : list[i].body, req: 1, span: 1 }], 1),
    v => { const next = list.slice(), r = { title: v.title.trim(), body: v.body.trim() }; if (i == null) next.push(r); else next[i] = r; return save(next); },
    { extra: i != null ? `<button type="button" class="btn danger" data-a="rdel">${esc(L("Delete", "حذف"))}</button>` : "" });
  Object.assign(S.H, { radd: () => edit(null), redit: el => { edit(+el.dataset.i); S.H.rdel = async () => { if (await confirmIt(L("Delete this saved reply?", "حذف هذا الرد المحفوظ؟"), { danger: 1 })) { closePanel(); save(list.filter((_, j) => j !== +el.dataset.i)); } }; } });
  view.innerHTML = `<div class="card"><div class="row" style="margin-bottom:10px"><p class="muted small" style="margin:0">${esc(L("Ready answers for common questions. In a ticket, pick one to add it to your reply; {name} becomes the customer's first name.", "إجابات جاهزة للأسئلة المتكررة. في البلاغ، اختر واحدة لإضافتها إلى ردك؛ {name} يصبح الاسم الأول للعميل."))}</p><span class="sp"></span>
      <button class="btn pri" data-a="radd">＋ ${esc(L("New saved reply", "رد محفوظ جديد"))}</button></div>
    ${list.length ? `<div class="tbl"><table><tbody>${list.map((r, i) => `<tr><td style="width:30%"><b>${esc(r.title)}</b></td><td class="muted" dir="auto">${esc(String(r.body).slice(0, 160))}</td><td class="r"><button class="btn sm" data-a="redit" data-i="${i}">${esc(L("Edit", "تعديل"))}</button></td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">${esc(L("No saved replies yet.", "لا ردود محفوظة بعد."))}</div>`}</div>`;
};

// ================================================================== the website's texts: home page, sign-in screens, footer
// Every text the public pages show from the ERP's own wording (site-texts.json, made by the build from the ERP's script)
// can be rewritten here in English and Arabic; a blank language keeps the built-in wording. Saved for everyone at once.
async function websiteTexts(view, q) {
  const [cat, cur] = await Promise.all([fetch("site-texts.json", { cache: "no-store" }).then(r => (r.ok ? r.json() : null)).catch(() => null), rpc("site_texts_admin")]);
  if (!cat || !(cat.texts || []).length) {
    view.innerHTML = `<div class="card"><div class="empty">${esc(L("The list of texts comes with the website build (site-texts.json) and is not on this site yet. It appears after the next deploy.", "تأتي قائمة النصوص مع بناء الموقع (site-texts.json) وليست على هذا الموقع بعد. تظهر بعد النشر القادم."))}</div></div>`;
    return;
  }
  const places = cat.places || [], place = places.some(p => p.key === q.place) ? q.place : (places[0] || {}).key, find = (q.q || "").trim().toLowerCase();
  const rows = cat.texts.filter(x => x.place === place && (!find || [x.key, x.en, x.ar, (cur[x.key] || {}).en, (cur[x.key] || {}).ar].some(v => String(v || "").toLowerCase().includes(find))));
  const changed = cat.texts.filter(x => cur[x.key]).length, pname = p => (S.lang === "ar" ? p.ar : p.en) || p.key;
  const same = x => x.en === x.ar;              // an address or a link: one box, the same for both languages
  const field = (x, lang) => { const v = (cur[x.key] || {})[lang] || "", ph = x[lang] || "", dir = lang === "ar" && !same(x) ? "rtl" : "ltr";
    return x.multiline || ph.length > 70 ? `<textarea name="${esc(x.key)}|${lang}" rows="${Math.min(4, ph.split("\n").length + 1)}" dir="${dir}" placeholder="${esc(ph)}">${esc(v)}</textarea>`
      : `<input name="${esc(x.key)}|${lang}" dir="${dir}" value="${esc(v)}" placeholder="${esc(ph)}">`; };
  Object.assign(S.H, {
    txplace: el => { location.hash = "#/texts?place=" + encodeURIComponent(el.dataset.k); },
    txfind: el => { location.hash = "#/texts?place=" + encodeURIComponent(place) + (el.value.trim() ? "&q=" + encodeURIComponent(el.value.trim()) : ""); },
    txsave: async () => {
      const f = $("#txf"), out = {};
      for (const x of rows) {
        const en = (f.elements[x.key + "|en"] || {}).value || "", ar = same(x) ? en : (f.elements[x.key + "|ar"] || {}).value || "", was = cur[x.key] || {};
        if (en.trim() !== (was.en || "") || ar.trim() !== (was.ar || "")) out[x.key] = { en, ar };
      }
      if (!Object.keys(out).length) return toast(L("Nothing changed.", "لم يتغير شيء."));
      await rpc("site_texts_save", { p: out });
      toast(L(`Saved ${Object.keys(out).length} text(s). Visitors see them the next time a page opens.`, `حُفظ ${Object.keys(out).length} نص. يراها الزوار عند فتح الصفحة التالية.`)); route(); },
    txreset: async el => { const k = el.dataset.k;
      if (!await confirmIt(L("Go back to the built-in wording for this text (both languages)?", "العودة إلى الصياغة الأصلية لهذا النص (باللغتين)؟"))) return;
      await rpc("site_texts_save", { p: { [k]: { en: "", ar: "" } } }); toast(L("Back to the built-in wording.", "عاد إلى الصياغة الأصلية.")); route(); },
  });
  view.innerHTML = `<div class="card"><div class="row" style="gap:8px;flex-wrap:wrap">
      ${places.map(p => `<button class="btn sm ${p.key === place ? "pri" : ""}" data-a="txplace" data-k="${esc(p.key)}">${esc(pname(p))} <span class="small" style="opacity:.7">${cat.texts.filter(x => x.place === p.key).length}</span></button>`).join("")}
      <span class="sp"></span><input type="search" placeholder="${esc(L("Search the texts", "ابحث في النصوص"))}" value="${esc(q.q || "")}" data-change="txfind" style="max-width:240px"></div>
    <p class="muted small" style="margin:10px 0 0">${esc(L("Write the wording visitors should see; leave a language blank to keep the built-in wording shown faintly in the box. A line break in a heading starts a new line on the page. Links must start with https://.", "اكتب الصياغة التي يراها الزوار؛ اترك اللغة فارغة للإبقاء على الصياغة الأصلية الظاهرة باهتة في الخانة. سطر جديد في العنوان يبدأ سطراً جديداً في الصفحة. يجب أن تبدأ الروابط بـ https://."))}
      ${changed ? " " + chip(L(`${changed} changed`, `${changed} معدّل`), "warn") : ""}</p></div>
    <form id="txf" class="card" onsubmit="return false">${rows.length ? `<div class="tbl"><table><thead><tr><th style="width:22%">${esc(L("Text", "النص"))}</th><th>English</th><th>العربية</th><th></th></tr></thead><tbody>
      ${rows.map(x => { const c = cur[x.key]; return `<tr><td><b class="small" dir="auto">${esc(x.en.split("\n").join(" ").slice(0, 60))}</b><span class="sub" dir="ltr">${esc(x.key)}</span>
          ${c ? `<span class="sub">${esc(L("Changed", "عُدّل"))} ${esc(day(c.updated_at))}${c.updated_by ? " · " + esc(c.updated_by) : ""}</span>` : ""}</td>
        ${same(x) ? `<td colspan="2">${field(x, "en")}<span class="sub">${esc(L("The same in both languages", "واحد في اللغتين"))}</span></td>` : `<td>${field(x, "en")}</td><td>${field(x, "ar")}</td>`}
        <td class="r">${c ? `<button type="button" class="btn sm" data-a="txreset" data-k="${esc(x.key)}" title="${esc(L("Back to the built-in wording", "العودة إلى الصياغة الأصلية"))}">↺</button>` : ""}</td></tr>`; }).join("")}</tbody></table></div>
      <div class="row" style="margin-top:12px"><button class="btn pri" data-a="txsave">${esc(L("Save", "حفظ"))}</button></div>`
      : `<div class="empty">${esc(L("No text matches.", "لا يوجد نص مطابق."))}</div>`}</form>`;
}

// ---- the other tabs: the layout and the announcement bar, pictures and clips, the footer's links (site_config)
const WEB_TABS = () => [["texts", L("Texts", "النصوص")], ["layout", L("Layout & announcement", "التخطيط والإعلان")], ["media", L("Pictures & clips", "الصور والمقاطع")],
  ["links", L("Footer links", "روابط التذييل")]];
const SITE_URL = /^https:\/\/[^\s"'<>]+$/;
const SITE_BUILTIN_LINKS = [{ en: "LinkedIn", ar: "", url: "https://www.linkedin.com/company/mybeyonderp" },
  { en: "Facebook", ar: "", url: "https://www.facebook.com/people/Beyond-ERP/61595801625159/" }, { en: "Instagram", ar: "", url: "https://www.instagram.com/mybeyonderp/" }];
const siteMediaUrl = p => { const c = cfg(); return c && p ? `${c.url}/storage/v1/object/public/site-media/${String(p).split("/").map(encodeURIComponent).join("/")}` : ""; };
const siteMediaTag = (p, kind, extra = "") => !p ? "" : kind === "video"
  ? `<video src="${esc(siteMediaUrl(p))}" muted loop autoplay playsinline preload="metadata" ${extra}></video>` : `<img src="${esc(siteMediaUrl(p))}" alt="" ${extra}>`;
async function siteConfig() { return ((await rpc("site_public")) || {}).site || {}; }
async function siteConfigSave(next, msg) {
  await rpc("site_config_save", { p: next });
  toast(msg || L("Saved. Visitors see it the next time a page opens.", "حُفظ. يراه الزوار عند فتح الصفحة التالية.")); route();
}
const SITE_PARTS = () => [["books", L("Scene 2 — Books that balance themselves", "المشهد 2 — دفاتر متوازنة دائماً")], ["projects", L("Scene 3 — Profit by project", "المشهد 3 — الربح لكل مشروع")],
  ["systems", L("Scene 4 — All your systems in one place", "المشهد 4 — جميع أنظمتك في مكان واحد")], ["forecast", L("Scene 5 — Price and cost forecasting", "المشهد 5 — توقع الأسعار والتكاليف")],
  ["end", L("Scene 6 — The closing scene", "المشهد 6 — المشهد الختامي")], ["band", L("The band with the list of systems", "شريط قائمة الأنظمة")],
  ["email", L("The email address in the footer", "البريد الإلكتروني في التذييل")], ["links", L("The links in the footer", "الروابط في التذييل")]];

async function websiteLayout(view) {
  const site = await siteConfig(), a = site.announcement || {}, hide = site.hide || [], sc = site.showcase || {};
  const bar = v => !v.a_en && !v.a_ar ? "" : `<div class="site-prev ${esc(v.a_tone || "info")}"><span>${esc(S.lang === "ar" && v.a_ar ? v.a_ar : v.a_en)}</span>${v.a_link ? ` <u>${esc((S.lang === "ar" && v.a_link_ar) || v.a_link_en || L("Learn more", "اعرف المزيد"))}</u>` : ""} <span class="muted">×</span></div>`;
  S.H.wlsave = async () => { const v = readForm($("#wlf"));
    const ann = { on: !!v.a_on, en: v.a_en.trim(), ar: v.a_ar.trim(), link: v.a_link.trim(), link_en: v.a_link_en.trim(), link_ar: v.a_link_ar.trim(), tone: v.a_tone, where: v.a_where };
    if (ann.on && !ann.en) throw new Error(L("Write the announcement in English (Arabic is optional).", "اكتب الإعلان بالإنجليزية (العربية اختيارية)."));
    if (ann.link && !SITE_URL.test(ann.link)) throw new Error(L("The announcement's link must start with https://", "يجب أن يبدأ رابط الإعلان بـ https://"));
    if (v.sc_on && !sc.media) throw new Error(L("Add the showcase's picture or clip first (Pictures & clips).", "أضف صورة العرض أو مقطعه أولاً (الصور والمقاطع)."));
    await siteConfigSave({ ...site, announcement: ann, hide: SITE_PARTS().map(p => p[0]).filter(k => !v["show_" + k]), showcase: { ...sc, on: !!v.sc_on } }); };
  view.innerHTML = `<form id="wlf" onsubmit="return false"><div class="grid g2">
    <div class="card"><h2>${esc(L("Announcement bar", "شريط الإعلان"))}</h2><p class="muted small">${esc(L("A short line at the top of the home page and/or the sign-in screens: an offer, news, a maintenance note. Visitors can close it; a new text shows again.", "سطر قصير أعلى الصفحة الرئيسية و/أو شاشات تسجيل الدخول: عرض أو خبر أو تنبيه صيانة. يمكن للزائر إغلاقه؛ ويظهر النص الجديد مرة أخرى."))}</p>
      ${form([{ k: "a_on", l: L("Show the announcement", "إظهار الإعلان"), type: "check", v: !!a.on, span: 1 },
        { k: "a_en", l: L("Text (English)", "النص (إنجليزي)"), v: a.en || "", span: 1 }, { k: "a_ar", l: L("Text (Arabic)", "النص (عربي)"), v: a.ar || "", span: 1, dir: "rtl" },
        { k: "a_link", l: L("Link (optional, https://…)", "الرابط (اختياري، https://…)"), v: a.link || "", dir: "ltr", span: 1 },
        { k: "a_link_en", l: L("Link text (English)", "نص الرابط (إنجليزي)"), v: a.link_en || "" }, { k: "a_link_ar", l: L("Link text (Arabic)", "نص الرابط (عربي)"), v: a.link_ar || "", dir: "rtl" },
        { k: "a_tone", l: L("Style", "النمط"), type: "select", v: a.tone || "info", opts: [["info", L("Calm (brand colour)", "هادئ (لون العلامة)")], ["ok", L("Good news (green)", "خبر سار (أخضر)")], ["warn", L("Attention (amber)", "تنبيه (كهرماني)")]] },
        { k: "a_where", l: L("Where", "أين"), type: "select", v: a.where || "home", opts: [["home", L("Home page", "الصفحة الرئيسية")], ["signin", L("Sign-in screens", "شاشات تسجيل الدخول")], ["both", L("Both", "كلاهما")]] }])}
      <b class="small muted">${esc(L("Preview", "معاينة"))}</b><div id="annprev">${bar({ a_en: a.en, a_ar: a.ar, a_link: a.link, a_link_en: a.link_en, a_link_ar: a.link_ar, a_tone: a.tone })}</div></div>
    <div><div class="card"><h2>${esc(L("Home page parts", "أجزاء الصفحة الرئيسية"))}</h2><p class="muted small">${esc(L("The first scene (the name, its line and the button) always shows. Untick a part to leave it out; the scrolling story adjusts to the scenes left.", "يظهر المشهد الأول (الاسم وسطره والزر) دائماً. ألغِ تحديد جزء لإخفائه؛ ويتكيف التمرير مع المشاهد المتبقية."))}</p>
      <div class="grid g1" style="gap:6px">${SITE_PARTS().map(([k, l]) => `<label class="chk"><input type="checkbox" name="show_${k}" ${hide.includes(k) ? "" : "checked"}> ${esc(l)}</label>`).join("")}</div></div>
    <div class="card"><h2>${esc(L("Showcase", "العرض"))}</h2><p class="muted small">${esc(L("A block after the story with a picture or a clip of your choice (a product tour, a customer, an event). Its heading and text are in Texts → Home page.", "قسم بعد القصة بصورة أو مقطع تختاره (جولة في المنتج، عميل، فعالية). عنوانه ونصه في النصوص ← الصفحة الرئيسية."))}</p>
      ${sc.media ? `<div class="site-thumb">${siteMediaTag(sc.media, sc.kind)}</div>` : `<p class="muted small">${esc(L("No picture or clip yet:", "لا توجد صورة أو مقطع بعد:"))} <a href="#/texts?tab=media">${esc(L("Pictures & clips", "الصور والمقاطع"))}</a></p>`}
      ${form([{ k: "sc_on", l: L("Show the showcase on the home page", "إظهار العرض في الصفحة الرئيسية"), type: "check", v: !!sc.on, span: 1 }])}</div></div></div>
    <div class="row" style="margin-top:4px"><button class="btn pri" data-a="wlsave">${esc(L("Save", "حفظ"))}</button></div></form>`;
  afterHooks.push(v => { const f = $("#wlf", v); if (f) f.addEventListener("input", () => { $("#annprev", v).innerHTML = bar(readForm(f)); }); });
}

// the pictures and clips: each slot shows what it has, takes a new file, or goes back to the built-in look
const SITE_SLOTS = () => [
  ["logo", L("Logo", "الشعار"), L("In the home page's top bar and footer, and on the sign-in screens, instead of the BEYONDERP wordmark. A wide PNG or WebP with a transparent background works best.", "في الشريط العلوي للصفحة الرئيسية وتذييلها وفي شاشات تسجيل الدخول بدلاً من كلمة BEYONDERP. الأفضل PNG أو WebP عريض بخلفية شفافة."), false],
  ["hero", L("Home page background", "خلفية الصفحة الرئيسية"), L("Behind the first scene: a picture, or a short silent clip that loops (MP4 or WebM, a few seconds).", "خلف المشهد الأول: صورة، أو مقطع قصير صامت يتكرر (MP4 أو WebM، بضع ثوانٍ)."), true],
  ["signin", L("Sign-in background", "خلفية تسجيل الدخول"), L("Behind the sign-in and getting-started screens.", "خلف شاشات تسجيل الدخول والبدء."), false],
  ["showcase", L("Showcase", "العرض"), L("The showcase block's picture or clip (switch it on in Layout & announcement).", "صورة قسم العرض أو مقطعه (شغّله من التخطيط والإعلان)."), true]];
const slotGet = (site, k) => k === "showcase" ? [(site.showcase || {}).media, (site.showcase || {}).kind] : [(site.media || {})[k], k === "hero" ? (site.media || {}).hero_kind : "image"];
function slotSet(site, k, path, kind) {
  const next = JSON.parse(JSON.stringify(site || {}));
  if (k === "showcase") { next.showcase = { ...(next.showcase || {}), media: path, kind: kind || "image" }; if (!path) next.showcase.on = false; }
  else { next.media = { ...(next.media || {}), [k]: path }; if (k === "hero") next.media.hero_kind = kind || "image"; }
  return next;
}
const SITE_IMAGES = ["image/png", "image/jpeg", "image/webp", "image/gif"], SITE_CLIPS = ["video/mp4", "video/webm"];
async function websiteMedia(view) {
  const site = await siteConfig();
  const used = (st, p) => SITE_SLOTS().some(([k]) => slotGet(st, k)[0] === p);
  const drop = async p => req("DELETE", `/storage/v1/object/site-media/${String(p).split("/").map(encodeURIComponent).join("/")}`, undefined, await token()).catch(() => null);
  Object.assign(S.H, {
    wmup: async el => {
      const f = el.files && el.files[0], k = el.dataset.slot, slot = SITE_SLOTS().find(x => x[0] === k); if (!f || !slot) return;
      const clip = SITE_CLIPS.includes(f.type);
      if (!SITE_IMAGES.includes(f.type) && !clip) throw new Error(L("Use a PNG, JPEG, WebP or GIF picture, or an MP4 or WebM clip.", "استخدم صورة PNG أو JPEG أو WebP أو GIF، أو مقطع MP4 أو WebM."));
      if (clip && !slot[3]) throw new Error(L("This place takes a picture, not a clip.", "هذا المكان يأخذ صورة وليس مقطعاً."));
      if (f.size > (clip ? 30 : 8) * 1048576) throw new Error(clip ? L("Clips up to 30 MB — a few seconds is enough for a background.", "المقاطع حتى 30 ميجابايت — تكفي بضع ثوانٍ للخلفية.") : L("Pictures up to 8 MB.", "الصور حتى 8 ميجابايت."));
      toast(L("Uploading…", "جارٍ الرفع…"));
      const old = slotGet(site, k)[0], up = await upload("site-media", f, "site"), next = slotSet(site, k, up.path, clip ? "video" : "image");
      await rpc("site_config_save", { p: next });
      if (old && !used(next, old)) await drop(old);
      toast(L("Saved. Visitors see it the next time a page opens.", "حُفظ. يراه الزوار عند فتح الصفحة التالية.")); route(); },
    wmdel: async el => { const k = el.dataset.slot, old = slotGet(site, k)[0];
      if (!await confirmIt(L("Remove this and go back to the built-in look?", "إزالة هذا والعودة إلى الشكل الأصلي؟"))) return;
      const next = slotSet(site, k, "", "image"); await rpc("site_config_save", { p: next });
      if (old && !used(next, old)) await drop(old);
      toast(L("Back to the built-in look.", "عاد إلى الشكل الأصلي.")); route(); } });
  view.innerHTML = `<div class="grid g2">${SITE_SLOTS().map(([k, l, help, clips]) => { const [p, kind] = slotGet(site, k);
    return `<div class="card"><h2>${esc(l)}</h2><p class="muted small">${esc(help)}</p>
      <div class="site-thumb ${k === "logo" ? "logo" : ""}">${p ? siteMediaTag(p, kind) : `<span class="muted small">${esc(L("Built-in look", "الشكل الأصلي"))}</span>`}</div>
      <div class="row" style="margin-top:10px"><label class="btn sm pri">${esc(p ? L("Replace", "استبدال") : L("Upload", "رفع"))}<input type="file" hidden data-change="wmup" data-slot="${k}" accept="${[...SITE_IMAGES, ...(clips ? SITE_CLIPS : [])].join(",")}"></label>
        ${p ? `<button type="button" class="btn sm" data-a="wmdel" data-slot="${k}">${esc(L("Remove", "إزالة"))}</button>` : ""}
        <span class="muted small">${esc(clips ? L("Picture up to 8 MB, or clip up to 30 MB", "صورة حتى 8 ميجابايت أو مقطع حتى 30 ميجابايت") : L("Picture up to 8 MB", "صورة حتى 8 ميجابايت"))}</span></div></div>`; }).join("")}</div>`;
}

// the footer's links (and the email shown there)
async function websiteLinks(view) {
  const [site, cur] = await Promise.all([siteConfig(), rpc("site_texts_admin")]);
  let rows = (Array.isArray(site.links) && site.links.length ? site.links : SITE_BUILTIN_LINKS).map(x => ({ en: x.en || "", ar: x.ar || "", url: x.url || "" }));
  const email = (cur["foot.email"] || {}).en || "";
  const read = () => $$("#wkf tr[data-i]").map(tr => ({ en: $("[name=en]", tr).value.trim(), ar: $("[name=ar]", tr).value.trim(), url: $("[name=url]", tr).value.trim() }));
  const draw = () => { $("#wkrows").innerHTML = rows.map((r, i) => `<tr data-i="${i}"><td><input name="en" value="${esc(r.en)}" maxlength="40" placeholder="LinkedIn"></td>
      <td><input name="ar" value="${esc(r.ar)}" maxlength="40" dir="rtl" placeholder="${esc(L("optional", "اختياري"))}"></td><td><input name="url" value="${esc(r.url)}" dir="ltr" placeholder="https://"></td>
      <td class="r nowrap"><button type="button" class="btn sm" data-a="wkup" data-i="${i}" title="${esc(L("Move earlier", "تقديم"))}">↑</button><button type="button" class="btn sm" data-a="wkdown" data-i="${i}" title="${esc(L("Move later", "تأخير"))}">↓</button><button type="button" class="btn sm" data-a="wkdel" data-i="${i}" title="${esc(L("Remove", "إزالة"))}">✕</button></td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">${esc(L("No links: the built-in ones (LinkedIn, Facebook, Instagram) show.", "لا روابط: تظهر الروابط الأصلية (LinkedIn وFacebook وInstagram)."))}</td></tr>`; };
  Object.assign(S.H, {
    wkadd: () => { rows = read(); if (rows.length >= 12) return toast(L("Up to 12 links.", "حتى 12 رابطاً."), true); rows.push({ en: "", ar: "", url: "" }); draw(); },
    wkdel: el => { rows = read(); rows.splice(+el.dataset.i, 1); draw(); },
    wkup: el => { rows = read(); const i = +el.dataset.i; if (i > 0) [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; draw(); },
    wkdown: el => { rows = read(); const i = +el.dataset.i; if (i < rows.length - 1) [rows[i + 1], rows[i]] = [rows[i], rows[i + 1]]; draw(); },
    wksave: async () => { const list = read().filter(r => r.en || r.url);
      for (const r of list) { if (!r.en) throw new Error(L("Give every link a name.", "أعطِ كل رابط اسماً.")); if (!SITE_URL.test(r.url)) throw new Error(L(`The address of ${r.en} must start with https://`, `يجب أن يبدأ عنوان ${r.en} بـ https://`)); }
      const mail = $("#wkf [name=email]").value.trim();
      if (mail && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(mail)) throw new Error(L("Check the email address.", "تحقق من البريد الإلكتروني."));
      if (mail !== email) await rpc("site_texts_save", { p: { "foot.email": { en: mail, ar: mail } } });
      const same = JSON.stringify(list) === JSON.stringify(SITE_BUILTIN_LINKS.map(x => ({ en: x.en, ar: x.ar, url: x.url })));
      await siteConfigSave({ ...site, links: same ? [] : list }); } });
  view.innerHTML = `<form id="wkf" class="card" onsubmit="return false"><h2>${esc(L("Footer links", "روابط التذييل"))}</h2>
    <p class="muted small">${esc(L("The links at the bottom of the home page: your social pages, terms, privacy, a contact page. Each needs a name and an https:// address; the Arabic name is optional. To hide them all, untick them in Layout & announcement.", "الروابط أسفل الصفحة الرئيسية: صفحاتك الاجتماعية والشروط والخصوصية وصفحة التواصل. يحتاج كل رابط اسماً وعنوان https://؛ والاسم العربي اختياري. لإخفائها كلها ألغِ تحديدها في التخطيط والإعلان."))}</p>
    <div class="tbl"><table><thead><tr><th>${esc(L("Name", "الاسم"))}</th><th>${esc(L("Name (Arabic)", "الاسم (عربي)"))}</th><th>${esc(L("Address", "العنوان"))}</th><th></th></tr></thead><tbody id="wkrows"></tbody></table></div>
    <div class="row" style="margin-top:8px"><button type="button" class="btn sm" data-a="wkadd">＋ ${esc(L("Link", "رابط"))}</button></div>
    <label class="f" style="margin-top:14px;max-width:420px">${esc(L("Email shown in the footer (blank: the built-in one)", "البريد الظاهر في التذييل (فارغ: الأصلي)"))}<input name="email" dir="ltr" value="${esc(email)}" placeholder="info@mybeyonderp.com"></label>
    <div class="row" style="margin-top:12px"><button class="btn pri" data-a="wksave">${esc(L("Save", "حفظ"))}</button></div></form>`;
  afterHooks.push(() => draw());
}

PAGES.texts = async (view, parts, q) => {
  const tab = ["layout", "media", "links"].includes(q.tab) ? q.tab : "texts";
  await ({ texts: websiteTexts, layout: websiteLayout, media: websiteMedia, links: websiteLinks })[tab](view, q);
  view.insertAdjacentHTML("afterbegin", `<div class="row" style="gap:6px;margin-bottom:12px;flex-wrap:wrap">${WEB_TABS().map(([k, l]) => `<a class="btn sm ${k === tab ? "pri" : ""}" href="#/texts?tab=${k}">${esc(l)}</a>`).join("")}
    <span class="sp"></span><a class="btn sm" href="./#/discover" target="_blank" rel="noopener">${esc(L("Open the home page", "فتح الصفحة الرئيسية"))} ↗</a></div>`);
};

// ================================================================== support visits
PAGES.visits = async view => {
  const rows = await rpc("console_visits", { p_status: "" });
  if (!DT.visits) DT.visits = { f: { st: "open" }, q: "" };
  view.innerHTML = `<div class="card"><div id="vtbl"></div></div>`;
  afterHooks.push(v => table($("#vtbl", v), "visits", rows, { sort: "updated_at", dir: "desc", search: ["ref", "reason", "company_name", "email", "address", "technician"],
    onRow: x => { location.hash = "#/visit/" + x.id; }, rowCls: x => x.staff_unread ? "unread" : "",
    cols: [{ k: "ref", l: L("Visit", "الزيارة"), h: x => `<b>${esc(x.ref)}</b><span class="sub">${esc(day(x.created_at))}</span>` },
      { k: "company_name", l: L("Company", "الشركة"), h: x => `${esc(x.company_name || "")}<span class="sub" dir="ltr">${esc(x.email)}</span>` },
      { k: "reason", l: L("For", "السبب"), h: x => esc(String(x.reason).slice(0, 90)) },
      { k: "st", l: L("Status", "الحالة"), v: x => x.status, h: x => mapChip(VIS_ST(), x.status) }, { k: "pay", l: L("Payment", "الدفع"), v: x => x.pay_status, h: x => mapChip(PAY_ST(), x.pay_status) },
      { k: "price", l: L("Price", "السعر"), num: true, h: x => x.price != null ? money(x.price) : "—" },
      { k: "scheduled_at", l: L("Scheduled", "الموعد"), date: true, h: x => esc(x.scheduled_at ? dt(x.scheduled_at) : "—") }, { k: "technician", l: L("Technician", "الفني") },
      { k: "updated_at", l: L("Updated", "آخر تحديث"), date: true, h: x => esc(ago(x.updated_at)) }],
    filters: [{ k: "st", l: L("Status", "الحالة"), type: "select", opts: [["open", L("Still open", "مفتوحة")], ...Object.entries(VIS_ST()).map(([k, v]) => [k, v[0]])], test: (x, v) => v === "open" ? !["done", "cancelled", "rejected", "declined"].includes(x.status) : x.status === v },
      { k: "pay", l: L("Payment", "الدفع"), type: "select", opts: Object.entries(PAY_ST()).map(([k, v]) => [k, v[0]]), test: (x, v) => x.pay_status === v },
      { k: "from", l: L("Scheduled from", "الموعد من"), type: "from", col: "scheduled_at" }, { k: "to", l: L("Scheduled to", "الموعد حتى"), type: "to", col: "scheduled_at" }] }));
};
PAGES.visit = async (view, parts) => {
  const id = +parts[1], x = (await rpc("console_visits", { p_status: "" })).find(v => v.id === id);
  if (!x) throw new Error(L("Visit not found.", "الزيارة غير موجودة."));
  $("#ttl").textContent = `${x.ref} — ${x.company_name || ""}`; $("#crumb").textContent = L("Support visits", "زيارات الدعم");
  const set = async (p, tell = true) => { await rpc("console_visit_set", { p_visit: id, p }); if (tell) push("visit", id); toast(L("Saved.", "تم الحفظ.")); route(); };
  Object.assign(S.H, {
    vquote: () => panel(L("Price and proposed time", "السعر والموعد المقترح"), form([
      { k: "price", l: L(`Price (${x.currency || ""})`, `السعر (${x.currency || ""})`), type: "number", v: x.price ?? "", min: 0, req: 1 },
      { k: "pay_when", l: L("Payment", "الدفع"), type: "select", v: x.pay_when, opts: [["before", L("Before the visit", "قبل الزيارة")], ["after", L("After the visit", "بعد الزيارة")], ["free", L("No charge", "بدون رسوم")]] },
      { k: "note", l: L("Message to the customer (time proposed, what to prepare…)", "رسالة للعميل (الموعد المقترح، ما يجب تجهيزه…)"), type: "area", v: x.note || "", span: 1 }]),
      v => set({ status: "quoted", price: v.pay_when === "free" ? 0 : +v.price, pay_when: v.pay_when, note: v.note, pay_status: v.pay_when === "free" ? "waived" : x.pay_status })),
    vsched: () => panel(L("Schedule the visit", "جدولة الزيارة"), form([
      { k: "when", l: L("Date and time", "التاريخ والوقت"), type: "datetime-local", v: x.scheduled_at ? String(x.scheduled_at).slice(0, 16) : "", req: 1 },
      { k: "technician", l: L("Who goes", "من سيذهب"), v: x.technician || "" }, { k: "note", l: L("Message to the customer", "رسالة للعميل"), type: "area", v: x.note || "", span: 1 }]),
      v => set({ status: "scheduled", scheduled_at: new Date(v.when).toISOString(), technician: v.technician, note: v.note })),
    vpaid: async () => { if (await confirmIt(L("Confirm the payment was received?", "تأكيد استلام الدفعة؟"))) set({ pay_status: "paid" }); },
    vwaive: async () => { if (await confirmIt(L("No charge for this visit?", "بدون رسوم لهذه الزيارة؟"))) set({ pay_status: "waived" }); },
    vdone: async () => { if (await confirmIt(L("Mark the visit as done?", "تعليم الزيارة كمنتهية؟"))) set({ status: "done" }); },
    vreject: async () => { const why = await ask(L("Why can't it be done? The customer sees this.", "لماذا لا يمكن إجراؤها؟ يرى العميل هذا."), "", { area: 1 }); if (why) set({ status: "rejected", note: why }); },
    vnote: async () => { const n = await ask(L("Private note for the team", "ملاحظة خاصة للفريق"), x.staff_note || "", { area: 1 }); if (n !== null) set({ staff_note: n }, false); } });
  const appr = can("approve");
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><a class="btn" href="#/visits">← ${esc(L("Support visits", "زيارات الدعم"))}</a>${mapChip(VIS_ST(), x.status)}${mapChip(PAY_ST(), x.pay_status)}</div>
    <div class="split"><div class="card"><h2>${esc(x.ref)}</h2><p style="white-space:pre-wrap">${esc(x.reason)}</p>${fileList("support-files", x.files)}
      <div class="small"><b>${esc(L("Address", "العنوان"))}</b>: ${esc(x.address || "—")}<br><b>${esc(L("Phone", "الهاتف"))}</b>: <a href="tel:${esc(x.phone)}" dir="ltr">${esc(x.phone)}</a><br>
        <b>${esc(L("Suits them", "يناسبهم"))}</b>: ${esc(x.preferred || "—")}<br><b>${esc(L("Asked by", "طلبها"))}</b>: <span dir="ltr">${esc(x.email)}</span> · ${x.company_id ? `<a href="#/company/${x.company_id}">${esc(x.company_name || "")}</a>` : esc(x.company_name || "")}
        ${x.ticket_id ? `<br><a href="#/ticket/${x.ticket_id}">${esc(L("From a support ticket", "من بلاغ دعم"))}</a>` : ""}</div>
      ${x.note ? `<div class="banner" style="margin-top:12px"><b>${esc(L("Told the customer", "أُبلغ العميل"))}</b>: ${esc(x.note)}</div>` : ""}
      ${x.staff_note ? `<div class="banner warn"><b>${esc(L("Team note", "ملاحظة الفريق"))}</b>: ${esc(x.staff_note)}</div>` : ""}</div>
    <div><div class="card"><h3>${esc(L("Next step", "الخطوة التالية"))}</h3><div class="kpis" style="grid-template-columns:repeat(2,minmax(0,1fr))">${kpi(L("Price", "السعر"), x.price != null ? money(x.price) : "—", "", esc(x.currency || ""))}${kpi(L("Scheduled", "الموعد"), esc(x.scheduled_at ? dt(x.scheduled_at) : "—"), "", esc(x.technician || ""))}</div>
      <div class="grid" style="gap:8px"><button class="btn ${x.status === "requested" ? "pri" : ""}" data-a="vquote">${esc(L("Set price / propose time…", "تحديد السعر / اقتراح موعد…"))}</button>
        <button class="btn ${x.status === "accepted" ? "pri" : ""}" data-a="vsched">${esc(L("Schedule…", "جدولة…"))}</button>
        ${appr && x.pay_status !== "paid" ? `<button class="btn ${x.pay_status === "submitted" ? "pri" : ""}" data-a="vpaid">✓ ${esc(L("Payment received", "تم استلام الدفعة"))}</button><button class="btn" data-a="vwaive">${esc(L("No charge", "بدون رسوم"))}</button>` : ""}
        ${x.status === "scheduled" ? `<button class="btn pri" data-a="vdone">✓ ${esc(L("Mark done", "تمت"))}</button>` : ""}
        <button class="btn" data-a="vnote">${esc(L("Team note…", "ملاحظة للفريق…"))}</button>
        ${!["done", "cancelled", "rejected", "declined"].includes(x.status) ? `<button class="btn danger" data-a="vreject">${esc(L("Not possible…", "غير ممكنة…"))}</button>` : ""}</div></div>
      ${x.pay_status !== "none" && x.method_name ? `<div class="card"><h3>${esc(L("Payment sent", "الدفعة المرسلة"))}</h3><div class="small">${esc(x.method_name)}${x.proof_ref ? ` · <b dir="ltr">${esc(x.proof_ref)}</b>` : ""}${x.proof_amount != null ? ` · ${money(x.proof_amount)}` : ""}</div>${fileList("billing-proofs", x.proof)}</div>` : ""}</div></div>`;
};
function visitPanel(base) {
  toast(L("Visits are requested by the customer from Help and support in the ERP; open the ticket's company to follow up.", "يطلب العميل الزيارة من «المساعدة والدعم» في النظام؛ افتح شركة البلاغ للمتابعة."));
  if (base.company_id) location.hash = "#/company/" + base.company_id;
}

// ================================================================== cancellations and churn
PAGES.cancellations = async view => {
  const all = await rpc("console_companies");
  const rows = all.filter(x => x.cancel_note || ["expired", "past_due", "unpaid", "suspended"].includes(x.state.status));
  const reason = x => x.cancel_note ? "cancel" : x.state.status;
  const R = { cancel: L("Asked not to renew", "طلب عدم التجديد"), expired: L("Ended without paying", "انتهت دون دفع"), past_due: L("Payment due (grace)", "دفعة مستحقة (سماح)"), unpaid: L("Never subscribed", "لم يشترك"), suspended: L("Suspended", "موقوفة") };
  view.innerHTML = `<div class="kpis">${Object.entries(R).map(([k, l]) => kpi(l, num(rows.filter(x => reason(x) === k).length), k === "cancel" || k === "expired" ? "warn" : "")).join("")}</div><div class="card"><div id="xtbl"></div></div>`;
  afterHooks.push(v => table($("#xtbl", v), "churn", rows, { sort: "ends", dir: "asc", search: ["name", "owner", "cancel_note"], onRow: x => { location.hash = "#/company/" + x.id; },
    cols: [{ k: "name", l: L("Company", "الشركة"), h: x => `<b>${esc(x.name)}</b>` }, { k: "owner", l: L("Owner", "المالك"), v: x => x.created_by },
      { k: "why", l: L("Why", "السبب"), v: x => R[reason(x)], h: x => stChip(x.state.status) + `<span class="sub">${esc(R[reason(x)])}</span>` },
      { k: "cancel_note", l: L("Reason given", "السبب المذكور") }, { k: "cancel_at", l: L("Asked on", "تاريخ الطلب"), date: true, h: x => esc(x.cancel_at ? day(x.cancel_at) : "—") },
      { k: "ends", l: L("Ends / ended", "ينتهي / انتهى"), date: true, v: x => x.state.ends ? String(x.state.ends).slice(0, 10) : "" },
      { k: "paid_total", l: L("Paid in total", "إجمالي المدفوع"), num: true, h: x => money(x.paid_total) }, { k: "last_change", l: L("Last activity", "آخر نشاط"), date: true, h: x => esc(ago(x.last_change)) }],
    filters: [{ k: "why", l: L("Why", "السبب"), type: "select", opts: Object.entries(R), test: (x, v) => reason(x) === v },
      { k: "from", l: L("Ends from", "ينتهي من"), type: "from", col: "ends" }, { k: "to", l: L("Ends to", "ينتهي حتى"), type: "to", col: "ends" }] }));
};

// ================================================================== announcements
PAGES.notices = async view => {
  const rows = await rpc("console_notices");
  const LV = { info: L("Information", "معلومة"), warn: L("Important", "مهم"), critical: L("Urgent", "عاجل") };
  const AU = { all: L("Everyone", "الجميع"), trial: L("Companies on trial", "شركات في التجربة"), paying: L("Paying companies", "الشركات المدفوعة"), unpaid: L("Companies not paying", "شركات غير مدفوعة") };
  const edit = n => { n = n || { level: "info", audience: "all", active: true };
    panel(n.id ? n.title : L("New announcement", "إعلان جديد"), form([
      { k: "title", l: L("Title", "العنوان"), v: n.title, req: 1 }, { k: "title_ar", l: L("Title (Arabic)", "العنوان (عربي)"), v: n.title_ar || "" },
      { k: "body", l: L("Text", "النص"), type: "area", v: n.body || "", span: 1 }, { k: "body_ar", l: L("Text (Arabic)", "النص (عربي)"), type: "area", v: n.body_ar || "", span: 1 },
      { k: "level", l: L("Kind", "النوع"), type: "select", v: n.level, opts: Object.entries(LV) }, { k: "audience", l: L("Shown to", "يظهر لـ"), type: "select", v: n.audience, opts: Object.entries(AU) },
      { k: "starts_at", l: L("From", "من"), type: "date", v: String(n.starts_at || today()).slice(0, 10) }, { k: "ends_at", l: L("Until (blank: until switched off)", "حتى (فارغ: حتى الإيقاف)"), type: "date", v: String(n.ends_at || "").slice(0, 10) },
      { k: "active", l: L("Shown", "ظاهر"), type: "check", v: n.active }]),
      async v => { await rpc("console_notice_save", { p: { ...v, id: n.id || "", starts_at: v.starts_at ? v.starts_at + "T00:00:00Z" : "", ends_at: v.ends_at ? v.ends_at + "T23:59:59Z" : "" } }); toast(L("Saved.", "تم الحفظ.")); },
      { extra: (n.id ? `<button type="button" class="btn danger" data-a="ndel">${esc(L("Delete", "حذف"))}</button>` : "") + `<button type="button" class="btn" data-a="nmail">✉ ${esc(L("Send by email…", "إرسال بالبريد…"))}</button>` });
    S.H.nmail = () => { const v = readForm($("#panel")); mailPanel({ subject: v.title, subject_ar: v.title_ar, body: v.body, body_ar: v.body_ar, status: v.audience, notice_id: n.id || "" }); };
    S.H.ndel = async () => { if (!await confirmIt(L("Delete this announcement?", "حذف هذا الإعلان؟"), { danger: true })) return; await rpc("console_notice_save", { p: { id: n.id, title: n.title, delete: true } }); closePanel(); route(); }; };
  S.H.nnew = () => edit();
  S.H.mnew = () => mailPanel({});
  const [mails, ms] = await Promise.all([rpc("console_mailings").catch(() => []), mailStatus()]);
  view.innerHTML = `<div class="row" style="margin-bottom:12px;gap:8px;flex-wrap:wrap"><button class="btn pri" data-a="nnew">+ ${esc(L("Announcement", "إعلان"))}</button><button class="btn" data-a="mnew">✉ ${esc(L("Email customers…", "مراسلة العملاء بالبريد…"))}</button><span class="muted small">${esc(L("Announcements show as a bar at the top of the ERP for the companies chosen; each person can hide it. Emails go to each person's inbox, only when you send them.", "تظهر الإعلانات شريطاً أعلى النظام للشركات المختارة ويمكن لكل شخص إخفاؤها. ويصل البريد لصندوق كل شخص عند إرسالك فقط."))}</span></div>
    <div class="card"><div id="ntbl"></div></div>
    <div class="card"><h2>✉ ${esc(L("Emails sent", "الرسائل المرسلة"))}</h2>${mailReadyNote(ms)}<div id="mtbl"></div></div>`;
  afterHooks.push(v => table($("#mtbl", v), "mailings", mails, { sort: "created_at", dir: "desc", search: ["subject", "created_by"], onRow: mailDetail,
    cols: [{ k: "subject", l: L("Subject", "الموضوع"), h: m => `<b>${esc(m.subject)}</b><span class="sub">${esc(mailWho(m.audience))}</span>` },
      { k: "sent", l: L("Sent", "أُرسل"), num: true, h: m => `${num(m.sent)} / ${num(m.total)}` }, { k: "failed", l: L("Failed", "فشل"), num: true, h: m => m.failed ? chip(num(m.failed), "bad") : "0" },
      { k: "status", l: L("Status", "الحالة"), h: m => mapChip(MAIL_ST(), m.status) }, { k: "created_at", l: L("When", "متى"), date: true, h: m => `${esc(dt(m.created_at))}<span class="sub">${esc(m.created_by || "")}</span>` }] }));
  afterHooks.push(v => table($("#ntbl", v), "notices", rows, { sort: "starts_at", dir: "desc", search: ["title", "body"], onRow: edit, rowCls: n => n.active ? "" : "dim",
    cols: [{ k: "title", l: L("Title", "العنوان"), h: n => `<b>${esc(n.title)}</b><span class="sub">${esc(LV[n.level])}</span>` }, { k: "audience", l: L("Shown to", "يظهر لـ"), v: n => AU[n.audience] },
      { k: "starts_at", l: L("From", "من"), date: true, h: n => esc(day(n.starts_at)) }, { k: "ends_at", l: L("Until", "حتى"), date: true, h: n => esc(day(n.ends_at)) },
      { k: "active", l: L("Status", "الحالة"), v: n => n.active ? 1 : 0, h: n => n.active ? chip(L("Shown", "ظاهر"), "ok") : chip(L("Off", "معطل")) }],
    filters: [{ k: "on", l: L("Status", "الحالة"), type: "select", opts: [["1", L("Shown", "ظاهر")], ["0", L("Off", "معطل")]], test: (n, v) => String(+!!n.active) === v }] }));
};

// ================================================================== announcement emails (sent by the "mail" function, one email per person)
const MAIL_ST = () => ({ sending: [L("Sending", "جارٍ الإرسال"), "info"], stopped: [L("Stopped", "متوقف"), "warn"], done: [L("Sent", "أُرسل"), "ok"] });
const MAIL_AU = () => ({ all: L("Everyone", "الجميع"), trial: L("Companies on trial", "شركات في التجربة"), paying: L("Paying companies", "الشركات المدفوعة"), unpaid: L("Companies not paying", "شركات غير مدفوعة") });
const mailWho = a => { a = a || {}; return `${MAIL_AU()[a.status || "all"]} · ${a.who === "admins" ? L("administrators", "المسؤولون") : L("everyone in them", "كل من فيها")}${(a.plans || []).length ? " · " + a.plans.join(", ") : ""}`; };
const mailStatus = () => fnCall("mail", { action: "status" }).catch(e => ({ ready: false, error: e.message }));
const mailReadyNote = ms => ms.ready ? `<p class="muted small">${esc(L("Sent from", "تُرسل من"))} <span dir="ltr">${esc(ms.from)}</span></p>`
  : `<div class="banner warn small">${esc(L("Email sending is not set up yet: the owner adds an email service's key to the workspace (see docs/EMAIL_SETUP.md). Until then you can prepare emails but not send them.", "لم يُضبط إرسال البريد بعد: يضيف المالك مفتاح خدمة البريد لمساحة العمل (انظر docs/EMAIL_SETUP.md). حتى ذلك الحين يمكنك تجهيز الرسائل دون إرسالها."))}</div>`;
// keep sending while this console is open; the progress shows at the bottom of the screen
async function mailRun(id) {
  if (S.mailing === id) return; S.mailing = id;
  let bar = $("#mailbar"); if (!bar) { bar = document.createElement("div"); bar.id = "mailbar"; bar.className = "mailbar"; document.body.appendChild(bar); }
  const show = r => { bar.innerHTML = `✉ ${esc(L(`Sending… ${num(r.done || 0)} of ${num(r.total || 0)} sent`, `جارٍ الإرسال… أُرسل ${num(r.done || 0)} من ${num(r.total || 0)}`))}${r.failures ? ` · ${esc(L(`${r.failures} failed`, `فشل ${r.failures}`))}` : ""}`; };
  show({});
  try {
    for (let i = 0; i < 500; i++) {
      const r = await fnCall("mail", { action: "send", mailing: id }); show(r);
      if (!r.left || r.status !== "sending") { toast(r.status === "done" ? L(`Sent to ${num(r.done)} people${r.failures ? `; ${r.failures} failed` : ""}.`, `أُرسل إلى ${num(r.done)} شخص${r.failures ? `؛ فشل ${r.failures}` : ""}.`) : L("Sending stopped.", "توقف الإرسال."), !!r.failures); break; }
    }
  } catch (e) { toast(e.message, true); } finally { S.mailing = null; bar.remove(); if (parse().parts[0] === "notices") route(); }
}
async function mailPanel(m) {
  const ms = await mailStatus();
  const plans = await rpc("billing_admin_overview").then(o => o.config.plans || []).catch(() => []);
  const aud = () => { const v = readForm($("#panel")); return { status: v.status, who: v.who, plans: plans.filter(p => v["pl_" + p.code]).map(p => p.code) }; };
  const count = async () => { const el = $("#mcount"); if (!el) return; try { const r = await rpc("console_mail_audience", { p: aud() });
    el.innerHTML = `👥 ${esc(L(`Goes to ${num(r.people)} people in ${num(r.companies)} companies.`, `يصل إلى ${num(r.people)} شخص في ${num(r.companies)} شركة.`))}`; el.dataset.n = r.people; } catch (e) { el.textContent = e.message; } };
  panel(L("Email customers", "مراسلة العملاء بالبريد"), mailReadyNote(ms) + form([
    { k: "subject", l: L("Subject", "الموضوع"), v: m.subject || "", req: 1 }, { k: "subject_ar", l: L("Subject (Arabic, optional)", "الموضوع (عربي، اختياري)"), v: m.subject_ar || "", dir: "rtl" },
    { k: "body", l: L("Message", "الرسالة"), type: "area", rows: 7, v: m.body || "", req: 1 }, { k: "body_ar", l: L("Message (Arabic, optional — sent below the English)", "الرسالة (عربي، اختياري — تُرسل أسفل الإنجليزية)"), type: "area", rows: 7, v: m.body_ar || "", dir: "rtl" },
    { k: "status", l: L("Companies", "الشركات"), type: "select", v: m.status || "all", opts: Object.entries(MAIL_AU()) },
    { k: "who", l: L("People in them", "الأشخاص فيها"), type: "select", v: "everyone", opts: [["everyone", L("Everyone", "الجميع")], ["admins", L("Administrators only", "المسؤولون فقط")]] },
    ...(plans.length ? [{ type: "html", span: 1, html: `<div class="small"><b>${esc(L("Only these plans (none ticked: all plans)", "هذه الباقات فقط (بدون تحديد: كل الباقات)"))}</b><div class="row" style="gap:10px;flex-wrap:wrap;margin-top:4px">${plans.map(p => `<label class="chk"><input type="checkbox" name="pl_${esc(p.code)}"> ${esc((S.lang === "ar" && p.name_ar) || p.name)}</label>`).join("")}</div></div>` }] : []),
    { type: "html", span: 1, html: `<div class="banner info small" id="mcount">…</div><p class="muted small" style="margin:6px 0 0">${esc(L("Each person gets their own email; no one sees the others. Replies come to the company inbox. Use it for service news (maintenance, changes), not advertising.", "يحصل كل شخص على رسالته الخاصة ولا يرى أحد الآخرين. تصل الردود إلى بريد الشركة. استخدمه لأخبار الخدمة (الصيانة والتغييرات)، لا للإعلانات."))}</p>` }], 2),
    async v => { const n = +($("#mcount").dataset.n || 0); if (!n) throw new Error(L("No one matches — choose other people.", "لا أحد مطابق — اختر أشخاصاً آخرين."));
      if (!ms.ready) throw new Error(L("Email sending is not set up yet.", "لم يُضبط إرسال البريد بعد."));
      if (!await confirmIt(L(`Send “${v.subject}” to ${num(n)} people now?`, `إرسال «${v.subject}» إلى ${num(n)} شخص الآن؟`))) return false;
      const r = await rpc("console_mail_create", { p: { subject: v.subject, subject_ar: v.subject_ar, body: v.body, body_ar: v.body_ar, audience: aud(), notice_id: m.notice_id || "" } });
      closePanel(); mailRun(r.id); route(); return false; },
    { wide: true, saveLabel: L("Send…", "إرسال…"), stay: true, extra: `<button type="button" class="btn" data-a="mtest">🧪 ${esc(L("Send me a test", "أرسل لي تجربة"))}</button>`,
      onOpen: p => { count(); p.addEventListener("change", e => { if (/^(status|who|pl_)/.test(e.target.name || "")) count(); }); } });
  S.H.mtest = async el => { const v = readForm($("#panel")); if (!v.subject.trim() || !v.body.trim()) return toast(L("Write the subject and the message first.", "اكتب الموضوع والرسالة أولاً."), true);
    el.disabled = true; try { const r = await fnCall("mail", { action: "test", subject: v.subject, subject_ar: v.subject_ar, body: v.body, body_ar: v.body_ar }); toast(L(`Test sent to ${r.sent}.`, `أُرسلت التجربة إلى ${r.sent}.`)); } catch (e) { toast(e.message, true); } finally { el.disabled = false; } };
}
function mailDetail(m) {
  const left = m.total - m.sent - m.failed;
  panel(m.subject, `<p>${mapChip(MAIL_ST(), m.status)} ${esc(mailWho(m.audience))}</p>
    <p class="small">${esc(L(`${num(m.sent)} of ${num(m.total)} sent · ${num(m.failed)} failed · ${num(left)} waiting`, `أُرسل ${num(m.sent)} من ${num(m.total)} · فشل ${num(m.failed)} · بانتظار ${num(left)}`))}<br>${esc(L(`By ${m.created_by || ""}, ${dt(m.created_at)}`, `بواسطة ${m.created_by || ""}، ${dt(m.created_at)}`))}</p>
    ${m.subject_ar ? `<p dir="rtl"><b>${esc(m.subject_ar)}</b></p>` : ""}<div class="card small" style="white-space:pre-wrap" dir="auto">${esc(m.body)}</div>${m.body_ar ? `<div class="card small" style="white-space:pre-wrap" dir="rtl">${esc(m.body_ar)}</div>` : ""}
    ${(m.failures || []).length ? `<h3>${esc(L("Not delivered", "لم تُسلّم"))}</h3><div class="small">${m.failures.map(f => `<div dir="ltr"><b>${esc(f.email)}</b> — ${esc(f.error || "")}</div>`).join("")}</div>` : ""}`, null,
    { extra: [m.status === "sending" && left ? `<button type="button" class="btn pri" data-a="mgo">▶ ${esc(L("Continue sending", "متابعة الإرسال"))}</button>` : "",
      m.status === "sending" ? `<button type="button" class="btn" data-a="mstate" data-s="stop">⏸ ${esc(L("Stop", "إيقاف"))}</button>` : "",
      m.status === "stopped" ? `<button type="button" class="btn pri" data-a="mstate" data-s="resume">▶ ${esc(L("Resume", "استئناف"))}</button>` : "",
      m.failed ? `<button type="button" class="btn" data-a="mstate" data-s="retry">↻ ${esc(L("Retry the failed", "إعادة محاولة الفاشلة"))}</button>` : ""].join("") });
  S.H.mgo = () => { closePanel(); mailRun(m.id); };
  S.H.mstate = async el => { const r = await rpc("console_mail_state", { p_mailing: m.id, p_state: el.dataset.s }); closePanel(); if (r.status === "sending") mailRun(m.id); route(); };
}

// ================================================================== plans: what customers see, what each includes
const planCard = (p, cfg, tools = "") => {
  const ar = S.lang === "ar", feats = ar && (p.features_ar || []).length ? p.features_ar : (p.features || []);
  return `<div class="plan ${p.highlight ? "hl" : ""}" style="--pc:${esc(p.color || "var(--accent)")}">${p.badge || p.badge_ar ? `<span class="badge">${esc(ar && p.badge_ar ? p.badge_ar : p.badge)}</span>` : ""}
    <h3 style="margin:0">${esc(ar && p.name_ar ? p.name_ar : p.name)} ${p.hidden ? chip(L("Hidden", "مخفية")) : ""}${p.code === cfg.trial_plan ? chip(L("Trial plan", "باقة التجربة"), "info") : ""}</h3>
    ${p.tagline || p.tagline_ar ? `<div class="muted small">${esc(ar && p.tagline_ar ? p.tagline_ar : p.tagline)}</div>` : ""}
    <div class="price">${p.contact ? esc(L("Contact us", "تواصل معنا")) : `${money(p.price)} <small>${esc(cfg.currency)} / ${esc(L("month · per company", "شهر · لكل شركة"))}</small>`}</div>
    <div class="small">${esc(p.users ? L(`${p.users} users`, `${p.users} مستخدمين`) : L("users as agreed", "المستخدمون حسب الاتفاق"))}${p.companies ? ` · ${esc(L(`up to ${p.companies} compan${p.companies === 1 ? "y" : "ies"}`, `حتى ${p.companies} شركة`))}` : ""}${p.perms === false ? ` · ${esc(L("no user permissions", "بدون صلاحيات مستخدمين"))}` : ""}${p.notify_perms === false ? ` · ${esc(L("no notification permissions", "بدون صلاحيات إشعارات"))}` : ""}${(p.deny || []).length ? ` · ${esc(L(`${p.deny.length} screen(s) off`, `${p.deny.length} شاشة متوقفة`))}` : ""} · ${esc(p.modules && p.modules !== "all" ? L(`${p.modules.length} modules + core`, `${p.modules.length} أقسام + الأساسية`) : L("every module", "كل الأقسام"))}
      · ${esc(cfg.ai_mode === "free" ? L("AI free for all", "الذكاء الاصطناعي مجاني للجميع") : cfg.ai_mode === "off" ? L("AI off", "الذكاء الاصطناعي متوقف") : p.ai ? L("AI included", "يشمل الذكاء الاصطناعي") : L("no AI", "بدون ذكاء اصطناعي"))}
      · ${esc(cfg.support_mode === "free" || p.support !== false ? L("online support", "دعم إلكتروني") : L("no online support", "بدون دعم إلكتروني"))}</div>
    <ul>${feats.map(f => `<li>${esc(f)}</li>`).join("")}</ul>${tools ? `<div class="tools">${tools}</div>` : ""}</div>`;
};
PAGES.plans = async view => {
  const cfg = (await rpc("billing_admin_overview")).config, plans = cfg.plans.map(p => ({ ...p }));
  const save = async (list, msg) => { await rpc("billing_set_config", { p: { plans: list } }); toast(msg || L("Saved.", "تم الحفظ.")); route(); };
  const edit = i => {
    const p = i == null ? { code: "", name: "", price: 0, users: 3, features: [], modules: "all", ai: false, support: true, color: "" } : plans[i];
    const mods = Array.isArray(p.modules) ? p.modules : [];
    panel(i == null ? L("New plan", "باقة جديدة") : p.name, `<div class="split" style="grid-template-columns:minmax(0,1.3fr) minmax(0,1fr)"><div>${form([
      { k: "code", l: L("Code (letters, never changes once used)", "الكود (حروف، لا يتغير بعد الاستخدام)"), v: p.code, req: 1, dir: "ltr", dis: i != null },
      { k: "price", l: L(`Price per company per month (${cfg.currency})`, `السعر لكل شركة شهرياً (${cfg.currency})`), type: "number", v: p.price, min: 0, req: 1 },
      { k: "name", l: L("Name", "الاسم"), v: p.name, req: 1 }, { k: "name_ar", l: L("Name (Arabic)", "الاسم (عربي)"), v: p.name_ar || "" },
      { k: "tagline", l: L("One line under the name", "سطر تحت الاسم"), v: p.tagline || "" }, { k: "tagline_ar", l: L("One line (Arabic)", "سطر (عربي)"), v: p.tagline_ar || "" },
      { k: "features", l: L("What it says (one line each)", "المزايا المعروضة (سطر لكل ميزة)"), type: "area", rows: 5, v: (p.features || []).join("\n") },
      { k: "features_ar", l: L("What it says (Arabic)", "المزايا (عربي)"), type: "area", rows: 5, v: (p.features_ar || []).join("\n") },
      { k: "badge", l: L("Badge (e.g. Most popular)", "شارة (مثل الأكثر طلباً)"), v: p.badge || "" }, { k: "badge_ar", l: L("Badge (Arabic)", "الشارة (عربي)"), v: p.badge_ar || "" },
      { k: "color", l: L("Colour", "اللون"), type: "color", v: p.color || "#4f46e5" }, { k: "users", l: L("Users per company (a hard limit; blank on a “contact us” plan: set per deal)", "المستخدمون لكل شركة (حد لا يُتجاوز؛ فارغ في باقة «تواصل معنا»: يُحدد لكل اتفاق)"), type: "number", v: p.users ?? "", min: 1 },
      { k: "contact", l: L("Price on request: customers contact you (Enterprise); it cannot be ordered in the app", "السعر عند الطلب: يتواصل العميل معك (المؤسسات)؛ لا يمكن طلبها من التطبيق"), type: "check", v: p.contact, span: 1 },
      { k: "highlight", l: L("Stand out (outlined)", "مميزة (بإطار)"), type: "check", v: p.highlight }, { k: "hidden", l: L("Hidden: not offered, you can still give it", "مخفية: لا تُعرض، ويمكنك منحها"), type: "check", v: p.hidden },
      { k: "companies", l: L("Companies a customer may own on this plan (blank: the general rule in Rules & switches)", "الشركات التي يملكها العميل في هذه الباقة (فارغ: القاعدة العامة في القواعد والمفاتيح)"), type: "number", v: p.companies ?? "", min: 1,
        help: L("A person's limit is the most generous plan among the companies they own; a number set for one person in People beats it.", "حد الشخص هو الأسخى بين باقات شركاته؛ والرقم المحدد لشخص في الأشخاص يتقدم عليه.") },
      { k: "ai", l: L("Includes the AI assistant", "تشمل المساعد الذكي"), type: "check", v: p.ai }, { k: "support", l: L("Includes online support", "تشمل الدعم الإلكتروني"), type: "check", v: p.support !== false },
      { k: "perms", l: L("User permissions: the company chooses which menus, screens and tabs each user sees", "صلاحيات المستخدمين: تختار الشركة القوائم والشاشات والتبويبات لكل مستخدم"), type: "check", v: p.perms !== false, span: 1,
        help: L("Off: every user has full use of what the plan includes; permissions set before stay as they were and cannot be changed.", "عند الإيقاف: لكل مستخدم كامل ما تشمله الباقة؛ وتبقى الصلاحيات السابقة كما هي ولا يمكن تغييرها.") },
      { k: "notify_perms", l: L("Notification permissions: the company chooses who receives which notifications", "صلاحيات الإشعارات: تختار الشركة من يتلقى كل إشعار"), type: "check", v: p.notify_perms !== false, span: 1,
        help: L("Off: everyone receives the standard notifications for their department.", "عند الإيقاف: يتلقى الجميع الإشعارات المعتادة لقسمهم.") },
      { k: "support_rank", l: L("Support priority (higher is answered first)", "أولوية الدعم (الأعلى يُرد عليه أولاً)"), type: "number", v: p.support_rank ?? 0, min: 0, max: 99,
        help: L("The support queue puts tickets of higher-priority plans first.", "تضع قائمة الدعم بلاغات الباقات الأعلى أولوية في المقدمة.") },
      { k: "reply_hours", l: L("First reply within (hours, optional)", "أول رد خلال (ساعات، اختياري)"), type: "number", v: p.reply_hours ?? "", min: 0,
        help: L("Shown to the team as a due time; blank: no target.", "يظهر للفريق كموعد مستحق؛ فارغ: بلا هدف.") },
      { k: "mods", l: L("Modules", "الأقسام"), type: "select", v: p.modules === "all" || !p.modules ? "all" : "list", opts: [["all", L("Every module", "كل الأقسام")], ["list", L("Core modules plus these:", "الأقسام الأساسية بالإضافة إلى:")]], span: 1 },
      { type: "html", span: 1, html: `<div class="checks">${SECTIONS().filter(([k]) => !(cfg.core_modules || []).includes(k)).map(([k, l]) => `<label class="chk"><input type="checkbox" name="s_${k}" ${mods.includes(k) ? "checked" : ""}> ${esc(l)}</label>`).join("")}</div>
        <p class="muted small">${esc(L("Core modules and each industry's starter modules are always included (Menus by industry).", "الأقسام الأساسية والأقسام المبدئية لكل نشاط مشمولة دائماً (القوائم حسب النشاط)."))}</p>` },
      { type: "html", span: 1, html: `<b class="small">${esc(L("Switch off screens or tabs on this plan (tick to switch off)", "إيقاف شاشات أو تبويبات في هذه الباقة (حدد للإيقاف)"))}</b>${treeHtml(Object.fromEntries((p.deny || []).map(k => [k, "off"])), "off")}` }])}</div>
      <div><b class="small muted">${esc(L("Preview", "معاينة"))}</b><div class="preview" id="pprev"></div></div></div>`,
      async v => { const np = { ...p, code: (i == null ? v.code : p.code).trim().toLowerCase().replace(/[^a-z0-9_-]/g, ""), name: v.name.trim(), name_ar: v.name_ar.trim(), tagline: v.tagline.trim(), tagline_ar: v.tagline_ar.trim(),
          price: +v.price, users: v.users === "" ? null : +v.users, companies: v.companies === "" ? null : Math.round(+v.companies), perms: !!v.perms, notify_perms: !!v.notify_perms,
          contact: !!v.contact, deny: treeRead(v, true), features: v.features.split("\n").map(s => s.trim()).filter(Boolean), features_ar: v.features_ar.split("\n").map(s => s.trim()).filter(Boolean),
          badge: v.badge.trim(), badge_ar: v.badge_ar.trim(), color: v.color, highlight: !!v.highlight, hidden: !!v.hidden, ai: !!v.ai, support: !!v.support,
          support_rank: Math.max(0, Math.min(99, Math.round(+v.support_rank || 0))), reply_hours: v.reply_hours === "" || !(+v.reply_hours > 0) ? null : +v.reply_hours,
          modules: v.mods === "all" ? "all" : SECTIONS().map(s => s[0]).filter(k => v["s_" + k]) };
        if (!np.code) throw new Error(L("Give the plan a code.", "أدخل كود الباقة."));
        if (!np.contact && !(np.users >= 1)) throw new Error(L("Set how many users the plan includes.", "حدد عدد المستخدمين في الباقة."));
        if (np.companies != null && !(np.companies >= 1)) throw new Error(L("Companies: 1 or more, or leave it blank.", "الشركات: 1 أو أكثر، أو اتركه فارغاً."));
        if (np.users == null) delete np.users; if (np.companies == null) delete np.companies; if (!np.deny.length) delete np.deny; if (np.reply_hours == null) delete np.reply_hours;
        const list = plans.slice(); if (i == null) list.push(np); else list[i] = np; await save(list); }, { wide: true,
        extra: i != null ? `<button type="button" class="btn" data-a="pdup">${esc(L("Duplicate", "نسخ"))}</button>${p.code !== cfg.trial_plan ? `<button type="button" class="btn danger" data-a="pdel">${esc(L("Delete", "حذف"))}</button>` : ""}` : "",
        onOpen: f => { treeWire(f); const draw = () => { const v = readForm(f); $("#pprev", f).innerHTML = planCard({ ...p, ...v, price: +v.price, users: +v.users, features: v.features.split("\n").filter(Boolean), features_ar: v.features_ar.split("\n").filter(Boolean),
          modules: v.mods === "all" ? "all" : SECTIONS().map(s => s[0]).filter(k => v["s_" + k]) }, cfg); }; f.addEventListener("input", draw); f.addEventListener("change", draw); draw(); } });
    S.H.pdup = () => { closePanel(); const c = { ...p, code: p.code + "-2", name: p.name + " 2", hidden: true }; save([...plans, c], L("Copied as a hidden plan.", "نُسخت كباقة مخفية.")); };
    S.H.pdel = async () => { if (!await confirmIt(L(`Delete the plan ${p.name}? Companies already on it keep their paid period; renewals then need another plan.`, `حذف الباقة ${p.name}؟ تحتفظ الشركات المشتركة بفترتها المدفوعة؛ ويلزمها باقة أخرى عند التجديد.`), { danger: true })) return; closePanel(); save(plans.filter((_, j) => j !== i)); };
  };
  Object.assign(S.H, { pnew: () => edit(null), pedit: el => edit(+el.dataset.i),
    pup: el => { const i = +el.dataset.i; if (i < 1) return; const l = plans.slice(); [l[i - 1], l[i]] = [l[i], l[i - 1]]; save(l); },
    pdown: el => { const i = +el.dataset.i; if (i >= plans.length - 1) return; const l = plans.slice(); [l[i + 1], l[i]] = [l[i], l[i + 1]]; save(l); },
    phide: el => { const l = plans.slice(), i = +el.dataset.i; l[i] = { ...l[i], hidden: !l[i].hidden }; save(l); } });
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><button class="btn pri" data-a="pnew">+ ${esc(L("Plan", "باقة"))}</button>
      <span class="muted small">${esc(L("Customers see these cards on their Subscription page, in this order. New prices apply from the next payment.", "يرى العملاء هذه البطاقات في صفحة الاشتراك بهذا الترتيب. تسري الأسعار الجديدة من الدفعة التالية."))}</span></div>
    <div class="plans">${plans.map((p, i) => planCard(p, cfg, `<button class="btn sm" data-a="pedit" data-i="${i}">${esc(L("Edit", "تعديل"))}</button><button class="btn sm" data-a="phide" data-i="${i}">${esc(p.hidden ? L("Show", "إظهار") : L("Hide", "إخفاء"))}</button>
      <button class="btn sm" data-a="pup" data-i="${i}" title="${esc(L("Move earlier", "تقديم"))}">↑</button><button class="btn sm" data-a="pdown" data-i="${i}" title="${esc(L("Move later", "تأخير"))}">↓</button>`)).join("")}</div>`;
};

// ================================================================== sections, AI and support: included, sold on their own, or free
PAGES.addons = async view => {
  const cfg = (await rpc("billing_admin_overview")).config, core = cfg.core_modules || [], prices = cfg.module_prices || {};
  S.H.asave = async () => { const f = $("#af"), v = readForm(f);
    const core2 = SECTIONS().map(s => s[0]).filter(k => v["c_" + k]), mp = {};
    for (const [k] of SECTIONS()) if (v["sell_" + k] && !v["c_" + k]) mp[k] = Math.max(0, +v["p_" + k] || 0);
    await rpc("billing_set_config", { p: { core_modules: core2, module_prices: mp, extra_user: +v.extra_user, extra_company_pct: +v.extra_company_pct,
      ai_mode: v.ai_mode, ai_addon: !!v.ai_addon, ai_price: +v.ai_price || 0, support_mode: v.support_mode, support_addon: !!v.support_addon, support_price: +v.support_price || 0 } });
    toast(L("Saved.", "تم الحفظ.")); route(); };
  view.innerHTML = `<form id="af" onsubmit="return false"><div class="card"><h2>${esc(L("Modules of the ERP", "أقسام النظام"))}</h2>
    <p class="muted small">${esc(L("Core: every plan includes it. Sold on its own: a customer can add it to a plan that does not include it, for this monthly price per company (0 = free to add). Neither: only plans that include it.", "أساسي: تشمله كل الباقات. يُباع منفصلاً: يمكن للعميل إضافته لباقة لا تشمله بهذا السعر الشهري لكل شركة (0 = إضافة مجانية). غير ذلك: فقط الباقات التي تشمله."))}</p>
    <div class="tbl"><table><thead><tr><th>${esc(L("Module", "القسم"))}</th><th>${esc(L("Core", "أساسي"))}</th><th>${esc(L("Sold on its own", "يُباع منفصلاً"))}</th><th>${esc(L(`Price / month (${cfg.currency})`, `السعر / شهر (${cfg.currency})`))}</th></tr></thead><tbody>
      ${SECTIONS().map(([k, l]) => `<tr><td>${esc(l)}</td><td><input type="checkbox" name="c_${k}" ${core.includes(k) ? "checked" : ""}></td><td><input type="checkbox" name="sell_${k}" ${k in prices ? "checked" : ""}></td>
        <td><input type="number" name="p_${k}" min="0" step="any" value="${esc(prices[k] ?? "")}" style="max-width:140px"></td></tr>`).join("")}</tbody></table></div></div>
    <div class="grid g2"><div class="card"><h2>${esc(L("AI assistant", "المساعد الذكي"))}</h2>${form([
      { k: "ai_mode", l: L("Who gets it", "من يحصل عليه"), type: "select", v: cfg.ai_mode, opts: [["plan", L("Plans that include it (and the add-on)", "الباقات التي تشمله (والإضافة)")], ["free", L("Everyone, free", "الجميع مجاناً")], ["off", L("Nobody (switched off)", "لا أحد (متوقف)")]], span: 1 },
      { k: "ai_addon", l: L("Sold as an add-on", "يُباع كإضافة"), type: "check", v: cfg.ai_addon }, { k: "ai_price", l: L("Add-on price / month per company", "سعر الإضافة / شهر لكل شركة"), type: "number", v: cfg.ai_price, min: 0 }])}
      <p class="muted small">${esc(L("Each company still connects its own AI provider key in its settings (keys never leave the server).", "تربط كل شركة مفتاح مزود الذكاء الاصطناعي الخاص بها من إعداداتها (لا تغادر المفاتيح الخادم)."))}</p></div>
    <div class="card"><h2>${esc(L("Online support (tickets)", "الدعم الإلكتروني (البلاغات)"))}</h2>${form([
      { k: "support_mode", l: L("Who gets it", "من يحصل عليه"), type: "select", v: cfg.support_mode, opts: [["free", L("Everyone, free", "الجميع مجاناً")], ["plan", L("Plans that include it (and the add-on)", "الباقات التي تشمله (والإضافة)")]], span: 1 },
      { k: "support_addon", l: L("Sold as an add-on", "يُباع كإضافة"), type: "check", v: cfg.support_addon }, { k: "support_price", l: L("Add-on price / month per company", "سعر الإضافة / شهر لكل شركة"), type: "number", v: cfg.support_price, min: 0 }])}
      <p class="muted small">${esc(L("Questions about the subscription itself are always allowed. Per customer, set it in the company's “What it can use”. Visits are priced one by one (Rules & switches → Support visits).", "الأسئلة عن الاشتراك نفسه مسموحة دائماً. لكل عميل اضبطه من «ما يمكنها استخدامه» في صفحة الشركة. الزيارات تُسعَّر كل واحدة على حدة (القواعد والمفاتيح ← زيارات الدعم)."))}</p></div></div>
    <div class="card"><h2>${esc(L("Users and companies", "المستخدمون والشركات"))}</h2>${form([
      { k: "extra_user", l: L(`Extra user, per month (${cfg.currency})`, `المستخدم الإضافي شهرياً (${cfg.currency})`), type: "number", v: cfg.extra_user, min: 0 },
      { k: "extra_company_pct", l: L("Each further company in an order (% of the plan)", "كل شركة إضافية في الطلب (% من الباقة)"), type: "number", v: cfg.extra_company_pct, min: 0, max: 1000 }])}</div>
    <button class="btn pri" data-a="asave">${esc(L("Save", "حفظ"))}</button></form>`;
};

// ================================================================== menus by industry: the starter sections every plan includes
PAGES.menus = async view => {
  const cfg = (await rpc("billing_admin_overview")).config, core = cfg.core_modules || [];
  const inds = industriesOf(cfg), secs = SECTIONS();
  const iname = (k, d) => S.lang === "ar" && d.name_ar ? d.name_ar : d.name || k;
  const save = async next => { await rpc("billing_set_config", { p: next }); toast(L("Saved.", "تم الحفظ.")); route(); };
  Object.assign(S.H, {
    msave: () => { const v = readForm($("#mf")), next = {};
      inds.forEach(([k, d]) => { next[k] = { ...d, modules: secs.map(s => s[0]).filter(m => v[`i_${k}_${m}`]) }; });
      return save({ core_modules: secs.map(s => s[0]).filter(m => v["c_" + m]), core_by_industry: next }); },
    mnew: () => panel(L("New industry", "نشاط جديد"), form([{ k: "key", l: L("Code (small letters)", "الكود (حروف صغيرة)"), req: 1, dir: "ltr" }, { k: "name", l: L("Name", "الاسم"), req: 1 }, { k: "name_ar", l: L("Name (Arabic)", "الاسم (عربي)") }]),
      v => { const k = v.key.trim().toLowerCase().replace(/[^a-z_]/g, ""); if (!k) throw new Error(L("Give it a code.", "أدخل الكود.")); if ((cfg.core_by_industry || {})[k]) throw new Error(L("This code is used.", "هذا الكود مستخدم."));
        return save({ core_by_industry: { ...(cfg.core_by_industry || {}), [k]: { name: v.name.trim(), name_ar: v.name_ar.trim(), modules: [] } } }); }),
    mren: el => { const k = el.dataset.k, d = cfg.core_by_industry[k];
      panel(L("Industry", "النشاط"), form([{ k: "name", l: L("Name", "الاسم"), v: d.name || "", req: 1 }, { k: "name_ar", l: L("Name (Arabic)", "الاسم (عربي)"), v: d.name_ar || "" }]),
        v => save({ core_by_industry: { ...cfg.core_by_industry, [k]: { ...d, name: v.name.trim(), name_ar: v.name_ar.trim() } } }), {
          extra: `<button type="button" class="btn danger" data-a="mdel" data-k="${esc(k)}">${esc(L("Delete", "حذف"))}</button>` }); },
    mdel: async el => { const k = el.dataset.k; if (!await confirmIt(L("Delete this industry? Companies set to it keep only the core modules and their plan's.", "حذف هذا النشاط؟ الشركات المرتبطة به تحتفظ بالأقسام الأساسية وأقسام باقتها فقط."), { danger: true })) return;
      const next = { ...cfg.core_by_industry }; delete next[k]; closePanel(); save({ core_by_industry: next }); },
    mall: el => { const k = el.dataset.k, boxes = $$(`input[name^="i_${k}_"]`, $("#mf")), on = boxes.some(b => !b.checked); boxes.forEach(b => { b.checked = on; }); },
  });
  view.innerHTML = `<form id="mf" onsubmit="return false"><div class="card"><div class="row"><h2 style="margin:0">${esc(L("Starter modules by industry", "الأقسام المبدئية حسب النشاط"))}</h2><span class="sp"></span>
      <button class="btn sm" data-a="mnew">+ ${esc(L("Industry", "نشاط"))}</button></div>
    <p class="muted small">${esc(L("Every plan includes the core modules and the starter modules of the company's industry, so each business has what it needs from day one. Plans add more; one company can get more on its own page (What it can use), and any screen or tab can be switched off per plan or per company. The trial and free companies see every module.", "تشمل كل باقة الأقسام الأساسية والأقسام المبدئية لنشاط الشركة، ليجد كل نشاط ما يحتاجه من اليوم الأول. تضيف الباقات المزيد، ويمكن منح شركة واحدة المزيد من صفحتها (ما يمكنها استخدامه)، ويمكن إيقاف أي شاشة أو تبويب لكل باقة أو لكل شركة. التجربة والشركات المجانية ترى كل الأقسام."))}</p>
    <div class="tbl matrix"><table><thead><tr><th>${esc(L("Module", "القسم"))}</th><th class="center">${esc(L("Core (all)", "أساسي (للكل)"))}</th>
      ${inds.map(([k, d]) => `<th class="center"><button type="button" class="linkb" data-a="mren" data-k="${esc(k)}" title="${esc(L("Rename or delete", "إعادة التسمية أو الحذف"))}">${esc(iname(k, d))}</button><br><button type="button" class="linkb small" data-a="mall" data-k="${esc(k)}">${esc(L("all / none", "الكل / لا شيء"))}</button></th>`).join("")}</tr></thead>
      <tbody>${secs.map(([m, l]) => `<tr><td>${esc(l)}</td><td class="center"><input type="checkbox" name="c_${m}" ${core.includes(m) ? "checked" : ""}></td>
        ${inds.map(([k, d]) => `<td class="center"><input type="checkbox" name="i_${k}_${m}" ${(d.modules || []).includes(m) ? "checked" : ""} title="${esc(iname(k, d) + " · " + l)}"></td>`).join("")}</tr>`).join("")}</tbody></table></div>
    <div class="row" style="margin-top:12px"><button class="btn pri" data-a="msave">${esc(L("Save", "حفظ"))}</button></div></div></form>`;
};

// ================================================================== rules and switches
PAGES.settings = async view => {
  const c = (await rpc("billing_admin_overview")).config, appr = can("approve"), sup = c.support || {}, pg = c.page || {}, mt = c.maintenance || {}, vi = c.visits || {}, nt = c.notify || {};
  S.H.toggle = async () => { const on = !c.enabled;
    if (!await confirmIt(on ? L(`Switch billing on? Every company that exists now gets a fresh ${c.trial_days}-day free trial from this moment; after that a person's first company has the trial and later ones need a subscription.`, `تفعيل الاشتراكات؟ تحصل كل شركة موجودة الآن على تجربة مجانية جديدة ${c.trial_days} يوماً من هذه اللحظة؛ بعد ذلك الشركة الأولى لكل شخص لها التجربة وما بعدها يحتاج اشتراكاً.`)
      : L("Switch billing off? The app becomes free for everyone (nothing is lost).", "إيقاف الاشتراكات؟ يصبح التطبيق مجانياً للجميع (لا يضيع شيء)."))) return;
    await rpc("billing_set_config", { p: { enabled: on } }); S.me.billing_enabled = on; toast(L("Saved.", "تم الحفظ.")); route(); };
  // a hidden plan only for the trial, starting as a copy of the trial's current plan: edit it in Plans
  S.H.trialplan = async () => {
    const from = c.plans.find(p => p.code === c.trial_plan) || c.plans[0];
    const tp = { ...from, code: "trial", name: "Trial", name_ar: "التجربة", price: 0, hidden: true, contact: false, highlight: false, badge: "", badge_ar: "",
      users: from.users || 3, tagline: "", tagline_ar: "" };
    await rpc("billing_set_config", { p: { plans: [...c.plans, tp], trial_plan: "trial" } });
    toast(L("Made the Trial plan (hidden). Choose what it includes in Plans; set “During the trial” to “Only what the plan includes” for it to apply.",
      "أُنشئت باقة التجربة (مخفية). اختر ما تشمله من الباقات؛ واضبط «أثناء التجربة» على «ما تشمله الباقة فقط» لتُطبق."));
    location.hash = "#/plans"; };
  S.H.ssave = async () => { const v = readForm($("#sf2"));
    const p = { trial_days: +v.trial_days, trial_plan: v.trial_plan, trial_full: v.trial_full === "all", grace_days: +v.grace_days, yearly_months: +v.yearly_months, currency: v.currency.trim(),
      max_companies: +v.max_companies || 0, notify: { trial_days: +v.n_trial || 3, renewal_days: +v.n_renew || 7 },
      support: { whatsapp: v.s_whatsapp.trim(), phone: v.s_phone.trim(), email: v.s_email.trim(), hours: v.s_hours.trim() },
      page: { title: v.pg_title, title_ar: v.pg_title_ar, intro: v.pg_intro, intro_ar: v.pg_intro_ar, footer: v.pg_footer, footer_ar: v.pg_footer_ar },
      visits: { on: !!v.v_on, price: +v.v_price || 0, pay_when: v.v_pay, terms: v.v_terms, terms_ar: v.v_terms_ar } };
    if (appr) Object.assign(p, { accept_new: !!v.accept_new, maintenance: { on: !!v.m_on, message: v.m_msg, message_ar: v.m_msg_ar } });
    await rpc("billing_set_config", { p }); toast(L("Saved.", "تم الحفظ.")); route(); };
  view.innerHTML = `<div class="card"><div class="row"><h2 style="margin:0">${esc(L("Billing", "الاشتراكات"))} ${c.enabled ? chip(L("On", "مفعّلة"), "ok") : chip(L("Off — free for everyone", "متوقفة — مجاني للجميع"))}</h2><span class="sp"></span>
      ${appr ? `<button class="btn ${c.enabled ? "danger" : "pri"}" data-a="toggle">${esc(c.enabled ? L("Switch billing off", "إيقاف الاشتراكات") : L("Switch billing on", "تفعيل الاشتراكات"))}</button>` : ""}</div>
      ${c.enabled_at ? `<p class="muted small">${esc(L("Live since", "مفعّلة منذ"))} ${esc(day(c.enabled_at))}</p>` : ""}</div>
    <form id="sf2" onsubmit="return false">
    ${appr ? `<div class="card"><h2>${esc(L("Switches", "المفاتيح"))}</h2>${form([
      { k: "m_on", l: L("Maintenance: changes paused for every customer (viewing works; your team can still save)", "صيانة: إيقاف الحفظ لكل العملاء (يبقى العرض، ويستطيع فريقك الحفظ)"), type: "check", v: mt.on, span: 1 },
      { k: "m_msg", l: L("Maintenance message", "رسالة الصيانة"), v: mt.message || "" }, { k: "m_msg_ar", l: L("Maintenance message (Arabic)", "رسالة الصيانة (عربي)"), v: mt.message_ar || "" },
      { k: "accept_new", l: L("Accept new customers (people without a company may create one)", "قبول عملاء جدد (يمكن لمن ليس لديه شركة إنشاء واحدة)"), type: "check", v: c.accept_new !== false, span: 1 }])}</div>` : ""}
    <div class="grid g2"><div class="card"><h2>${esc(L("Trial and periods", "التجربة والفترات"))}</h2>${form([
      { k: "trial_days", l: L("Free trial (days) — a person's first company", "التجربة المجانية (أيام) — الشركة الأولى لكل شخص"), type: "number", v: c.trial_days, min: 0, max: 365 },
      { k: "trial_full", l: L("During the trial", "أثناء التجربة"), type: "select", v: c.trial_full !== false ? "all" : "plan", span: 1,
        opts: [["all", L("Everything: every module, screen, the assistant and permissions", "كل شيء: كل الأقسام والشاشات والمساعد والصلاحيات")], ["plan", L("Only what the plan below includes", "ما تشمله الباقة أدناه فقط")]] },
      { k: "trial_plan", l: L("The trial's plan (its users and companies always apply)", "باقة التجربة (يُطبق عدد مستخدميها وشركاتها دائماً)"), type: "select", v: c.trial_plan, opts: c.plans.map(p => [p.code, p.name + (p.hidden ? " · " + L("hidden", "مخفية") : "")]) },
      { type: "html", span: 1, html: `<p class="muted small" style="margin:0">${esc(L("To decide exactly what the trial includes (modules, screens and tabs, users, companies, assistant, permissions), make a hidden plan for it and choose it here.", "لتحديد ما تشمله التجربة بدقة (الأقسام والشاشات والتبويبات والمستخدمون والشركات والمساعد والصلاحيات)، أنشئ باقة مخفية لها واخترها هنا."))}
        ${c.plans.some(p => p.code === "trial") ? "" : ` <button type="button" class="btn sm" data-a="trialplan">${esc(L("Make a plan for the trial", "إنشاء باقة للتجربة"))}</button>`}</p>` },
      { k: "grace_days", l: L("Grace days after a paid period", "أيام السماح بعد الفترة المدفوعة"), type: "number", v: c.grace_days, min: 0, max: 60 },
      { k: "yearly_months", l: L("A year costs (months)", "السنة تكلف (أشهر)"), type: "number", v: c.yearly_months, min: 1, max: 12 },
      { k: "currency", l: L("Currency", "العملة"), v: c.currency }, { k: "max_companies", l: L("Companies one person may own (0 = no limit)", "الشركات التي يملكها الشخص (0 = بلا حد)"), type: "number", v: c.max_companies || 0, min: 0 }])}</div>
    <div class="card"><h2>${esc(L("When to warn me", "متى أُنبَّه"))}</h2>${form([
      { k: "n_trial", l: L("Days before a trial ends", "أيام قبل نهاية التجربة"), type: "number", v: nt.trial_days ?? 3, min: 1, max: 60 },
      { k: "n_renew", l: L("Days before a paid period ends", "أيام قبل نهاية الفترة المدفوعة"), type: "number", v: nt.renewal_days ?? 7, min: 1, max: 60 }])}
      <h2 style="margin-top:16px">${esc(L("Support contacts shown to customers", "بيانات الدعم للعملاء"))}</h2>${form([
      { k: "s_whatsapp", l: "WhatsApp", v: sup.whatsapp || "", dir: "ltr" }, { k: "s_phone", l: L("Phone", "الهاتف"), v: sup.phone || "", dir: "ltr" },
      { k: "s_email", l: L("Email", "البريد الإلكتروني"), v: sup.email || "", dir: "ltr" }, { k: "s_hours", l: L("Working hours", "مواعيد العمل"), v: sup.hours || "" }])}</div></div>
    <div class="card"><h2>${esc(L("Support visits", "زيارات الدعم"))}</h2>${form([
      { k: "v_on", l: L("Customers can ask for a visit", "يمكن للعملاء طلب زيارة"), type: "check", v: vi.on !== false, span: 1 },
      { k: "v_price", l: L(`Usual price (${c.currency}) — you can change it per visit`, `السعر المعتاد (${c.currency}) — يمكنك تغييره لكل زيارة`), type: "number", v: vi.price || 0, min: 0 },
      { k: "v_pay", l: L("Usually paid", "يُدفع عادة"), type: "select", v: vi.pay_when || "before", opts: [["before", L("Before the visit", "قبل الزيارة")], ["after", L("After the visit", "بعد الزيارة")], ["free", L("No charge", "بدون رسوم")]] },
      { k: "v_terms", l: L("Terms shown when asking (area covered, cancellation…)", "الشروط عند الطلب (المناطق، الإلغاء…)"), type: "area", v: vi.terms || "" }, { k: "v_terms_ar", l: L("Terms (Arabic)", "الشروط (عربي)"), type: "area", v: vi.terms_ar || "" }])}</div>
    <div class="card"><h2>${esc(L("Customers' Subscription page", "صفحة الاشتراك لدى العملاء"))}</h2>${form([
      { k: "pg_title", l: L("Heading", "العنوان"), v: pg.title || "" }, { k: "pg_title_ar", l: L("Heading (Arabic)", "العنوان (عربي)"), v: pg.title_ar || "" },
      { k: "pg_intro", l: L("Text above the plans", "نص فوق الباقات"), type: "area", v: pg.intro || "" }, { k: "pg_intro_ar", l: L("Text above the plans (Arabic)", "نص فوق الباقات (عربي)"), type: "area", v: pg.intro_ar || "" },
      { k: "pg_footer", l: L("Small print under the total", "ملاحظة أسفل الإجمالي"), type: "area", v: pg.footer || "" }, { k: "pg_footer_ar", l: L("Small print (Arabic)", "الملاحظة (عربي)"), type: "area", v: pg.footer_ar || "" }])}</div>
    <button class="btn pri" data-a="ssave">${esc(L("Save", "حفظ"))}</button></form>`;
};

// ================================================================== payment methods
PAGES.methods = async view => {
  const rows = await rpc("console_methods");
  const KN = { instapay: "InstaPay", wallet: L("Mobile wallet", "محفظة"), bank: L("Bank transfer", "تحويل بنكي"), card: L("Card (Paymob)", "بطاقة (Paymob)"), cash: L("Cash", "نقداً"), other: L("Other", "أخرى") };
  const edit = m => { m = m || { kind: "instapay", needs_proof: true, active: true, sort: rows.length * 10 };
    panel(m.id ? m.name : L("New payment method", "طريقة دفع جديدة"), form([
      { k: "kind", l: L("Type", "النوع"), type: "select", v: m.kind, opts: Object.entries(KN) }, { k: "sort", l: L("Order in the list", "الترتيب"), type: "number", v: m.sort },
      { k: "name", l: L("Name shown to customers", "الاسم الظاهر للعملاء"), v: m.name, req: 1 }, { k: "name_ar", l: L("Name (Arabic)", "الاسم (عربي)"), v: m.name_ar || "" },
      { k: "details", l: L("Number / InstaPay address / account (customers copy it)", "الرقم / عنوان إنستاباي / الحساب (ينسخه العملاء)"), v: m.details || "", span: 1, dir: "ltr" },
      { k: "instructions", l: L("Instructions", "التعليمات"), type: "area", v: m.instructions || "" }, { k: "instructions_ar", l: L("Instructions (Arabic)", "التعليمات (عربي)"), type: "area", v: m.instructions_ar || "" },
      { k: "needs_proof", l: L("Customer uploads a picture of the transfer", "يرفع العميل صورة التحويل"), type: "check", v: m.needs_proof }, { k: "active", l: L("Offered to customers", "متاحة للعملاء"), type: "check", v: m.active }]),
      async v => { await rpc("console_method_save", { p: { ...v, id: m.id || "" } }); toast(L("Saved.", "تم الحفظ.")); },
      { extra: m.id ? `<button type="button" class="btn danger" data-a="mdel">${esc(L("Delete", "حذف"))}</button>` : "" });
    S.H.mdel = async () => { if (!await confirmIt(L("Delete this method? (Kept but switched off if orders used it.)", "حذف هذه الطريقة؟ (تبقى معطلة إن استُخدمت في طلبات.)"), { danger: true })) return; await rpc("console_method_delete", { p_id: m.id }); closePanel(); route(); }; };
  S.H.mnew = () => edit();
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><button class="btn pri" data-a="mnew">+ ${esc(L("Payment method", "طريقة دفع"))}</button></div><div class="card"><div id="mtbl"></div></div>`;
  afterHooks.push(v => table($("#mtbl", v), "methods", rows, { sort: "sort", dir: "asc", onRow: edit, rowCls: m => m.active ? "" : "dim",
    empty: L("No payment methods yet. Add InstaPay, a wallet number or a bank account.", "لا توجد طرق دفع بعد. أضف إنستاباي أو رقم محفظة أو حساباً بنكياً."),
    cols: [{ k: "name", l: L("Method", "الطريقة"), h: m => `${KIND_ICON[m.kind] || ""} <b>${esc(m.name)}</b><span class="sub">${esc(KN[m.kind] || m.kind)}</span>` }, { k: "details", l: L("Details", "البيانات"), h: m => `<span dir="ltr">${esc(m.details)}</span>` },
      { k: "needs_proof", l: L("Picture", "صورة"), v: m => m.needs_proof ? "✓" : "" }, { k: "sort", l: L("Order", "الترتيب"), num: true }, { k: "active", l: L("Status", "الحالة"), v: m => m.active ? 1 : 0, h: m => m.active ? chip(L("Offered", "متاحة"), "ok") : chip(L("Off", "معطلة")) }] }));
};

// ================================================================== the team
const AREAS = () => [["overview", L("Overview", "نظرة عامة")], ["customers", L("Customers, people, cancellations", "العملاء والأشخاص والإلغاءات")], ["billing", L("See orders and payments", "عرض الطلبات والمدفوعات")],
  ["approve", L("Approve payments; trials, free use, suspend, archive, accounts", "اعتماد المدفوعات؛ التجارب والمجاني والإيقاف والأرشفة والحسابات")], ["support", L("Support tickets and visits", "بلاغات وزيارات الدعم")],
  ["notices", L("Announcements", "الإعلانات")], ["settings", L("Plans, prices, rules, payment methods, look", "الباقات والأسعار والقواعد وطرق الدفع والمظهر")],
  ["site", L("Website: texts, pictures, clips, layout and links of the public pages", "الموقع: نصوص وصور ومقاطع وتخطيط وروابط الصفحات العامة")]];
const DESK_L = () => ({ reply: L("Reply to customers and start live chats", "الرد على العملاء وبدء المحادثات"), notes: L("Write internal notes", "كتابة ملاحظات داخلية"),
  manage: L("Change status, priority and who it is assigned to; end live chats", "تغيير الحالة والأولوية والمسؤول؛ إنهاء المحادثات"), close: L("Close tickets and share solutions", "إغلاق البلاغات ونشر الحلول"),
  books: L("Enter customers' books when they allow it", "دخول دفاتر العملاء عند سماحهم"), contacts: L("See customers' emails and company details", "رؤية بريد العملاء وبيانات الشركة"),
  replies: L("Edit saved replies", "تعديل الردود المحفوظة"), help: L("Edit Help & FAQ and shared solutions", "تعديل المساعدة والحلول المنشورة"),
  bugs: L("See bug reports", "رؤية بلاغات الأخطاء"), visits: L("Handle support visits", "إدارة زيارات الدعم") });
// ready-made support roles; the switches can then be changed one by one
const DESK_ROLES = () => ({ shadow: [L("Shadow — reads tickets, writes internal notes only", "مرافق — يقرأ البلاغات ويكتب ملاحظات داخلية فقط"), ["notes"]],
  agent: [L("Agent — answers and closes tickets", "موظف دعم — يرد على البلاغات ويغلقها"), ["reply", "notes", "manage", "close", "books", "contacts", "bugs"]],
  lead: [L("Lead — everything on the support desk", "مسؤول — كل شيء في مكتب الدعم"), DESK_KEYS] });
const deskOf = perms => perms == null ? Object.fromEntries(DESK_KEYS.map(k => [k, true])) : perms.desk && typeof perms.desk === "object" ? perms.desk : Object.fromEntries(DESK_KEYS.map(k => [k, !!perms.support]));
const deskRole = d => Object.entries(DESK_ROLES()).find(([, [, ks]]) => DESK_KEYS.every(k => !!d[k] === ks.includes(k)))?.[0] || "custom";
const localDT = v => { if (!v) return ""; const d = new Date(v); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
PAGES.team = async view => {
  const rows = await rpc("console_staff");
  const edit = s => { s = s || { active: true, perms: {} };
    const d = deskOf(s.email ? s.perms : { support: false }), role = deskRole(d);
    panel(s.email || L("Add a team member", "إضافة عضو للفريق"), form([
      { k: "email", l: L("Their sign-in email", "بريد تسجيل الدخول"), v: s.email || "", req: 1, dis: !!s.email, dir: "ltr" }, { k: "name", l: L("Name", "الاسم"), v: s.name || "" },
      { k: "full", l: L("Full access (everything, including the team, backups and deleting)", "صلاحية كاملة (كل شيء، بما في ذلك الفريق والنسخ الاحتياطية والحذف)"), type: "check", v: !!s.email && s.perms == null, span: 1 },
      ...AREAS().map(([k, l]) => ({ k: "a_" + k, l, type: "check", v: !!(s.perms || {})[k], span: 1 })),
      { type: "html", span: 1, html: `<div class="desk-perms"><h3 style="margin:8px 0 4px">${esc(L("On the support desk", "في مكتب الدعم"))}</h3>
        <p class="muted small" style="margin:0 0 8px">${esc(L("Applies when “Support tickets and visits” is ticked. Pick a role, then change any switch.", "يُطبق عند تحديد «بلاغات وزيارات الدعم». اختر دوراً ثم غيّر أي مفتاح."))}</p>
        <label class="f">${esc(L("Role", "الدور"))}<select data-change="deskrole">${Object.entries(DESK_ROLES()).map(([k, [l]]) => `<option value="${k}" ${k === role ? "selected" : ""}>${esc(l)}</option>`).join("")}
          <option value="custom" ${role === "custom" ? "selected" : ""}>${esc(L("Custom", "مخصص"))}</option></select></label>
        <div class="grid g1" style="gap:4px;margin-top:6px">${DESK_KEYS.map(k => `<label class="chk"><input type="checkbox" name="d_${k}" data-change="deskone" ${d[k] ? "checked" : ""}> ${esc(DESK_L()[k])}</label>`).join("")}</div></div>` },
      { k: "until", l: L("Access ends (blank: no end)", "تنتهي الصلاحية (فارغ: بلا نهاية)"), type: "datetime-local", v: localDT(s.until), help: L("For someone shadowing or helping for a while. Not used with full access.", "لمن يرافق أو يساعد لفترة. لا يُستخدم مع الصلاحية الكاملة.") },
      { k: "active", l: L("Active", "نشط"), type: "check", v: s.active }], 1),
      async v => { const perms = Object.fromEntries(AREAS().filter(([k]) => v["a_" + k]).map(([k]) => [k, true]));
        if (perms.support) perms.desk = Object.fromEntries(DESK_KEYS.map(k => [k, !!v["d_" + k]]));
        await rpc("console_staff_save", { p: { email: s.email || v.email, name: v.name, full: !!v.full, perms, active: !!v.active, until: v.until && !v.full ? new Date(v.until).toISOString() : "" } }); toast(L("Saved.", "تم الحفظ.")); },
      { extra: s.email && s.email !== S.me.email ? `<button type="button" class="btn danger" data-a="sdel">${esc(L("Remove", "إزالة"))}</button>` : "" });
    S.H.deskrole = el => { const ks = (DESK_ROLES()[el.value] || [, null])[1]; if (!ks) return; DESK_KEYS.forEach(k => { const c = $(`#panel [name=d_${k}]`); if (c) c.checked = ks.includes(k); }); };
    S.H.deskone = () => { const dd = Object.fromEntries(DESK_KEYS.map(k => [k, !!($(`#panel [name=d_${k}]`) || {}).checked])); const r = $("#panel [data-change=deskrole]"); if (r) r.value = deskRole(dd); };
    S.H.sdel = async () => { if (!await confirmIt(L(`Remove ${s.email} from the team?`, `إزالة ${s.email} من الفريق؟`), { danger: true })) return; await rpc("console_staff_save", { p: { email: s.email, delete: true } }); closePanel(); route(); }; };
  S.H.snew = () => edit();
  const ended = s => s.until && new Date(s.until) <= new Date();
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><button class="btn pri" data-a="snew">+ ${esc(L("Team member", "عضو فريق"))}</button><span class="muted small">${esc(L("They sign in here with their usual account and see only what is ticked. The console button appears in their ERP menu.", "يسجلون الدخول هنا بحسابهم المعتاد ويرون ما حُدد فقط. يظهر زر اللوحة في قائمة النظام لديهم."))}</span></div><div class="card"><div id="stbl"></div></div>`;
  afterHooks.push(v => table($("#stbl", v), "team", rows, { sort: "email", dir: "asc", onRow: edit, rowCls: s => s.active && !ended(s) ? "" : "dim",
    cols: [{ k: "email", l: L("Person", "الشخص"), h: s => `<b>${esc(s.name || s.email)}</b><span class="sub" dir="ltr">${esc(s.email)}</span>` },
      { k: "perms", l: L("Access", "الصلاحيات"), v: s => s.perms == null ? "full" : Object.keys(s.perms).join(" "), h: s => s.perms == null ? chip(L("Full access", "صلاحية كاملة"), "ok")
        : (AREAS().filter(([k]) => s.perms[k]).map(([k, l]) => chip(k === "support" ? `${l} · ${(DESK_ROLES()[deskRole(deskOf(s.perms))] || [L("Custom", "مخصص")])[0].split(" — ")[0]}` : l)).join(" ") || "—") },
      { k: "until", l: L("Access ends", "تنتهي الصلاحية"), date: true, h: s => s.until ? (ended(s) ? chip(L("Ended", "انتهت"), "bad") + " " : "") + esc(dt(s.until)) : "—" },
      { k: "active", l: L("Status", "الحالة"), v: s => s.active ? 1 : 0, h: s => s.active ? chip(L("Active", "نشط"), "ok") : chip(L("Off", "معطل")) }] }));
};

// ================================================================== the look of this console
PAGES.look = async view => {
  const lk = { ...LOOK0, ...(S.look || {}) };
  const live = () => { const v = readForm($("#lf")); S.look = { ...lk, ...v, logo: $("#lf").dataset.logo ?? lk.logo }; applyLook(); };
  Object.assign(S.H, {
    lsave: async () => { live(); await rpc("console_setting_save", { p_key: "console_look", p_value: S.look }); toast(L("Saved for the whole team.", "حُفظ للفريق كله.")); shell(); route(); },
    lreset: async () => { S.look = { ...LOOK0 }; await rpc("console_setting_save", { p_key: "console_look", p_value: S.look }); shell(); route(); },
    lsw: el => { $("[name=accent]").value = el.dataset.c; live(); },
    llogo: async el => { const f = el.files[0]; if (!f) return;
      const img = new Image(); img.src = URL.createObjectURL(f); await img.decode();
      const c = document.createElement("canvas"), k = Math.min(1, 160 / Math.max(img.width, img.height)); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height); $("#lf").dataset.logo = c.toDataURL("image/png"); live(); toast(L("Logo ready — press Save.", "الشعار جاهز — اضغط حفظ.")); },
    lnologo: () => { $("#lf").dataset.logo = ""; live(); } });
  view.innerHTML = `<form id="lf" class="card" onsubmit="return false"><h2>${esc(L("This console's look (for the whole team)", "مظهر هذه اللوحة (للفريق كله)"))}</h2>${form([
      { k: "name", l: L("Name at the top", "الاسم في الأعلى"), v: lk.name }, { k: "sub", l: L("Line under the name", "سطر تحت الاسم"), v: lk.sub },
      { k: "accent", l: L("Main colour", "اللون الأساسي"), type: "color", v: lk.accent },
      { type: "html", html: `<label class="f">${esc(L("Quick colours", "ألوان سريعة"))}<div class="swatches">${["#4f46e5", "#0ea5e9", "#0d9488", "#16a34a", "#ca8a04", "#ea580c", "#dc2626", "#db2777", "#7c3aed", "#334155"].map(c => `<button type="button" style="background:${c}" data-a="lsw" data-c="${c}"></button>`).join("")}</div></label>` },
      { k: "mode", l: L("Light or dark (each device can also switch from the top bar)", "فاتح أو داكن (يمكن لكل جهاز التبديل من الشريط العلوي)"), type: "select", v: lk.mode, opts: [["auto", L("Follow the device", "حسب الجهاز")], ["light", L("Light", "فاتح")], ["dark", L("Dark", "داكن")]] },
      { k: "side", l: L("Side menu", "القائمة الجانبية"), type: "select", v: lk.side, opts: [["dark", L("Dark", "داكنة")], ["light", L("Light", "فاتحة")], ["brand", L("Main colour", "اللون الأساسي")]] },
      { k: "density", l: L("Spacing", "المسافات"), type: "select", v: lk.density, opts: [["comfortable", L("Comfortable", "مريحة")], ["compact", L("Compact", "مضغوطة")]] },
      { k: "corners", l: L("Corners", "الحواف"), type: "select", v: lk.corners, opts: [["rounded", L("Rounded", "مستديرة")], ["square", L("Square", "حادة")]] },
      { type: "html", span: 1, html: `<label class="f">${esc(L("Logo", "الشعار"))}<input type="file" accept="image/*" data-change="llogo"></label>${lk.logo ? `<button type="button" class="btn sm" data-a="lnologo" style="margin-top:6px">${esc(L("Remove logo", "إزالة الشعار"))}</button>` : ""}` }])}
    <div class="row" style="margin-top:14px"><button class="btn pri" data-a="lsave">${esc(L("Save", "حفظ"))}</button><button class="btn" data-a="lreset">${esc(L("Back to the original look", "العودة للمظهر الأصلي"))}</button>
      <span class="muted small">${esc(L("Changes show as you make them; Save keeps them for everyone on the team.", "تظهر التغييرات فوراً؛ «حفظ» يحفظها لكل الفريق."))}</span></div></form>`;
  afterHooks.push(v => { const f = $("#lf", v); f.addEventListener("input", live); f.addEventListener("change", e => { if (e.target.type !== "file") live(); }); });
};

// ================================================================== activity of the team
PAGES.log = async view => {
  const rows = await rpc("console_log", { p_limit: 2000 });
  view.innerHTML = `<div class="card"><div id="gtbl"></div></div>`;
  afterHooks.push(v => table($("#gtbl", v), "log", rows, { sort: "at", dir: "desc", search: ["email", "action", "target", "d"],
    cols: [{ k: "at", l: L("When", "متى"), date: true, h: r => esc(dt(r.at)) }, { k: "email", l: L("Who", "من"), h: r => `<span dir="ltr">${esc(r.email)}</span>` },
      { k: "action", l: L("What", "ماذا") }, { k: "target", l: L("On", "على"), h: r => `<span dir="ltr">${esc(r.target || "")}</span>` },
      { k: "d", l: L("Details", "التفاصيل"), v: r => r.detail ? JSON.stringify(r.detail).slice(0, 300) : "", h: r => `<span class="muted small" dir="ltr">${esc(r.detail ? JSON.stringify(r.detail).slice(0, 200) : "")}</span>` }],
    filters: [{ k: "who", l: L("Who", "من"), type: "select", opts: [...new Set(rows.map(r => r.email))].map(e => [e, e]), test: (r, v) => r.email === v },
      { k: "what", l: L("What", "ماذا"), type: "select", opts: [...new Set(rows.map(r => r.action))].sort().map(a => [a, a]), test: (r, v) => r.action === v },
      { k: "from", l: L("From", "من"), type: "from", col: "at" }, { k: "to", l: L("To", "إلى"), type: "to", col: "at" }] }));
};

// ================================================================== deleted companies, and bringing one back from its backup
PAGES.deleted = async view => {
  const rows = await rpc("console_deleted");
  S.H.restore = async el => { const f = el.files[0]; if (!f) return;
    let bk; try { bk = JSON.parse(await f.text()); } catch { return toast(L("This file is not a backup.", "هذا الملف ليس نسخة احتياطية."), true); }
    if (!await confirmIt(L(`Bring back "${(bk.company || {}).name}" from this backup (taken ${day(bk.taken_at)})?`, `استعادة «${(bk.company || {}).name}» من هذه النسخة (أُخذت ${day(bk.taken_at)})؟`))) return;
    const r = await rpc("console_company_restore", { p_backup: bk }); toast(L("Restored.", "تمت الاستعادة.")); location.hash = "#/company/" + r.id; };
  view.innerHTML = `${S.me.full ? `<div class="card"><h2>${esc(L("Bring a company back", "استعادة شركة"))}</h2><p class="muted small">${esc(L("Choose the backup file downloaded from the company's page. Its books, people, billing and payments come back as they were.", "اختر ملف النسخة الاحتياطية المنزّل من صفحة الشركة. تعود دفاترها وأفرادها واشتراكها ومدفوعاتها كما كانت."))}</p>
      <input type="file" accept="application/json,.json" data-change="restore" style="max-width:360px"></div>` : ""}<div class="card"><div id="dtbl"></div></div>`;
  afterHooks.push(v => table($("#dtbl", v), "deleted", rows, { sort: "deleted_at", dir: "desc", search: ["name", "created_by", "deleted_by"], empty: L("Nothing deleted.", "لم يُحذف شيء."),
    cols: [{ k: "name", l: L("Company", "الشركة"), h: r => `<b>${esc(r.name)}</b>` }, { k: "created_by", l: L("Owner", "المالك") },
      { k: "deleted_at", l: L("Deleted", "حُذفت"), date: true, h: r => esc(dt(r.deleted_at)) }, { k: "deleted_by", l: L("By", "بواسطة") },
      { k: "paid", l: L("Had paid", "المدفوع"), num: true, v: r => (r.summary || {}).paid_total ?? "", h: r => money((r.summary || {}).paid_total) }] }));
};

start();
})();
