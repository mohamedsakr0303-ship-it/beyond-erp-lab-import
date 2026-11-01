# Sending email from info@mybeyonderp.com

The owner console and support desk send customer emails (Announcements → Email customers) through an email
service. Gmail forwarding (Cloudflare Email Routing) receives mail for info@mybeyonderp.com, but it cannot send
emails to many people; an email service does that. BeyondERP works with **Resend** or **Brevo**. Both have a free
tier; check their current limits on their sites before choosing.

**Resend is the simpler fit:** it sends from a sub-domain (`send.mybeyonderp.com`), so its records do not touch the
ones Cloudflare Email Routing already uses.

## 1. Create the account and verify the domain

1. Sign up at the service with info@mybeyonderp.com.
2. Add the domain `mybeyonderp.com` (Resend: Domains → Add domain; Brevo: Senders, domains & dedicated IPs → Domains).
3. The service shows a few DNS records. In Cloudflare → mybeyonderp.com → **DNS → Records**, add each one exactly as
   shown (type, name, value). Set the proxy to **DNS only** (grey cloud).
   * Keep **one** SPF record (`v=spf1 …`) per name. If the service asks for an SPF record on `mybeyonderp.com` itself,
     do not add a second one: add its `include:…` to the existing record, e.g.
     `v=spf1 include:_spf.mx.cloudflare.net include:spf.brevo.com ~all`.
   * If there is no DMARC record yet, add a TXT record named `_dmarc` with `v=DMARC1; p=none; rua=mailto:info@mybeyonderp.com`.
4. Back in the service, press **Verify**. It can take from minutes to a few hours.

## 2. Give the key to the workspace (it stays on the server)

1. In the service, create an API key with permission to send.
2. Supabase → your project → **Edge Functions → Secrets** → add:

| Name | Value |
|---|---|
| `MAIL_PROVIDER` | `resend` or `brevo` |
| `MAIL_API_KEY` | the key from step 1 |
| `MAIL_FROM` | `BeyondERP <info@mybeyonderp.com>` (optional; this is the default) |
| `MAIL_REPLY_TO` | `info@mybeyonderp.com` (optional; this is the default) |

The `mail` function is deployed with the others when a release reaches `main`. Never put the key in the repository,
in a GitHub variable that is printed, or in a page.

## 3. Check

Owner console → Announcements → **✉ Email customers…**: the panel says *Sent from BeyondERP <info@…>* instead of
"not set up yet". Write a message and press **Send me a test**: it arrives at your own address only. Check that it is
not in spam. If it is, wait for the domain to finish verifying, and check the SPF and DKIM records.
