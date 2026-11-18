// The "you were added to a company" notices, through the real edge functions (supabase/functions/manage-user and push)
// under Deno, with everything outside them stood in for: a small in-memory PostgREST + auth, the email service and a
// device's push endpoint. Run by tests/test_edge_notices.py (skipped when Deno is not installed):
//   deno run --allow-env --allow-read --allow-net=none tests/edge/notices_test.ts
// Prints one JSON line with what was sent; exits 1 on a failed check.
const URL_ = "http://supa.test";
for (const [k, v] of Object.entries({ SUPABASE_URL: URL_, SUPABASE_SERVICE_ROLE_KEY: "service", SUPABASE_ANON_KEY: "anon",
  MAIL_PROVIDER: "resend", MAIL_API_KEY: "mail-key", MAIL_FROM: "BeyondERP <info@mybeyonderp.com>", PUSH_SUBJECT: "https://mybeyonderp.com/" })) Deno.env.set(k, v);

const CO = "11111111-1111-1111-1111-111111111111", HERS = "22222222-2222-2222-2222-222222222222";
const now = new Date().toISOString();
const db: Record<string, any[]> = {
  companies: [{ id: CO, name: "Boss Co", created_by: "boss@b.test" }, { id: HERS, name: "Sama Co", created_by: "sama@x.com" }],
  members: [{ company_id: CO, email: "boss@b.test", is_admin: true, is_super: true, active: true, pending: false, welcome_seen: true, added_by: null, added_at: now },
            { company_id: HERS, email: "sama@x.com", is_admin: true, is_super: false, active: true, pending: false, welcome_seen: true, added_by: null, added_at: now }],
  people: [{ email: "boss@b.test", username: "boss" }, { email: "sama@x.com", username: "" }],     // she signed up with only an email
  push_subs: [], push_config: [], push_log: [], push_queue: [],
};
const accounts: Record<string, string> = { "boss@b.test": "id-boss", "sama@x.com": "id-sama", "omar@c.test": "id-omar" };
const tokens: Record<string, string> = { "tok-boss": "boss@b.test", "tok-omar": "omar@c.test" };
const sent = { mail: [] as any[], push: [] as any[], pushCalls: [] as any[] };

// ---- a device of hers, subscribed for notifications (keys made the way a browser makes them)
const pair = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
const raw = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
const b64u = (b: Uint8Array) => btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
db.push_subs.push({ endpoint: "https://push.example/dev-1", email: "sama@x.com", company_id: HERS, p256dh: b64u(raw),
                    auth: b64u(crypto.getRandomValues(new Uint8Array(16))), prefs: { lang: "ar" } });

// ---- the stand-in PostgREST: eq / neq / is / ilike / in filters, select, upsert (on_conflict), patch, delete
function match(row: any, q: URLSearchParams) {
  for (const [k, v] of q) {
    if (["select", "limit", "on_conflict", "order"].includes(k)) continue;
    const [op, ...rest] = v.split("."); const val = rest.join(".");
    const cell = row[k];
    if (op === "eq" && String(cell ?? "") !== val) return false;
    if (op === "neq" && String(cell ?? "") === val) return false;
    if (op === "is" && String(cell) !== val) return false;
    if (op === "ilike" && String(cell ?? "").toLowerCase() !== val.toLowerCase()) return false;
    if (op === "in" && !val.replace(/^\(|\)$/g, "").split(",").map((x) => x.replace(/^"|"$/g, "")).includes(String(cell))) return false;
    if (op === "gte" && !(String(cell ?? "") >= val)) return false;
    if (op === "like" && !String(cell ?? "").startsWith(val.replace(/\*$/, ""))) return false;
  }
  return true;
}
const json = (data: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(data === undefined ? "" : JSON.stringify(data), { status, headers: { "Content-Type": "application/json", ...headers } });

let pushHandler: (r: Request) => Promise<Response>;
const realFetch = globalThis.fetch;
globalThis.fetch = (async (input: any, init: any = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url);
  const method = (init.method || "GET").toUpperCase();
  const body = init.body ? (typeof init.body === "string" ? init.body : new TextDecoder().decode(init.body)) : "";
  const auth = String((init.headers || {})["Authorization"] || (init.headers || {})["authorization"] || "").replace(/^Bearer\s+/i, "");
  if (url.host === "api.resend.com") { sent.mail.push({ key: auth, ...JSON.parse(body) }); return json({ id: "m1" }); }
  if (url.host === "push.example") { sent.push.push({ endpoint: url.href, headers: init.headers, bytes: (init.body as Uint8Array).length }); return new Response("", { status: 201 }); }
  if (url.origin !== URL_) throw new Error("unexpected request to " + url.href);
  if (url.pathname === "/functions/v1/push") {                     // manage-user → the push function, as Supabase routes it
    sent.pushCalls.push({ auth, body: JSON.parse(body) });
    return pushHandler(new Request(url.href, { method, headers: init.headers, body }));
  }
  if (url.pathname === "/auth/v1/user") { const e = tokens[auth]; return e ? json({ email: e, factors: [] }) : json({ msg: "bad token" }, 401); }
  if (url.pathname.startsWith("/auth/v1/admin/users")) return json({ id: "x" });
  if (url.pathname === "/rest/v1/rpc/auth_user_id") return json(accounts[JSON.parse(body).p_email] || null);
  const t = url.pathname.match(/^\/rest\/v1\/(\w+)$/)?.[1];
  if (!t || !(t in db)) throw new Error("no such table " + url.pathname);
  const q = url.searchParams;
  if (method === "GET") {
    const rows = db[t].filter((r) => match(r, q));
    if (t === "push_log") return json(rows, 200, { "content-range": `0-0/${rows.length}` });
    return json(rows.slice(0, Number(q.get("limit") || 1000)));
  }
  if (method === "POST") {
    const items = [].concat(JSON.parse(body));
    const keys = (q.get("on_conflict") || "").split(",").filter(Boolean);
    for (const it of items as any[]) {
      const hit = keys.length ? db[t].find((r) => keys.every((k) => String(r[k]) === String(it[k]))) : null;
      if (hit) { if (!/ignore-duplicates/.test(String((init.headers || {}).Prefer || ""))) Object.assign(hit, Object.fromEntries(Object.entries(it).filter(([, v]) => v !== undefined))); }
      else db[t].push(t === "members" ? { welcome_seen: true, added_at: new Date().toISOString(), is_admin: false, ...it } : { ...it });
    }
    return json(undefined, 201);
  }
  if (method === "PATCH") { db[t].filter((r) => match(r, q)).forEach((r) => Object.assign(r, JSON.parse(body))); return json(undefined, 204); }
  if (method === "DELETE") { db[t] = db[t].filter((r) => !match(r, q)); return json(undefined, 204); }
  throw new Error("unhandled " + method + " " + url.href);
}) as typeof fetch;

// ---- load both functions; each hands its handler to Deno.serve
const handlers: ((r: Request) => Promise<Response>)[] = [];
(Deno as any).serve = (h: any) => { handlers.push(h); return { finished: Promise.resolve() }; };
await import("../../supabase/functions/push/index.ts");
await import("../../supabase/functions/manage-user/index.ts");
[pushHandler] = handlers;
const manageUser = handlers[1];
const call = async (h: any, token: string, b: unknown) => {
  const r = await h(new Request("http://fn.test/", { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(b) }));
  return { status: r.status, body: await r.json() };
};

const fails: string[] = [];
const ok = (c: unknown, what: string) => { if (!c) fails.push(what); };

// 1. the administrator invites her by email (her email is her username: she has none)
const r1 = await call(manageUser, "tok-boss", { company: CO, username: "sama@x.com", email: "sama@x.com" });
ok(r1.status === 200, "invite refused: " + JSON.stringify(r1.body));
const m = db.members.find((x) => x.company_id === CO && x.email === "sama@x.com");
ok(m && m.welcome_seen === false && m.added_by === "boss@b.test", "membership not marked new: " + JSON.stringify(m));
ok(db.people.find((p) => p.email === "sama@x.com").username === "", "her account's username was changed");
ok(db.members.find((x) => x.company_id === HERS && x.email === "sama@x.com").is_admin === true, "her own company changed");
ok(sent.pushCalls.length === 1 && sent.pushCalls[0].auth === "tok-boss" && sent.pushCalls[0].body.kind === "joined", "push not asked: " + JSON.stringify(sent.pushCalls));
ok(sent.push.length === 1 && sent.push[0].endpoint === "https://push.example/dev-1" && sent.push[0].bytes > 50, "no push reached her device");
ok(r1.body.notified && r1.body.notified.push === 1 && r1.body.notified.email === true, "notified: " + JSON.stringify(r1.body.notified));
const mail = sent.mail[0] || {};
ok(sent.mail.length === 1 && mail.key === "mail-key" && JSON.stringify(mail.to) === '["sama@x.com"]', "email not sent to her: " + JSON.stringify(sent.mail));
ok(/Boss Co/.test(mail.subject || "") && /تمت إضافتك/.test(mail.subject || ""), "subject: " + mail.subject);
ok(/boss added you to Boss Co/.test(mail.text || "") && /own password/.test(mail.text || ""), "body (existing account): " + mail.text);

// 2. saved again (her role changed): no second notice
await call(manageUser, "tok-boss", { company: CO, username: "sama@x.com", email: "sama@x.com" });
ok(sent.mail.length === 1 && sent.push.length === 1, "a second save sent again");

// 3. the push function itself refuses anyone but the administrator who just added her
const r3 = await call(pushHandler, "tok-omar", { action: "send", company: CO, kind: "joined", email: "sama@x.com" });
ok(r3.status === 403, "an outsider could send the joined notice: " + r3.status);
m.welcome_seen = true;                                                // she has seen the card
const r4 = await call(pushHandler, "tok-boss", { action: "send", company: CO, kind: "joined", email: "sama@x.com" });
ok(r4.status === 403 && sent.push.length === 1, "sent again after it was seen");

// 4. a brand-new person (created with a password) is told to ask for their sign-in details; no email to an internal address
const r5 = await call(manageUser, "tok-boss", { company: CO, username: "nada", email: "nada@n.test", password: "pw123456" });
ok(r5.status === 200, "new person refused: " + JSON.stringify(r5.body));
const mail2 = sent.mail[1] || {};
ok(/Ask them for your username and password/.test(mail2.text || ""), "new person's email: " + mail2.text);
const r6 = await call(manageUser, "tok-boss", { company: CO, username: "worker1", password: "pw123456" });
ok(r6.status === 200 && sent.mail.length === 2, "an internal address got an email");

console.log(JSON.stringify({ fails, mails: sent.mail.map((x) => ({ to: x.to, subject: x.subject })), pushes: sent.push.length }));
globalThis.fetch = realFetch;
Deno.exit(fails.length ? 1 : 0);
