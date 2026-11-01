-- R5: what each support team member may do, access that ends by itself, and announcements sent by email.
--
-- Support permissions: a team member with the "support" area sees the desk and its tickets. What they may do there is
-- perms.desk = {reply, notes, manage, close, books, contacts, replies, help, bugs, visits} (true/false). Without
-- perms.desk (everyone added before this) they may do all of it, as before. A "shadow" reads tickets and writes
-- internal notes only. platform_admins.until ends a team member's access at that time (never for full access).
-- Team members who may not see customer contacts get their email addresses hidden in every support list.
--
-- Announcement emails: the platform team picks who (by status, plan, administrators or everyone), sees the count,
-- sends a test to themselves, then sends. Each person gets their own email (no one sees the others). Who was sent
-- what is kept; a send can be stopped, resumed, and failed addresses retried. The "mail" function does the sending
-- with the email service's key, which stays on the server.

alter table public.platform_admins add column if not exists until timestamptz;

create or replace function public.beyond_staff() returns public.platform_admins
language sql stable security definer set search_path = public as $$
  select * from platform_admins where public.beyond_caller() <> '' and lower(email) = public.beyond_caller() and active
     and (perms is null or until is null or until > now())
$$;

create or replace function public.beyond_desk_allows(p_perms jsonb, p_action text) returns boolean
language sql immutable as $$
  select p_perms is null or jsonb_typeof(p_perms->'desk') is distinct from 'object' or coalesce((p_perms->'desk'->>p_action)::boolean, false)
$$;

create or replace function public.beyond_desk_can(p_action text) returns boolean
language plpgsql stable security definer set search_path = public as $$
begin
  return public.beyond_staff_can('support') and public.beyond_desk_allows((public.beyond_staff()).perms, p_action);
end $$;

create or replace function public.beyond_require_desk(p_action text) returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if not public.beyond_desk_can(p_action) then
    raise exception 'your team account is not allowed to do this (support: %)', p_action using errcode = '42501';
  end if;
end $$;

-- an email address as a team member sees it: whole, or hidden when they may not see customer contacts
create or replace function public.beyond_desk_email(p_email text) returns text
language plpgsql stable security definer set search_path = public as $$
begin
  if p_email is null or p_email = '' or public.beyond_desk_can('contacts') then return p_email; end if;
  return left(split_part(p_email, '@', 1), 1) || '•••@' || left(split_part(p_email, '@', 2), 1) || '•••';
end $$;

create or replace function public.console_me() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  return jsonb_build_object('email', s.email, 'name', coalesce(s.name, split_part(s.email, '@', 1)), 'full', s.perms is null,
    'perms', coalesce(s.perms, '{}'::jsonb), 'notify', coalesce(s.notify, '{}'::jsonb), 'until', s.until,
    'desk', (select jsonb_object_agg(k, public.beyond_staff_can('support') and public.beyond_desk_allows(s.perms, k))
               from unnest(array['reply', 'notes', 'manage', 'close', 'books', 'contacts', 'replies', 'help', 'bugs', 'visits']) k),
    'look', (select value from platform_settings where key = 'console_look'),
    'layout', (select value from platform_settings where key = 'console_layout'),
    'billing_enabled', (select enabled from billing_config where id = 1),
    'badges', jsonb_build_object(
      'orders', case when public.beyond_staff_can('billing') then (select count(*) from billing_orders where status = 'submitted') end,
      'tickets', case when public.beyond_staff_can('support') then (select count(*) from support_tickets where staff_unread and status not in ('closed')) end,
      'visits', case when public.beyond_desk_can('visits') then (select count(*) from support_visits where staff_unread and status not in ('done', 'cancelled', 'rejected')) end));
end $$;

-- the team: only full access manages it; at least one active full-access person remains; full access never ends by itself
create or replace function public.console_staff_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p->>'email', ''))); is_full boolean := coalesce((p->>'full')::boolean, false);
        np jsonb := case when is_full then null else coalesce(p->'perms', '{}'::jsonb) end; til timestamptz := nullif(p->>'until', '')::timestamptz;
begin
  perform public.beyond_require_owner();
  if e !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then raise exception 'enter the person''s sign-in email'; end if;
  if np is not null and jsonb_typeof(np) <> 'object' then raise exception 'permissions must be a list of switches'; end if;
  if np ? 'desk' then
    if jsonb_typeof(np->'desk') <> 'object' or exists (select 1 from jsonb_each(np->'desk') d
        where d.key not in ('reply', 'notes', 'manage', 'close', 'books', 'contacts', 'replies', 'help', 'bugs', 'visits') or jsonb_typeof(d.value) <> 'boolean') then
      raise exception 'unknown support permission';
    end if;
  end if;
  if is_full then til := null; end if;
  if til is not null and til <= now() then raise exception 'the end of access must be in the future'; end if;
  if coalesce((p->>'delete')::boolean, false) then
    delete from platform_admins where lower(email) = e;
  else
    insert into platform_admins(email, name, perms, active, added_by, until)
    values (e, nullif(trim(coalesce(p->>'name', '')), ''), np, coalesce((p->>'active')::boolean, true), public.beyond_caller(), til)
    on conflict (email) do update set name = excluded.name, perms = excluded.perms, active = excluded.active, until = excluded.until;
  end if;
  if not exists (select 1 from platform_admins where perms is null and active) then
    raise exception 'at least one person must keep full access';
  end if;
  perform public.beyond_log('staff.save', e, p - 'email');
  return public.console_staff();
end $$;

-- ================================================================== announcement emails
create table if not exists public.platform_mailings (
  id          bigserial primary key,
  subject     text not null,
  subject_ar  text,
  body        text not null,
  body_ar     text,
  audience    jsonb not null default '{}'::jsonb,   -- {status, plans, who}
  notice_id   bigint,
  status      text not null default 'sending' check (status in ('sending', 'stopped', 'done')),
  total       int not null default 0,
  sent        int not null default 0,
  failed      int not null default 0,
  created_by  text,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
alter table public.platform_mailings enable row level security;
revoke all on public.platform_mailings from anon, authenticated;

create table if not exists public.platform_mail_rcpt (
  mailing_id bigint not null references public.platform_mailings(id) on delete cascade,
  email      text not null,
  company_id uuid,
  status     text not null default 'queued' check (status in ('queued', 'sending', 'sent', 'failed')),
  error      text,
  claimed_at timestamptz,
  sent_at    timestamptz,
  primary key (mailing_id, email)
);
create index if not exists platform_mail_rcpt_q on public.platform_mail_rcpt(mailing_id, status);
alter table public.platform_mail_rcpt enable row level security;
revoke all on public.platform_mail_rcpt from anon, authenticated;

-- the people an announcement email goes to: active people of companies in that state (deleted and archived ones never)
create or replace function public.beyond_mail_targets(p jsonb) returns table(email text, company_id uuid)
language plpgsql stable security definer set search_path = public as $$
declare st text := coalesce(nullif(p->>'status', ''), 'all'); who text := coalesce(nullif(p->>'who', ''), 'everyone');
        plans text[] := array(select jsonb_array_elements_text(case when jsonb_typeof(p->'plans') = 'array' then p->'plans' else '[]'::jsonb end));
begin
  if st not in ('all', 'trial', 'paying', 'unpaid') then raise exception 'choose who receives it'; end if;
  if who not in ('everyone', 'admins') then raise exception 'choose administrators or everyone'; end if;
  return query
    select distinct on (lower(m.email)) lower(m.email), m.company_id
      from members m join companies c on c.id = m.company_id
      cross join lateral (select public.beyond_billing_state(c.id) b) s
     where m.active and not m.pending and not m.gateway and m.email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' and (who = 'everyone' or m.is_admin)
       and coalesce(s.b->>'status', '') not in ('archived')
       and (st = 'all' or (st = 'trial' and s.b->>'billing_status' = 'trial')
            or (st = 'paying' and s.b->>'billing_status' in ('active', 'past_due', 'comped'))
            or (st = 'unpaid' and s.b->>'billing_status' in ('unpaid', 'expired')))
       and (cardinality(plans) = 0 or s.b->>'plan' = any(plans))
     order by lower(m.email), m.is_admin desc;
end $$;

create or replace function public.console_mail_audience(p jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('notices');
  return (select jsonb_build_object('people', count(*), 'companies', count(distinct company_id)) from public.beyond_mail_targets(p));
end $$;

create or replace function public.console_mail_create(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare mid bigint; n int;
begin
  perform public.beyond_require_staff('notices');
  if coalesce(trim(p->>'subject'), '') = '' or coalesce(trim(p->>'body'), '') = '' then raise exception 'write the subject and the message'; end if;
  if length(p->>'subject') > 200 or length(coalesce(p->>'subject_ar', '')) > 200 then raise exception 'keep the subject under 200 characters'; end if;
  if length(p->>'body') > 20000 or length(coalesce(p->>'body_ar', '')) > 20000 then raise exception 'keep the message under 20,000 characters'; end if;
  if (select count(*) from platform_mailings where created_at > now() - interval '1 hour') >= 10 then
    raise exception 'too many emails sent in the last hour — wait a little';
  end if;
  insert into platform_mailings(subject, subject_ar, body, body_ar, audience, notice_id, created_by)
  values (trim(p->>'subject'), nullif(trim(coalesce(p->>'subject_ar', '')), ''), p->>'body', nullif(trim(coalesce(p->>'body_ar', '')), ''),
          jsonb_build_object('status', coalesce(nullif(p->'audience'->>'status', ''), 'all'), 'who', coalesce(nullif(p->'audience'->>'who', ''), 'everyone'),
                             'plans', case when jsonb_typeof(p->'audience'->'plans') = 'array' then p->'audience'->'plans' else '[]'::jsonb end),
          nullif(p->>'notice_id', '')::bigint, public.beyond_caller())
  returning id into mid;
  insert into platform_mail_rcpt(mailing_id, email, company_id)
  select mid, t.email, t.company_id from public.beyond_mail_targets((select audience from platform_mailings where id = mid)) t;
  get diagnostics n = row_count;
  if n = 0 then raise exception 'no one matches — choose other people'; end if;
  update platform_mailings set total = n where id = mid;
  perform public.beyond_log('mail.create', p->>'subject', jsonb_build_object('id', mid, 'people', n, 'audience', p->'audience'));
  return jsonb_build_object('id', mid, 'total', n);
end $$;

-- the next people to send to (the "mail" function asks, as the team member sending); stuck claims return after 10 minutes
create or replace function public.console_mail_next(p_mailing bigint, p_n int) returns jsonb
language plpgsql security definer set search_path = public as $$
declare m platform_mailings; who jsonb;
begin
  perform public.beyond_require_staff('notices');
  select * into m from platform_mailings where id = p_mailing;
  if m.id is null then raise exception 'email not found'; end if;
  if m.status <> 'sending' then return jsonb_build_object('to', '[]'::jsonb, 'status', m.status); end if;
  with c as (select r.email from platform_mail_rcpt r where r.mailing_id = p_mailing
               and (r.status = 'queued' or (r.status = 'sending' and r.claimed_at < now() - interval '10 minutes'))
             order by r.email limit greatest(1, least(coalesce(p_n, 20), 50)) for update skip locked),
       u as (update platform_mail_rcpt r set status = 'sending', claimed_at = now() from c where r.mailing_id = p_mailing and r.email = c.email returning r.email)
  select coalesce(jsonb_agg(u.email), '[]'::jsonb) into who from u;
  return jsonb_build_object('to', who, 'status', m.status, 'subject', m.subject, 'subject_ar', m.subject_ar, 'body', m.body, 'body_ar', m.body_ar);
end $$;

create or replace function public.beyond_mail_count(p_mailing bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  update platform_mailings set
    sent = (select count(*) from platform_mail_rcpt where mailing_id = p_mailing and status = 'sent'),
    failed = (select count(*) from platform_mail_rcpt where mailing_id = p_mailing and status = 'failed')
   where id = p_mailing;
  update platform_mailings set status = 'done', finished_at = now()
   where id = p_mailing and status = 'sending' and not exists (select 1 from platform_mail_rcpt where mailing_id = p_mailing and status in ('queued', 'sending'));
end $$;

create or replace function public.console_mail_done(p_mailing bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('notices');
  if jsonb_typeof(p) <> 'array' then raise exception 'results must be a list'; end if;
  update platform_mail_rcpt r set status = case when (x->>'ok')::boolean then 'sent' else 'failed' end,
         error = case when (x->>'ok')::boolean then null else left(coalesce(x->>'error', 'not sent'), 300) end,
         sent_at = case when (x->>'ok')::boolean then now() end
    from jsonb_array_elements(p) x
   where r.mailing_id = p_mailing and r.email = lower(x->>'email') and r.status = 'sending';
  perform public.beyond_mail_count(p_mailing);
  return (select to_jsonb(m) from platform_mailings m where id = p_mailing);
end $$;

-- stop, carry on, or try the failed addresses again
create or replace function public.console_mail_state(p_mailing bigint, p_state text) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('notices');
  if p_state = 'stop' then
    update platform_mail_rcpt set status = 'queued', claimed_at = null where mailing_id = p_mailing and status = 'sending';
    update platform_mailings set status = 'stopped' where id = p_mailing and status = 'sending';
  elsif p_state = 'resume' then
    update platform_mailings set status = 'sending', finished_at = null where id = p_mailing and status = 'stopped';
  elsif p_state = 'retry' then
    update platform_mail_rcpt set status = 'queued', error = null, claimed_at = null where mailing_id = p_mailing and status = 'failed';
    update platform_mailings set status = 'sending', finished_at = null where id = p_mailing;
  else raise exception 'unknown action';
  end if;
  perform public.beyond_mail_count(p_mailing);
  perform public.beyond_log('mail.' || p_state, p_mailing::text);
  return (select to_jsonb(m) from platform_mailings m where id = p_mailing);
end $$;

create or replace function public.console_mailings() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('notices');
  return (select coalesce(jsonb_agg(to_jsonb(m) || jsonb_build_object('failures',
            (select coalesce(jsonb_agg(jsonb_build_object('email', r.email, 'error', r.error)), '[]'::jsonb)
               from (select * from platform_mail_rcpt r where r.mailing_id = m.id and r.status = 'failed' order by r.email limit 50) r))
            order by m.created_at desc), '[]'::jsonb)
          from (select * from platform_mailings order by created_at desc limit 200) m);
end $$;

-- the caller's own address, for a test email (the "mail" function sends only there)
create or replace function public.console_mail_me() returns text
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('notices');
  return public.beyond_caller();
end $$;

-- ================================================================== support actions by permission (the functions as before, each now asking for its permission)
create or replace function public.console_ticket_set(p_ticket bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('manage');
  update support_tickets set
    status   = coalesce(nullif(p->>'status', ''), status),
    priority = coalesce(nullif(p->>'priority', ''), priority),
    assignee = case when p ? 'assignee' then nullif(p->>'assignee', '') else assignee end,
    customer_unread = customer_unread or (p ? 'status' and p->>'status' in ('resolved', 'closed')),
    updated_at = now()
  where id = p_ticket;
  if not found then raise exception 'ticket not found'; end if;
  perform public.beyond_log('ticket.set', (select ref from support_tickets where id = p_ticket), p);
  return public.support_get(p_ticket);
end $$;

create or replace function public.support_chat_end(p_ticket bigint, p_solved boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('manage');
  update support_tickets set chat = 'ended', chat_ended_at = now(), chat_ended_by = public.beyond_caller(),
         status = case when coalesce(p_solved, false) then 'resolved' else status end,
         waiting_since = case when coalesce(p_solved, false) then null else waiting_since end,
         customer_unread = true, updated_at = now()
   where id = p_ticket and chat = 'live';
  if not found then raise exception 'there is no live chat on this ticket'; end if;
  insert into support_messages(ticket_id, email, staff, kind, body)
  values (p_ticket, public.beyond_caller(), true, 'event', case when coalesce(p_solved, false) then 'chat_ended_solved' else 'chat_ended' end);
  perform public.beyond_log('ticket.chat_end', (select ref from support_tickets where id = p_ticket), jsonb_build_object('solved', coalesce(p_solved, false)));
  return public.support_get(p_ticket);
end $$;

create or replace function public.console_ticket_close(p_ticket bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare t support_tickets; share boolean := coalesce((p->>'share')::boolean, false);
begin
  perform public.beyond_require_desk('close');
  select * into t from support_tickets where id = p_ticket for update;
  if t.id is null then raise exception 'ticket not found'; end if;
  if share and (coalesce(trim(p->>'title'), '') = '' or coalesce(trim(p->>'resolution'), '') = '') then
    raise exception 'to share it, write a short title and how it was resolved (without anything about the customer)';
  end if;
  if coalesce(trim(p->>'reply'), '') = '' and not exists (select 1 from support_messages where ticket_id = p_ticket and staff and not internal and kind = 'msg') then
    raise exception 'answer the customer before closing — write the reply here or in the ticket';
  end if;
  if coalesce(trim(p->>'reply'), '') <> '' then
    insert into support_messages(ticket_id, email, staff, body) values (p_ticket, public.beyond_caller(), true, left(trim(p->>'reply'), 20000));
  end if;
  update support_tickets set status = 'closed', shared = share, waiting_since = null, staff_unread = false, customer_unread = true,
         chat = case when chat = 'live' then 'ended' else chat end, chat_ended_at = case when chat = 'live' then now() else chat_ended_at end,
         chat_ended_by = case when chat = 'live' then public.beyond_caller() else chat_ended_by end,
         first_reply_at = coalesce(first_reply_at, case when coalesce(trim(p->>'reply'), '') <> '' then now() end), updated_at = now()
   where id = p_ticket;
  if share then
    insert into help_solved(ticket_id, category, kind, title, title_ar, resolution, resolution_ar, created_by)
    values (p_ticket, nullif(p->>'category', ''), case when t.kind = 'bug' then 'bug' else 'issue' end, left(trim(p->>'title'), 200),
            left(trim(coalesce(p->>'title_ar', '')), 200), left(trim(p->>'resolution'), 5000), left(trim(coalesce(p->>'resolution_ar', '')), 5000),
            public.beyond_caller())
    on conflict (ticket_id) do update set category = excluded.category, title = excluded.title, title_ar = excluded.title_ar,
      resolution = excluded.resolution, resolution_ar = excluded.resolution_ar, published = true, updated_at = now();
  else
    update help_solved set published = false, updated_at = now() where ticket_id = p_ticket;
  end if;
  perform public.beyond_log('ticket.close', t.ref, jsonb_build_object('shared', share));
  return public.support_get(p_ticket);
end $$;

create or replace function public.console_ticket_customer(p_ticket bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare t support_tickets; st jsonb;
begin
  perform public.beyond_require_desk('contacts');
  select * into t from support_tickets where id = p_ticket;
  if t.id is null then raise exception 'ticket not found'; end if;
  st := case when t.company_id is not null then public.beyond_billing_state(t.company_id) end;
  return jsonb_build_object(
    'priority', public.beyond_support_rank(t.company_id),
    'company', case when t.company_id is not null then jsonb_build_object('id', t.company_id, 'name', (select name from companies where id = t.company_id),
       'status', st->>'billing_status', 'plan', st->>'plan', 'paid_until', st->>'paid_until', 'trial_ends', st->>'trial_ends', 'industry', st->>'industry',
       'users', (select count(*) from members where company_id = t.company_id and active and not pending),
       'admins', (select coalesce(jsonb_agg(email order by email), '[]'::jsonb) from members where company_id = t.company_id and active and not pending and is_admin)) end,
    'tickets', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'subject', subject, 'status', status, 'updated_at', updated_at) order by updated_at desc), '[]'::jsonb)
                from (select * from support_tickets x where x.id <> t.id and (lower(x.email) = lower(t.email) or (t.company_id is not null and x.company_id = t.company_id))
                      order by updated_at desc limit 10) o),
    'visits', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'status', status, 'scheduled_at', scheduled_at) order by created_at desc), '[]'::jsonb)
               from (select * from support_visits v where t.company_id is not null and v.company_id = t.company_id order by created_at desc limit 5) v));
end $$;

create or replace function public.console_replies_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('replies');
  if jsonb_typeof(p) <> 'array' or jsonb_array_length(p) > 200
     or exists (select 1 from jsonb_array_elements(p) r where jsonb_typeof(r) <> 'object' or coalesce(trim(r->>'title'), '') = ''
                or length(coalesce(r->>'body', '')) > 5000) then
    raise exception 'each saved reply needs a title and at most 5,000 characters';
  end if;
  insert into platform_settings(key, value, updated_by) values ('support_replies', p, public.beyond_caller())
  on conflict (key) do update set value = excluded.value, updated_by = excluded.updated_by, updated_at = now();
  perform public.beyond_log('support.replies', null, jsonb_build_object('count', jsonb_array_length(p)));
  return p;
end $$;

create or replace function public.console_help_category(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('help');
  if coalesce((p->>'delete')::boolean, false) then
    delete from help_categories where key = p->>'key';
  else
    if coalesce(trim(p->>'name'), '') = '' then raise exception 'give the category a name'; end if;
    insert into help_categories(key, name, name_ar, icon, sort)
    values (lower(trim(p->>'key')), trim(p->>'name'), trim(coalesce(p->>'name_ar', '')), left(coalesce(p->>'icon', ''), 8), coalesce((p->>'sort')::int, 0))
    on conflict (key) do update set name = excluded.name, name_ar = excluded.name_ar, icon = excluded.icon, sort = excluded.sort;
  end if;
  perform public.beyond_log('help.category', p->>'key', p);
  return public.console_help();
end $$;

create or replace function public.console_help_faq(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare fid bigint := nullif(p->>'id', '')::bigint;
begin
  perform public.beyond_require_desk('help');
  if coalesce((p->>'delete')::boolean, false) then
    delete from help_faq where id = fid;
  else
    if coalesce(trim(p->>'q'), '') = '' or coalesce(trim(p->>'a'), '') = '' then raise exception 'write the question and its answer'; end if;
    if length(p->>'a') > 20000 or length(coalesce(p->>'a_ar', '')) > 20000 then raise exception 'the answer is too long'; end if;
    if fid is null then
      insert into help_faq(category, q, q_ar, a, a_ar, sort, published, updated_by)
      values (nullif(p->>'category', ''), trim(p->>'q'), trim(coalesce(p->>'q_ar', '')), trim(p->>'a'), trim(coalesce(p->>'a_ar', '')),
              coalesce((p->>'sort')::int, 0), coalesce((p->>'published')::boolean, true), public.beyond_caller());
    else
      update help_faq set category = nullif(p->>'category', ''), q = trim(p->>'q'), q_ar = trim(coalesce(p->>'q_ar', '')), a = trim(p->>'a'),
             a_ar = trim(coalesce(p->>'a_ar', '')), sort = coalesce((p->>'sort')::int, sort), published = coalesce((p->>'published')::boolean, published),
             updated_by = public.beyond_caller(), updated_at = now() where id = fid;
    end if;
  end if;
  perform public.beyond_log('help.faq', coalesce(fid::text, 'new'), p - 'a' - 'a_ar');
  return public.console_help();
end $$;

create or replace function public.console_help_solved(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare sid bigint := (p->>'id')::bigint;
begin
  perform public.beyond_require_desk('help');
  if coalesce((p->>'delete')::boolean, false) then
    delete from help_solved where id = sid;
  else
    if coalesce(trim(p->>'title'), '') = '' or coalesce(trim(p->>'resolution'), '') = '' then raise exception 'write the title and the resolution'; end if;
    update help_solved set category = nullif(p->>'category', ''), title = left(trim(p->>'title'), 200), title_ar = left(trim(coalesce(p->>'title_ar', '')), 200),
           resolution = left(trim(p->>'resolution'), 5000), resolution_ar = left(trim(coalesce(p->>'resolution_ar', '')), 5000),
           published = coalesce((p->>'published')::boolean, published), updated_at = now() where id = sid;
  end if;
  perform public.beyond_log('help.solved', sid::text, jsonb_build_object('published', p->'published', 'delete', p->'delete'));
  return public.console_help();
end $$;

create or replace function public.console_bugs() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('bugs');
  return (select coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'ref', t.ref, 'subject', t.subject, 'company_name', t.company_name, 'email', public.beyond_desk_email(t.email),
            'status', t.status, 'assignee', t.assignee, 'shared', t.shared, 'created_at', t.created_at, 'updated_at', t.updated_at, 'unread', t.staff_unread,
            'version', t.diag->>'version', 'screen', t.diag->>'screen', 'files', jsonb_array_length(t.files),
            'errors', (select count(*) from jsonb_array_elements(t.log) e where e->>'t' = 'error'))
          order by t.created_at desc), '[]'::jsonb)
          from (select * from support_tickets where kind = 'bug' order by created_at desc limit 500) t);
end $$;

create or replace function public.console_visits(p_status text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('visits');
  return (select coalesce(jsonb_agg(public.beyond_visit_row(id, true) order by created_at desc), '[]'::jsonb)
          from (select id, created_at from support_visits where coalesce(p_status, '') = '' or status = p_status
                order by created_at desc limit 1000) x);
end $$;

create or replace function public.console_visit_set(p_visit bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v support_visits;
begin
  perform public.beyond_require_desk('visits');
  select * into v from support_visits where id = p_visit for update;
  if v.id is null then raise exception 'visit not found'; end if;
  if p ? 'status' and p->>'status' not in ('requested', 'quoted', 'accepted', 'scheduled', 'done', 'declined', 'cancelled', 'rejected') then
    raise exception 'unknown status';
  end if;
  if p->>'status' = 'rejected' and coalesce(trim(p->>'note'), '') = '' then raise exception 'write the reason (the customer sees it)'; end if;
  if p ? 'pay_status' and p->>'pay_status' not in ('none', 'submitted', 'paid', 'waived') then raise exception 'unknown payment state'; end if;
  if p ? 'price' and nullif(p->>'price', '') is not null and (p->>'price')::numeric < 0 then raise exception 'the price must be 0 or more'; end if;
  if p ? 'pay_status' and p->>'pay_status' = 'paid' and not public.beyond_staff_can('approve') then
    raise exception 'confirming a payment needs the approve permission' using errcode = '42501';
  end if;
  update support_visits set
    price        = case when p ? 'price' then nullif(p->>'price', '')::numeric else price end,
    pay_when     = coalesce(nullif(p->>'pay_when', ''), pay_when),
    scheduled_at = case when p ? 'scheduled_at' then nullif(p->>'scheduled_at', '')::timestamptz else scheduled_at end,
    technician   = case when p ? 'technician' then nullif(trim(p->>'technician'), '') else technician end,
    note         = case when p ? 'note' then nullif(trim(p->>'note'), '') else note end,
    staff_note   = case when p ? 'staff_note' then nullif(trim(p->>'staff_note'), '') else staff_note end,
    status       = coalesce(nullif(p->>'status', ''), status),
    pay_status   = coalesce(nullif(p->>'pay_status', ''), pay_status),
    paid_at      = case when p->>'pay_status' = 'paid' then now() else paid_at end,
    customer_unread = customer_unread or p ? 'status' or p ? 'price' or p ? 'scheduled_at' or p ? 'note' or p ? 'pay_status',
    staff_unread = false, updated_at = now()
  where id = p_visit;
  perform public.beyond_log('visit.set', v.ref, p);
  return public.beyond_visit_row(p_visit, true);
end $$;

create or replace function public.beyond_visit_access(p_visit bigint) returns text
language plpgsql stable security definer set search_path = public as $$
declare v support_visits;
begin
  select * into v from support_visits where id = p_visit;
  if v.id is null then return null; end if;
  if public.beyond_desk_can('visits') then return 'staff'; end if;
  if lower(v.email) = public.beyond_caller() then return 'customer'; end if;
  if v.company_id is not null and exists (select 1 from members where company_id = v.company_id and lower(email) = public.beyond_caller()
                                          and active and not pending and is_admin) then return 'customer'; end if;
  return null;
end $$;

create or replace function public.beyond_support_grant(p_company uuid) returns public.support_access
language sql stable security definer set search_path = public as $$
  select a.* from support_access a
   where a.company_id = p_company and lower(a.agent) = public.beyond_caller() and a.ended_at is null and a.expires_at > now()
     and public.beyond_desk_can('books')
   order by a.expires_at desc limit 1
$$;

create or replace function public.support_reply(p_ticket bigint, p_body text, p_files jsonb, p_internal boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a text; is_staff boolean;
begin
  a := public.beyond_ticket_access(p_ticket);
  if a is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  is_staff := a = 'staff';
  if is_staff then perform public.beyond_require_desk(case when coalesce(p_internal, false) then 'notes' else 'reply' end); end if;
  if coalesce(trim(p_body), '') = '' and jsonb_array_length(coalesce(p_files, '[]')) = 0 then raise exception 'write a message or attach a file'; end if;
  if not is_staff and (select status from support_tickets where id = p_ticket) = 'closed' then
    raise exception 'this ticket is closed — open a new one';
  end if;
  insert into support_messages(ticket_id, email, staff, internal, body, files)
  values (p_ticket, public.beyond_caller(), is_staff, is_staff and coalesce(p_internal, false), left(coalesce(p_body, ''), 20000),
          public.beyond_clean_files(p_files, case when is_staff then 'staff/' else public.beyond_caller() || '/' end));
  if is_staff and not coalesce(p_internal, false) then
    update support_tickets set status = case when status in ('resolved', 'closed') then status else 'answered' end,
           chat = 'live', chat_started_at = case when chat = 'live' then chat_started_at else now() end, chat_ended_at = null, chat_ended_by = null,
           first_reply_at = coalesce(first_reply_at, now()), waiting_since = null,
           assignee = coalesce(assignee, public.beyond_caller()), customer_unread = true, updated_at = now() where id = p_ticket;
  elsif not is_staff then
    update support_tickets set status = 'open', staff_unread = true, waiting_since = coalesce(waiting_since, now()), updated_at = now(),
           customer_seen = greatest(customer_seen, (select max(id) from support_messages where ticket_id = p_ticket))
     where id = p_ticket;
  end if;
  return public.support_get(p_ticket);
end $$;

create or replace function public.support_get(p_ticket bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a text; t jsonb;
begin
  a := public.beyond_ticket_access(p_ticket);
  if a is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  if a = 'staff' then update support_tickets set staff_unread = false where id = p_ticket and staff_unread;
  else update support_tickets set customer_unread = false,
         customer_seen = greatest(customer_seen, coalesce((select max(id) from support_messages where ticket_id = p_ticket), 0))
        where id = p_ticket; end if;
  select to_jsonb(s) - case when a = 'staff' then '' else 'assignee' end into t from support_tickets s where id = p_ticket;
  if a = 'staff' and not public.beyond_desk_can('contacts') then t := t || jsonb_build_object('email', public.beyond_desk_email(t->>'email'), 'diag', '{}'::jsonb); end if;
  return t || jsonb_build_object('as', a, 'messages',
    (select coalesce(jsonb_agg(case when a = 'staff' then to_jsonb(m) || jsonb_build_object('name', case when m.staff then public.beyond_staff_name(m.email) end, 'email', case when m.staff then m.email else public.beyond_desk_email(m.email) end)
                                    when m.staff then (to_jsonb(m) - 'email') || jsonb_build_object('email', '', 'name', public.beyond_staff_name(m.email))
                                    else to_jsonb(m) end order by m.id), '[]'::jsonb)
       from support_messages m where m.ticket_id = p_ticket and (a = 'staff' or not m.internal)));
end $$;

create or replace function public.console_queue() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return jsonb_build_object(
    'rows', (select coalesce(jsonb_agg(x order by (x->>'turn') = 'team' desc, (x->>'rank')::int desc, x->>'waiting_since' nulls last, x->>'updated_at' desc), '[]'::jsonb)
      from (select jsonb_build_object('id', t.id, 'ref', t.ref, 'company_id', t.company_id, 'company_name', t.company_name, 'email', public.beyond_desk_email(t.email),
              'app_user', t.app_user, 'kind', t.kind, 'subject', t.subject, 'priority', t.priority, 'status', t.status, 'assignee', t.assignee,
              'unread', t.staff_unread, 'chat', t.chat, 'created_at', t.created_at, 'updated_at', t.updated_at, 'first_reply_at', t.first_reply_at,
              'waiting_since', t.waiting_since, 'turn', case when t.status = 'open' then 'team' else 'customer' end,
              'files', jsonb_array_length(t.files), 'messages', (select count(*) from support_messages m where m.ticket_id = t.id and m.kind = 'msg'),
              'plan', r->>'plan', 'plan_name', r->>'plan_name', 'rank', coalesce((r->>'rank')::int, t.rank), 'reply_hours', r->'reply_hours',
              'due_at', case when t.first_reply_at is null and r ? 'reply_hours' and jsonb_typeof(r->'reply_hours') = 'number'
                             then t.created_at + make_interval(secs => (r->>'reply_hours')::numeric * 3600) end) x
            from (select t.*, public.beyond_support_rank(t.company_id) r from support_tickets t where t.status <> 'closed'
                  order by t.updated_at desc limit 500) t) q),
    'closed', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'company_name', company_name, 'email', public.beyond_desk_email(email), 'subject', subject,
                 'status', status, 'assignee', assignee, 'updated_at', updated_at, 'rank', rank, 'plan', plan) order by updated_at desc), '[]'::jsonb)
               from (select * from support_tickets where status = 'closed' order by updated_at desc limit 200) c),
    'replies', coalesce((select value from platform_settings where key = 'support_replies'), '[]'::jsonb),
    'team', (select coalesce(jsonb_agg(jsonb_build_object('email', email, 'name', name) order by name), '[]'::jsonb) from platform_admins
             where active and (perms is null or coalesce((perms->>'support')::boolean, false))));
end $$;

create or replace function public.console_tickets(p_status text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'company_id', company_id, 'company_name', company_name,
            'email', public.beyond_desk_email(email), 'app_user', app_user, 'kind', kind, 'subject', subject, 'priority', priority, 'status', status,
            'assignee', assignee, 'unread', staff_unread, 'files', jsonb_array_length(files), 'created_at', created_at, 'updated_at', updated_at,
            'messages', (select count(*) from support_messages m where m.ticket_id = t.id)) order by staff_unread desc, updated_at desc), '[]'::jsonb)
          from (select * from support_tickets where p_status = 'all' or (coalesce(p_status, '') = '' and status not in ('closed'))
                  or status = p_status order by updated_at desc limit 3000) t);
end $$;

create or replace function public.support_access_grant(p_ticket bigint, p_hours int, p_mode text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare t support_tickets; g support_access; ag text;
begin
  select * into t from support_tickets where id = p_ticket;
  if t.id is null or t.company_id is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  if not exists (select 1 from members where company_id = t.company_id and lower(email) = public.beyond_caller() and active and not pending and is_admin) then
    raise exception 'only an administrator of this company can let support in' using errcode = '42501';
  end if;
  if coalesce(p_mode, '') not in ('view', 'fix') then raise exception 'choose view only, or view and fix'; end if;
  if coalesce(p_hours, 0) not between 1 and 72 then raise exception 'choose between 1 and 72 hours'; end if;
  ag := lower(coalesce(t.assignee, ''));
  if ag = '' or not exists (select 1 from platform_admins where lower(email) = ag and active and (until is null or until > now() or perms is null)
                              and (perms is null or (coalesce((perms->>'support')::boolean, false) and public.beyond_desk_allows(perms, 'books')))) then
    raise exception 'wait until a team member answers this ticket, then allow them in';
  end if;
  update support_access set ended_at = now(), ended_by = public.beyond_caller()
   where company_id = t.company_id and lower(agent) = ag and ended_at is null;
  insert into support_access(company_id, ticket_id, agent, mode, granted_by, expires_at)
  values (t.company_id, t.id, ag, p_mode, public.beyond_caller(), now() + make_interval(hours => p_hours)) returning * into g;
  insert into support_messages(ticket_id, email, staff, kind, body)
  values (t.id, public.beyond_caller(), false, 'event', 'access_' || p_mode || ':' || to_char(g.expires_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'));
  update support_tickets set staff_unread = true, updated_at = now() where id = t.id;
  perform public.beyond_log('support.access', t.ref, jsonb_build_object('company', t.company_id, 'agent', ag, 'mode', p_mode, 'hours', p_hours));
  return public.beyond_access_row(g.id);
end $$;

revoke all on function public.beyond_desk_allows(jsonb, text), public.beyond_desk_can(text), public.beyond_require_desk(text), public.beyond_desk_email(text),
  public.beyond_mail_targets(jsonb), public.beyond_mail_count(bigint) from public, anon, authenticated;
revoke all on function public.console_mail_audience(jsonb), public.console_mail_create(jsonb), public.console_mail_next(bigint, int),
  public.console_mail_done(bigint, jsonb), public.console_mail_state(bigint, text), public.console_mailings(), public.console_mail_me() from public, anon;
grant execute on function public.console_mail_audience(jsonb), public.console_mail_create(jsonb), public.console_mail_next(bigint, int),
  public.console_mail_done(bigint, jsonb), public.console_mail_state(bigint, text), public.console_mailings(), public.console_mail_me() to authenticated;
