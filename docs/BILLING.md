# Paid subscriptions (hosted service)

Nothing changes for anyone until **you switch billing on**. The app stays free until then.

## How it works

| Stage | What the customer sees | What they can do |
|---|---|---|
| **Trial** (15 days, on the Business plan, a person's **first company only**) | "Free trial — N days left · Subscribe" bar | Everything |
| **Not subscribed** (any further company a person creates) | Red bar: "not subscribed yet" | Sign in, view, print, export. **Saving is refused by the server** |
| **Active** (paid) | Nothing, or "ends in N days" during the last week | Everything |
| **Payment due** (3 grace days after a paid period ends) | Amber bar: "pay within N days" | Everything |
| **Ended** | Red bar: "view, print and export only" | Sign in, view, print, export. **Saving is refused by the server** |

* **Every plan covers one company** with its own users. A customer subscribes several companies in one order ("adding shirts to a bag"): the first company costs the plan price, each further one adds a set share of it (Plans and settings → *Each further company*, default 100 %). Extra users are bought per company.
* The trial is counted from when the company was created, or from when you switched billing on, whichever is later. Companies that already existed when billing was switched on keep their trial too.
* Each payment adds 1 month or 1 year after the current period. Nothing charges a card by itself. If the customer does not pay again, the company ends automatically when the period (plus grace days) is over.
* Data is never deleted. Paying again unlocks it immediately.
* Plans limit the number of active people. Adding, approving or re-activating someone beyond the plan is refused, with a message to add extra users.
* The block is enforced in the database (`append_op` refuses changes), not only in the browser. Prices are worked out in the database too, never in the browser.

## How a customer pays

1. **Subscription** (user menu): tick the companies, set extra users per company, choose the plan and monthly or yearly. The total is quoted by the server.
2. Choose a **payment method** you set up (InstaPay, wallet, bank, cash, card). An order with a reference (BO-…) opens.
3. Transfer methods show your number or account with copy buttons. The customer sends the money, then **uploads a screenshot** of the transfer (and optionally the transfer reference and amount sent) and presses *Send for verification*.
4. You get a notification. In **Owner console → Payments to verify** you see the screenshot, the amount sent (flagged if it differs from the order) and the companies. **Approve and activate** records one payment per company and activates them all at once; **Not accepted…** asks for a reason the customer sees, and they can send a new proof.
5. Card payments (with Paymob set up) approve the order automatically when Paymob confirms the exact amount.

Screenshots are kept in a private storage bucket (`billing-proofs`): only the customer who uploaded them and your team can open them.

## Go-live checklist

1. **Make yourself the platform owner.** Run this once in Supabase → SQL editor (with your own sign-in email):
   ```sql
   insert into public.platform_admins(email, name) values ('your-sign-in-email@example.com', 'Your name');
   ```
   After that, your user menu shows **Owner console ↗**, which opens the console page (`owner.html`). Everything else is done from there; you can add team members with limited access under **Team**.
2. **Check the plans and prices.** Owner console → **Plans and settings → Plans**. Defaults are below; change them any time. New prices apply to the next payment.
3. **Add payment methods.** Owner console → **Payment methods → + Payment method**: for example InstaPay with your InstaPay address, a mobile wallet number, a bank account. Tick *Customer uploads a picture of the transfer* for transfers.
4. **Support contacts.** Owner console → Plans and settings → *Trial, prices and support contacts*: WhatsApp, phone, email and working hours shown on customers' Help and support page.
5. **Card / wallet payments (Paymob, optional).** Create a Paymob account (accept.paymob.com). Then, in Supabase → Edge Functions → Secrets, add:

   | Secret | Where it comes from |
   |---|---|
   | `PAYMOB_SECRET_KEY` | Paymob dashboard → Settings → Account info → Secret key |
   | `PAYMOB_PUBLIC_KEY` | Paymob dashboard → Settings → Account info → Public key |
   | `PAYMOB_HMAC` | Paymob dashboard → Settings → Account info → HMAC |
   | `PAYMOB_INTEGRATION_IDS` | Comma-separated integration ids (card, mobile wallet…), from Developers → Payment integrations |

   Then set Paymob's transaction processed callback to `https://<project>.supabase.co/functions/v1/billing?hook=paymob`, and add a payment method of type **Card**.
6. **Deploy the database and functions.** Apply the migrations (`supabase db push --remote`) and deploy the `billing` and `push` edge functions, so the console, orders, support and notifications work.
7. **Switch billing on.** Owner console → Plans and settings → **Switch billing on**. Each person's first company gets its trial from that moment. Switching off makes the app free again, and nothing is lost.

## Billing on, off, per company

* **Billing off** (Plans and settings): the app is free for everyone.
* **Billing on**: from that moment every company that already exists gets a fresh free trial (the trial length setting, 15 days by default); after that, a person's first company gets the trial and later ones need a subscription.
* **Per company**: *Make free* takes one company out of billing for good (your own companies, partners, pilots); *Charge again* puts it back. Or record a payment of 0 for as many months as you like, so it shows as paid.
* Every action is written to the console's Activity tab.

## The owner console

The console is **a page of its own**: `owner.html` on the same site (for example `https://your-site/owner.html`). It uses the same sign-in as the ERP. People on the team see **Owner console ↗** in their ERP user menu, which opens it in a new tab. Nobody else sees the button, and the page refuses anyone who is not on the team. Every action is checked again in the database.

| Area | What it is for |
|---|---|
| Overview | Expected against received (12 months … 7 days, or your own dates, with an export), totals, the companies falling due next, the latest payments, a quick look at any company, and what is waiting for you |
| Notifications | Worked out from the data and checked every minute: new tickets and replies, visits (requested, accepted, payment to check, in the next 2 days), payments to verify, orders left unpaid for 3 days, trials ending without a subscription, trials that ended unpaid, payment due, customers who stopped paying, renewals coming up, requests not to renew, new companies, companies not subscribed, more people than the plan allows. The bell shows them; the Notifications page filters them. Each person marks them read, dismisses them and chooses which kinds they want. While the console is open they also pop up on the screen with a sound. Tickets, payments and visits also come as phone and computer notifications when the console is closed |
| Customers | Every company, with search, filters (status, plan, archived or not, due dates, last payment dates, joined dates, amounts paid) and sorting on every column, plus a CSV export |
| A company's page | **Subscription**: record a payment (any months, any amount including 0, with add-ons), restart a trial for any number of days, extend the trial or paid period, stop the trial, make free or charge again, a standing discount, dates and plan. **Access**: what it can use (all modules or chosen ones, the AI assistant on/off, online support on/off, number of users), **Menus, screens & tabs** (switch any module, screen or tab off for this company alone, or give back one its plan switches off; a search box finds any of them), its **industry**, the **Enterprise deal** (plan, users agreed, how many companies the owner may have, modules, assistant, support, months and the agreed amount, in one step), and suspend. Each company is set on its own page, even when the same person owns several. **Company**: rename, change owner, archive or bring back, download a backup, and delete for good. **People**: make admin or user, activate or deactivate, approve a request to join, remove, get a password reset link, turn off two-step sign-in, block the person |
| People | Everyone using the workspace, with the companies they own and belong to, last activity, a private note, a company limit per person, and blocking |
| Cancellations & churn | Requests not to renew (with the reason), ended without paying, payment due, never subscribed, suspended |
| Payments to verify | Orders with the screenshots, filters and search. Approve and activate, change the amount first, or decline with a reason |
| All payments | Every payment ever, kept even when a company is deleted, with filters, totals and an export |
| Tickets, Support visits | The support inbox, and visits customers ask for: set the price and propose a time, schedule (date, who goes), confirm the payment or waive it, mark done, or decline with a reason |
| Plans | Design what customers see: name, a line under it, price, users (a hard limit), how many companies a customer on the plan may own, "price on request" (Enterprise: customers ask, they cannot order it in the app), the screens and tabs the plan switches off, features (English and Arabic), a badge, a colour, stand out, hidden (offered to nobody, but you can still give it), the modules it includes, AI assistant, online support, **user permissions** (whether the company may choose which menus, screens and tabs each user sees) and **notification permissions** (whether it may choose who receives which notifications). A live preview shows the card as customers will see it. You can also duplicate plans, change their order, and delete them |
| Menus by industry | The starter modules of each industry (a table: modules × industries, plus the core column for every company). Every plan includes the core modules and the starter modules of the company's industry. They start as the modules each industry template shows; untick what an industry's plans should not include by default, add your own industries, rename or delete them |
| Modules, AI & support | Which ERP modules are core (in every plan); which can be bought on their own and at what monthly price (0 = free to add); the AI assistant (everyone free, by plan with an optional paid add-on, or off); online support (everyone free, or by plan with an optional paid add-on); extra user price; each further company % |
| Rules & switches | Billing on/off; maintenance (changes paused for every customer, with your message); accept new customers or not; trial length; during the trial: everything, or only what the trial's plan includes (**Make a plan for the trial** adds a hidden plan to shape the trial exactly: modules, screens and tabs, users, companies, assistant, permissions); grace days; yearly price; currency; how many companies one person may own; when to warn you; support contacts; visit settings (on/off, usual price, before/after/no charge, terms); the texts of the customers' Subscription page |
| Website texts | Every text of the home page, the sign-in screens and the footer (address and links), in English and Arabic, grouped by where it appears and searchable. A blank language keeps the built-in wording; ↺ goes back to it. Saved for every visitor at once. A team member can be given this area (Team → *Website texts*) without anything else |
| Payment methods, Announcements | As before |
| Team | Who helps you, and which areas each one may use. Only full-access members manage the team, download backups and delete companies |
| Look of this console | Name, line under it, main colour, logo, light/dark, side menu style, spacing, corners. It is shared by the team, and each device can also switch light/dark |
| Activity, Deleted companies | Everything the team did, filterable; and companies deleted, with a way to bring one back from its backup file |

### Arranging the console

* **Right-click** (long-press on a phone) a menu item to rename it, move it up or down or to another group, or hide it; a group heading to rename or move it; a column heading to sort, rename, move, hide or show columns; a row to open it or copy a cell or the whole row.
* **Drag** menu items, group headings and column headings to a new place.
* **Lock everything in place** (from any of those menus) stops changes until someone unlocks it (the 🔒 at the bottom of the side menu).
* The arrangement is shared by the whole team when made by someone allowed to change the console's settings; anyone else's stays on their own device. *Reset the menu* / *Reset columns* puts things back.
* On a phone, the side menu closes when you tap outside it or press Escape.

### Users, plans and the Enterprise plan

* **A plan's number of users is a hard limit.** Once every seat is taken, nobody can be added, no request to join can be approved and nobody can be re-activated, and the app cannot create another user (also through a desktop gateway). The app says "No user seats left" with a button to Subscription. Two additions at the same moment cannot both take the last seat. Requests to join still arrive, and wait.
* A number of users set for one company in the console is a limit too, even when the company is free or billing is off.
* Companies that already have more people than their plan (for example after moving to a smaller plan) keep them; they cannot add anyone until they are back under the limit. The *More people than the plan allows* notification and the *Users* filter on Customers show them.
* **Companies per plan.** A plan can say how many companies a customer on it may own. A person's limit is the most generous plan among the companies they own (a company on its trial counts with the trial's plan); blank uses the general rule (Rules & switches → companies one person may own). A number set for one person on People beats both. With billing off, only the general rule and the person's number apply.
* **User and notification permissions per plan.** With *user permissions* off, the company's Users page offers no permission grid or project access: every user has full use of what the plan includes, new users are created that way, permissions set before stay as they were, and an existing user can be given full use. With *notification permissions* off, *Notifications by department* is not offered and everyone receives the standard notifications. The workspace refuses a change that restricts a user, or the notification rules, while the plan leaves them out. A trial that shows everything, free use and billing off include both; one company can be given them, or have them taken, on its page (*What it can use*).
* **Enterprise** ("price on request") is added once to the plans. Customers see *Contact us for pricing*; choosing it shows a short form (users, companies, modules, phone, notes) that arrives in Tickets as an *Enterprise plan request*. You then set the company up with **Enterprise deal** on its page.

### Looking after customers' data

* **Archive** keeps everything and makes the company read only. It leaves the main lists (use the "Show" filter to see archived ones). It can be undone any time.
* **Download backup** (full-access owners) saves one file with the company's books, people, billing and payments. Keep it safe: it contains the customer's data.
* **Delete for good** needs all of: the company archived first, a backup taken in the last 24 hours, and its name typed. Its payments stay in *All payments* under its name, so revenue figures do not change.
* **Bring back**: Deleted companies → choose the backup file.

### What customers see

* Plans as you designed them, the modules, AI assistant and online support each one includes, and add-ons they can tick per company (modules sold on their own, the AI assistant, online support). Your texts are shown above and below.
* Modules their plan does not include, and screens or tabs switched off for the company, disappear from the menu (and from their Excel workbook and templates). A screen switched off for the company says so when opened directly.
* Opening a module not in the plan directly says so, with an "Add it to the subscription" button for administrators; their data stays.
* Banners for maintenance, suspension and archiving.
* **Help and support**: tickets (when online support is included; questions about the subscription are always allowed) and **Ask for a visit**. For a visit, they accept or decline your price, pay with a transfer screenshot, and follow its status.

### What is enforced where

* Billing, read only (unpaid, ended, suspended, archived, maintenance), user limits, company limits (per plan and per person), user and notification permissions the plan leaves out, new customers, blocking a person, online support, visits and the AI assistant are refused by the database or the server functions. Changing the page does not get around them.
* Website texts are read by anyone (they are what the public pages show) and changed only by the owner or a team member given *Website texts*; every save is in Activity. The search-engine title and description of the site are part of the website build, not these texts.
* Modules, screens and tabs are hidden and refused by the app itself. Someone with technical skills could still reach a hidden module's screens in their own browser, but nothing outside their plan is offered to them.

## The support desk

The support team works in its own page, **support.html** (the 🎧 Support desk link in the ERP's user menu and in the owner console). It uses the same sign-in. A team member who only has the **Support** permission (owner console → Team) sees only the support pages there.

* **Queue.** Tickets waiting for the team come first, then the plan's **support priority**, then whoever has waited longest. Set each plan's priority on Plans: a higher number is answered first. You can also set an optional **first reply within (hours)**: the queue then shows when the reply is due and highlights tickets past it. When this was added, each plan got a priority from its price: the cheapest 0, each bigger plan one more, and "contact us" plans on top. Companies on a trial or without a paid plan have priority 0.
* **Ticket.** Shows the conversation, the customer's card (plan, priority, status, people, earlier tickets and visits, with no payment details), the technical details and what happened before the report. The team can:
  * reply with text, files or a 🎤 voice note;
  * pick a **saved reply**, where `{name}` becomes the customer's first name;
  * write internal notes the customer never sees;
  * assign the ticket;
  * arrange a visit.
* **Live chat.** The team's first answer opens a chat at the bottom corner of the customer's screen. Customers see the team member's first name, never their email. Both sides can send messages, pictures, videos, files and voice notes; customers on a computer can also send a screen recording. New messages appear by themselves: about every 5 seconds in the support desk, and every 4 seconds in the customer's open chat (25 when it is closed). **Only the team ends the chat** (⏹ End the chat), optionally marking the ticket solved. The customer then sees that the chat ended and can reopen the ticket. Writing again later starts the chat again.

### Help & FAQ, bug reports and shared solutions

* **Help & FAQ** (app user menu → 📖 Help & FAQ): questions by category in both languages, the problems the team has solved and shared, and a search across both. The report form suggests matching answers while the person types the summary.
* **Managing it** (support desk → Help center): add and order categories and questions (English and Arabic, published or draft).
* **Bug reports** (support desk → Bug reports): every report of the kind "Problem or bug", with the errors recorded before it, the version and the screen. Each one goes through the queue like any ticket.
* **Closing** (✓ Close… on a ticket): a ticket cannot be closed before the customer has had an answer. If the team hasn't replied yet, the reply is written in the same form. The team chooses whether to **share** the solution:
  * Only the title and the resolution written in that form are published, in a separate list.
  * Nothing about the customer is copied: no name, company, message or file.
  * Shared solutions can be edited or hidden at any time (Help center → Shared solutions).

### Support access to a customer's books

* **The customer decides.** A company administrator lets the team member answering their ticket in: 🔑 in the chat, or on the ticket page. They choose **View only** or **View and fix**, for 1 hour to 3 days, and can end it at any time. Nobody on the platform team can let themselves in.
* **For the team member:** the company appears in the ERP's company list with a banner saying what they may do and until when. They don't become a member: they take no user seat and aren't listed among the company's people. When the time is up, or the customer ends it, they are out at once. The server refuses their changes after that, and refuses all changes while access is view only.
* **Signed changes:** the customer's app adds a user "Name · BeyondERP Support" to the books the first time; adding it takes no seat. Every change the team member makes is a normal entry, through the same screens and rules as anyone's, signed with their name in the audit trail and the shared change log, and reversible the same way. The user stays in the books for next time, with no access between grants; a super administrator can deactivate it.

### What each support team member may do

Owner console → **Team** → a person → **On the support desk** (only the owner, with full access, changes this; every change is in the Activity log). Pick a role, then switch anything on or off:

| Role | May do |
|---|---|
| **Shadow** | Read tickets and write internal notes. Nothing the customer sees; customers' emails are hidden. |
| **Agent** | Reply and chat; notes; status, priority, assignee and ending chats; close and share solutions; enter books when the customer allows it; see customer contacts; see bug reports |
| **Lead** | All of the above, plus editing saved replies and Help & FAQ, and support visits |

The ten switches are: reply to customers, internal notes, manage (status, priority, assignee, end chats), close and share, customers' books, customer contacts, saved replies, Help & FAQ, bug reports, visits. **Access ends** (optional) stops the person's access at that moment, for someone shadowing or helping for a while. Team members added before these switches keep everything they had. The database enforces each switch; hiding a button is only the visible part. Someone without "customer contacts" sees addresses as `b•••@o•••` everywhere on the desk.

### Emailing customers (maintenance, changes)

**Announcements** (owner console, and the support desk for team members with the Announcements permission) → **✉ Email customers…**, or **Send by email…** on an announcement.

1. Write the subject and message. Arabic is optional and is sent below the English in the same email.
2. Choose who: companies (everyone, on trial, paying, not paying), optionally only some plans, then everyone in them or administrators only. The panel shows how many people that is.
3. **Send me a test** sends it to your own address only. **Send…** asks once more, with the number, and then sends.

* Each person gets their own email: nobody sees anyone else's address. Replies go to info@mybeyonderp.com.
* People in several companies get it once. Deleted and archived companies, and people not yet approved, are left out.
* Sending continues while the console is open, with the progress at the bottom of the screen. **Emails sent** lists every email with sent, failed and waiting counts; open one to stop, resume, or retry the addresses that failed (each with the reason).
* Nothing is sent automatically. Use it for service news, not advertising (that needs people's consent; see the plan for the newsletter).
* The email service's key stays on the server. Setting it up: [EMAIL_SETUP.md](EMAIL_SETUP.md).

## Referral program and upgrades

Every signed-in user of the online workspace has a personal share link (`https://mybeyonderp.com/?ref=CODE`) on **🎁 Referral program** in the user menu. The share button uses the same link.

**The rules (set in the console under Referrals; defaults shown):**

| Who | Discount | When |
|---|---|---|
| Invited person | 20 % | Their first paid subscription, once |
| Person who shared | 20 % | Their own first subscription, if they have not paid yet |
| Person who shared | 30 % | An upgrade while subscribed (a bigger plan, more modules, AI, support or users) |

* A link only links someone who is new: not a paying customer, not a member of the sharer's companies, and not the sharer.
* The sharer earns one reward per invited person, only when that person's **first payment is approved**. Signing up alone earns nothing.
* Discounts never add up. One reward is used per payment, and if a company already has a bigger standing discount, that one applies and the reward waits.
* Plain renewals keep rewards for later.
* The sharer is notified (push and in-app) when a reward is earned; the page shows sign-ups, subscribers, rewards ready and used, and each invited person with a masked email.
* The console can switch the program off, change the percentages, and void or restore any referral.

**Upgrades (the basket):** on Subscription → Upgrade, a customer with an active paid period picks a bigger plan, extra users or more add-ons for each company. They pay only the difference in the monthly price for the days left in the paid period (`difference × days left ÷ 30`, at the yearly rate if they paid yearly). The paid-until date does not change; from the next renewal they pay the new monthly price. The upgrade is applied when the console approves the payment, like any order.

## Pricing (October 2026)

### Why the first prices were too low

The first list (690 / 1,490 / 2,990 EGP a month, 9,900–45,000 EGP desktop) was set against the wrong products.

* **It was anchored to invoicing apps.** Daftra, Qoyod and Zoho sell invoices plus a ledger to shops and freelancers. BEYOND ERP runs a contractor's projects, BOQs and progress billing, equipment rental, real-estate installments, ETA and the full statements. Its real alternatives are Odoo with a partner, a custom-built system, or a local construction ERP.
* **It priced the software, not the result.** A contractor running 3–5 projects loses far more than the subscription to one missed variation, one unbilled rental month or one ETA penalty. Prices should be a small share of the value delivered, not of the cost to host it.
* **It ignored the real competitor's total cost.** In Egypt, Odoo partners quote 125,000–350,000 EGP to implement a small company (more for 20+ users), plus about 710 EGP per user per month. BEYOND ERP needs almost no implementation: it ships with the Egyptian chart of accounts, ETA, the Excel workbook and a demo company. That saving is worth charging for.
* **It gave away services.** Setup, migration from Excel and training were small optional extras. In this market they are often half the revenue, and the main reason customers succeed and stay.
* **Low prices read as low quality.** Finance managers and owners buying an ERP for a contracting company distrust a 690 EGP system. A price close to Daftra's puts the product in the same mental box as Daftra.

### What the market charges

| Product | Price | Notes |
|---|---|---|
| Daftra | 99–799 EGP / month | Invoicing and accounting |
| Optify Flex | 414–1,498 EGP / month (launch offer) | SME ERP |
| Hunt ERP | from 1,000 EGP / month; enterprise on quote | Egyptian ERP |
| Odoo (Egypt price list) | ≈ 475–710 EGP per user / month | Plus implementation of 125,000–350,000 EGP for a small company, 250,000–400,000 EGP for 20–100 users |

### Cloud plans (new defaults)

| Plan | Per month, per company | Users per company | For |
|---|---|---|---|
| Essentials | 1,900 EGP | 3 | Offices and traders: accounting, sales, purchasing, ETA, statements of account |
| **Business** (the anchor) | **4,500 EGP** | 8 | Contractors and fit-out firms: projects, BOQs, progress invoices, stock, POs, budgets, assets, loans, custody, bank reconciliation |
| Pro | 8,900 EGP | 20 | Groups, rental, showroom and real-estate businesses; priority support |
| Enterprise | from 15,000 EGP | 40+ | Dedicated onboarding, SLA, custom reports and integrations |

* Extra user: 350 EGP a month, for the company it is added to.
* Each further company in the same order: the share of the plan price set in the console (default 100 %; lower it to reward groups).
* Yearly: pay 10 months, get 12. Push annual contracts: they cut churn and fund growth.
* Eight Business users cost about 5,700 EGP a month on Odoo before any implementation. 4,500 EGP is still clearly cheaper, with the construction features built in.
* Launch offer: first 20 companies get 30 % off the first year in exchange for a case study or reference. This keeps the list price intact.

The new defaults are applied by `supabase/migrations/20261005000000_billing_prices.sql`. They only replace the old defaults when billing is still off and nobody has edited the prices. Prices you already set yourself are kept, and you can change them any time in Owner console → Plans and settings → Plans. Since the owner console, plans no longer carry a company count; the shipped Pro feature line "Up to 3 companies, …" is reduced to its support part.

### Offline (desktop) edition — one-time licence

| Licence | One-time | Users | Companies |
|---|---|---|---|
| Desktop Essentials | 35,000 EGP | 3 | 1 |
| Desktop Business (office network) | 85,000 EGP | 8 | 1 |
| Desktop Pro | 160,000 EGP | 20 | 3 |
| Desktop Enterprise | from 300,000 EGP | 40+ | any |

* **Pricing rule:** a licence costs about 1.5–2 years of the cloud plan, because the customer keeps it for good.
* **Maintenance:** 20 % of the licence every year, after the first 12 months, which are included. It covers updates, ETA changes and support. Licences carry `updates_until`, so a new version refuses to unlock without maintenance and the old one keeps working. That turns one-time sales into recurring income.
* **Extra user:** 6,000 EGP, one-time.
* **Issuing licences:** `python run.py licence-issue --key <private key> --company "…" --edition business --users 8 --updates-months 12 --install XXXX-XXXX-XXXX-XXXX`.

### Services (sold with every deal)

| Package | Price | Includes |
|---|---|---|
| Quick start | 15,000 EGP | Company setup, chart of accounts, opening balances from the Excel workbook, 1 day of training |
| Standard onboarding | 40,000 EGP | Everything in Quick start + migration of 1 year of history, projects/BOQs, users and permissions, 3 training days, 30 days of hand-holding |
| Full implementation | 75,000 EGP and up | Several companies or branches, rental and real-estate setup, custom reports and documents, on-site go-live |
| Training | 5,000 EGP per day | On site or online |
| Custom work | 1,200 EGP per hour | Reports, integrations, custom documents |
| Priority support / SLA | 20 % of the subscription | Response within 4 working hours, named contact |

### How to make it worth more

1. **Sell a vertical.** Market it as "the ERP for contractors, scaffolding / equipment rental and real-estate developers in Egypt", not as accounting software. Lead with the BOQ → progress invoice → project profit flow, the scaffold calculator and installments.
2. **Sell outcomes, with numbers.** Examples: "know each project's profit every day", "never miss a rental invoice", "ETA-ready from day one". Track before/after figures with the first customers and publish them as case studies.
3. **Make onboarding part of the product.** The Excel workbook, demo company and import kit make go-live fast. Package them as the paid onboarding service above, instead of giving them away.
4. **Price add-ons separately once customers depend on them.** For example: rental, showroom & installments, service subscriptions, extra companies, attachments storage, and later bank feeds, WhatsApp reminders or mobile apps for site engineers.
5. **Offer annual contracts and an SLA.** Larger firms want a phone number and a response time. Support should be a product, not a favour.
6. **Use partners.** Accountants and audit firms who set it up for their clients should earn 20 % of the first year's subscription. They are the cheapest sales channel in Egypt.
7. **Raise prices with the product.** Re-price every year for new customers. Existing customers keep their price for 12 months, which is also an argument to sign annually.
