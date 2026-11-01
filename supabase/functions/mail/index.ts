// Announcement emails from the platform team to the people using BeyondERP (maintenance, changes, news).
//
//   POST {action: "status"}                                   -> {ready, from}: is an email service set up?
//   POST {action: "test", subject, subject_ar, body, body_ar} -> sends that email to the caller only
//   POST {action: "send", mailing}                            -> sends the next part of a saved email; call again while
//                                                                {left} > 0 (each call works for up to ~40 seconds)
//
// Who may send, and to whom, is decided by the database: this function asks it as the caller (their own sign-in), so
// a person without the "Announcements" permission gets nothing. Each person gets their own email; no one sees the
// others' addresses. The email service's key stays here (Supabase secrets):
//   MAIL_PROVIDER = resend | brevo,  MAIL_API_KEY = the service's key,
//   MAIL_FROM = "BeyondERP <info@mybeyonderp.com>" (the address must be verified with the service), MAIL_REPLY_TO
// Deployed by .github/workflows/deploy.yml with --no-verify-jwt (the database checks the caller's token).

const URL_ = Deno.env.get("SUPABASE_URL")!;
const ANON = Deno.env.get("SUPABASE_ANON_KEY")!;
const PROVIDER = (Deno.env.get("MAIL_PROVIDER") || "").toLowerCase();
const KEY = Deno.env.get("MAIL_API_KEY") || "";
const FROM = Deno.env.get("MAIL_FROM") || "BeyondERP <info@mybeyonderp.com>";
const REPLY_TO = Deno.env.get("MAIL_REPLY_TO") || "info@mybeyonderp.com";
const SITE = Deno.env.get("PUSH_SUBJECT") || "https://mybeyonderp.com/";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type, x-client-info",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json" } });
const fail = (status: number, msg: string) => reply(status, { msg, error: msg });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const ready = () => (PROVIDER === "resend" || PROVIDER === "brevo") && KEY !== "";

// the database, as the caller
async function rpc(auth: string, fn: string, args: unknown) {
  const r = await fetch(`${URL_}/rest/v1/rpc/${fn}`, {
    method: "POST", headers: { apikey: ANON, Authorization: auth, "Content-Type": "application/json" }, body: JSON.stringify(args),
  });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
  if (!r.ok) throw new Error((data && (data.message || data.msg)) || `database ${r.status}`);
  return data;
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const para = (s: string) => esc(s.trim()).replace(/\r?\n/g, "<br>");

type Mail = { subject: string; subject_ar?: string | null; body: string; body_ar?: string | null };
function compose(m: Mail) {
  const subject = m.subject_ar ? `${m.subject} | ${m.subject_ar}` : m.subject;
  const en = `<div dir="ltr" style="text-align:left"><h2 style="margin:0 0 12px;font-size:18px">${esc(m.subject)}</h2><p style="margin:0;line-height:1.6">${para(m.body)}</p></div>`;
  const ar = m.body_ar ? `<hr style="border:0;border-top:1px solid #e3e5f0;margin:22px 0"><div dir="rtl" lang="ar" style="text-align:right">
    <h2 style="margin:0 0 12px;font-size:18px">${esc(m.subject_ar || m.subject)}</h2><p style="margin:0;line-height:1.8">${para(m.body_ar)}</p></div>` : "";
  const html = `<!doctype html><html><body style="margin:0;background:#f4f5fb;font-family:Arial,Helvetica,sans-serif;color:#1d2140">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px"><div style="font-weight:bold;letter-spacing:3px;color:#4f46e5;margin-bottom:14px">BEYONDERP</div>
  <div style="background:#fff;border-radius:12px;padding:24px">${en}${ar}</div>
  <p style="font-size:12px;color:#6b6f8f;line-height:1.6;margin:16px 4px">You receive this because you use BeyondERP. Questions? Reply to this email.<br>
  <span dir="rtl">تصلك هذه الرسالة لأنك تستخدم BeyondERP. لأي سؤال، رُدّ على هذه الرسالة.</span><br><a href="${SITE}" style="color:#4f46e5">${SITE.replace(/^https?:\/\//, "").replace(/\/$/, "")}</a></p></div></body></html>`;
  const text = `${m.subject}\n\n${m.body.trim()}${m.body_ar ? `\n\n—\n${m.subject_ar || m.subject}\n\n${m.body_ar.trim()}` : ""}\n\n—\nYou receive this because you use BeyondERP. Questions? Reply to this email.\n${SITE}`;
  return { subject, html, text };
}

const fromParts = () => { const m = FROM.match(/^\s*(.*?)\s*<([^>]+)>\s*$/); return m ? { name: m[1] || "BeyondERP", email: m[2] } : { name: "BeyondERP", email: FROM.trim() }; };

// one email to one person: {ok, error, retry} (retry: the service asked us to slow down)
async function sendOne(to: string, c: { subject: string; html: string; text: string }) {
  let r: Response;
  try {
    if (PROVIDER === "resend") {
      r = await fetch("https://api.resend.com/emails", { method: "POST", headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({ from: FROM, to: [to], subject: c.subject, html: c.html, text: c.text, reply_to: REPLY_TO }) });
    } else {
      r = await fetch("https://api.brevo.com/v3/smtp/email", { method: "POST", headers: { "api-key": KEY, "Content-Type": "application/json", accept: "application/json" },
        body: JSON.stringify({ sender: fromParts(), to: [{ email: to }], subject: c.subject, htmlContent: c.html, textContent: c.text, replyTo: { email: REPLY_TO } }) });
    }
  } catch (e) { return { ok: false, retry: false, error: String((e as Error).message || e).slice(0, 200) }; }
  if (r.ok) return { ok: true, retry: false, error: "" };
  const body = (await r.text()).slice(0, 200);
  return { ok: false, retry: r.status === 429, error: `${r.status} ${body}` };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "POST") return fail(405, "POST only");
  const auth = req.headers.get("Authorization") || "";
  if (!/^Bearer .{10,}$/.test(auth)) return fail(401, "Sign in first");
  const raw = await req.text();
  if (raw.length > 100000) return fail(413, "Too large");
  let b: any;
  try { b = JSON.parse(raw); } catch { return fail(400, "Send JSON"); }

  let me: string;
  try { me = String(await rpc(auth, "console_mail_me", {})); } catch (e) { return fail(403, (e as Error).message); }   // Announcements permission
  if (b.action === "status") return reply(200, { ready: ready(), from: ready() ? FROM : null, provider: ready() ? PROVIDER : null });
  if (!ready()) return fail(503, "Email sending is not set up yet (MAIL_PROVIDER and MAIL_API_KEY)");
  const pace = PROVIDER === "resend" ? 550 : 150;   // stay under the services' request limits

  if (b.action === "test") {
    const m: Mail = { subject: String(b.subject || "").slice(0, 200), subject_ar: b.subject_ar ? String(b.subject_ar).slice(0, 200) : null,
                      body: String(b.body || "").slice(0, 20000), body_ar: b.body_ar ? String(b.body_ar).slice(0, 20000) : null };
    if (!m.subject.trim() || !m.body.trim()) return fail(400, "Write the subject and the message");
    const r = await sendOne(me, compose({ ...m, subject: `[Test] ${m.subject}` }));
    return r.ok ? reply(200, { sent: me }) : fail(502, r.error);
  }

  if (b.action === "send") {
    const id = Number(b.mailing);
    if (!Number.isInteger(id) || id <= 0) return fail(400, "Which email?");
    const t0 = Date.now();
    let sent = 0, failed = 0, status = "sending";
    try {
      while (Date.now() - t0 < 40000) {
        const next = await rpc(auth, "console_mail_next", { p_mailing: id, p_n: 20 });
        status = next.status;
        if (!next.to || !next.to.length) break;
        const c = compose(next);
        const results: { email: string; ok: boolean; error?: string }[] = [];
        for (const to of next.to as string[]) {
          let r = await sendOne(to, c);
          if (r.retry) { await sleep(2000); r = await sendOne(to, c); }
          results.push({ email: to, ok: r.ok, error: r.error });
          r.ok ? sent++ : failed++;
          await sleep(pace);
          if (Date.now() - t0 > 50000) break;
        }
        // anything claimed but not tried goes back to the queue after 10 minutes (console_mail_next)
        await rpc(auth, "console_mail_done", { p_mailing: id, p: results });
      }
    } catch (e) { return fail(400, (e as Error).message); }
    const list = await rpc(auth, "console_mailings", {});
    const m = (list || []).find((x: any) => x.id === id) || {};
    return reply(200, { sent, failed, status: m.status || status, total: m.total, done: m.sent, failures: m.failed,
                        left: Math.max(0, (m.total || 0) - (m.sent || 0) - (m.failed || 0)) });
  }
  return fail(400, "Unknown action");
});
