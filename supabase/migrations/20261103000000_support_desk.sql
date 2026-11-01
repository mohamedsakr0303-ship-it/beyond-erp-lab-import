-- Support desk: tickets queued by the plan's support priority, a live chat with the customer once the team answers,
-- the support console's customer card and saved replies.
--
--   * Each plan has a support priority (support_rank: higher is answered first) and, optionally, a first-reply target
--     in hours (reply_hours). Companies on a trial or without a paid plan have priority 0.
--   * The queue: tickets waiting for the team first, then the higher priority, then whoever has waited longest.
--   * When the team answers (not an internal note), the ticket becomes a live chat that opens at the bottom right of the
--     customer's screen. Only the team ends it ("chat ended", optionally marking the ticket solved).
--   * Customers see the team member's first name, never their email.

-- ================================================================== priority per plan
alter table public.support_tickets add column if not exists plan text;
alter table public.support_tickets add column if not exists rank int not null default 0;
alter table public.support_tickets add column if not exists chat text not null default 'none';
alter table public.support_tickets add column if not exists chat_started_at timestamptz;
alter table public.support_tickets add column if not exists chat_ended_at timestamptz;
alter table public.support_tickets add column if not exists chat_ended_by text;
alter table public.support_tickets add column if not exists first_reply_at timestamptz;
alter table public.support_tickets add column if not exists waiting_since timestamptz;
alter table public.support_tickets add column if not exists customer_seen bigint not null default 0;
do $$ begin
  alter table public.support_tickets add constraint support_tickets_chat check (chat in ('none', 'live', 'ended'));
exception when duplicate_object then null; end $$;
alter table public.support_messages add column if not exists kind text not null default 'msg';     -- msg | event
update public.support_tickets set waiting_since = coalesce(waiting_since, updated_at) where status = 'open' and waiting_since is null;
create index if not exists support_tickets_queue on public.support_tickets(status, rank desc, waiting_since);

-- first plans get a priority from their price (the cheapest 0, each bigger plan one more, "contact us" plans on top);
-- the owner changes it on Plans
update public.billing_config c
   set plans = (select jsonb_agg(case when p ? 'support_rank' then p else p || jsonb_build_object('support_rank',
                  case when coalesce((p->>'contact')::boolean, false)
                       then (select count(*) from jsonb_array_elements(c.plans) q where not coalesce((q->>'contact')::boolean, false))
                       else (select count(*) from jsonb_array_elements(c.plans) q where not coalesce((q->>'contact')::boolean, false)
                               and coalesce((q->>'price')::numeric, 0) < coalesce((p->>'price')::numeric, 0)) end) end order by k)
                from jsonb_array_elements(c.plans) with ordinality s(p, k))
 where id = 1 and jsonb_typeof(plans) = 'array' and jsonb_array_length(plans) > 0;

-- a company's support priority now: {plan, plan_name, rank, reply_hours}
create or replace function public.beyond_support_rank(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare st jsonb; pl jsonb;
begin
  if p_company is null then return jsonb_build_object('plan', null, 'rank', 0); end if;
  st := public.beyond_billing_state(p_company);
  if coalesce(st->>'billing_status', '') <> 'active' then return jsonb_build_object('plan', st->>'plan', 'plan_name', null, 'rank', 0, 'trial', true); end if;
  pl := public.beyond_plan(st->>'plan');
  return jsonb_build_object('plan', st->>'plan', 'plan_name', pl->>'name',
    'rank', case when jsonb_typeof(pl->'support_rank') = 'number' then (pl->>'support_rank')::int else 0 end,
    'reply_hours', case when jsonb_typeof(pl->'reply_hours') = 'number' and (pl->>'reply_hours')::numeric > 0 then (pl->>'reply_hours')::numeric end);
end $$;

-- the team member's first name as customers see it
create or replace function public.beyond_staff_name(p_email text) returns text
language sql stable security definer set search_path = public as $$
  select coalesce(nullif(split_part(trim(coalesce((select name from platform_admins where lower(email) = lower(p_email)), '')), ' ', 1), ''), 'BeyondERP')
$$;

-- ================================================================== opening, reading, replying
create or replace function public.support_open(p_company uuid, p_kind text, p_subject text, p_body text, p_diag jsonb,
  p_log jsonb, p_files jsonb, p_app_user text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare tid bigint; nm text; rk jsonb;
begin
  if public.beyond_caller() = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  if p_company is not null and not public.beyond_in(p_company) then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if coalesce(trim(p_subject), '') = '' then raise exception 'write a short summary'; end if;
  if p_company is not null and coalesce(p_kind, '') not in ('billing', 'enterprise')
     and not coalesce((public.beyond_billing_state(p_company)->>'support')::boolean, true) then
    raise exception 'support_not_included: online support is not included in this company''s plan — an administrator can add it in Subscription. Questions about the subscription itself are always welcome (choose "Subscription and billing").'
      using errcode = 'P0402';
  end if;
  if (select count(*) from support_tickets where lower(email) = public.beyond_caller() and created_at > now() - interval '1 hour') >= 20 then
    raise exception 'too many reports in one hour — add to an open one instead';
  end if;
  select name into nm from companies where id = p_company;
  rk := public.beyond_support_rank(p_company);
  insert into support_tickets(company_id, company_name, email, app_user, kind, subject, body, diag, log, files, plan, rank, waiting_since)
  values (p_company, nm, public.beyond_caller(), left(coalesce(p_app_user, ''), 120), left(coalesce(p_kind, 'issue'), 30),
          left(trim(p_subject), 200), left(coalesce(p_body, ''), 20000),
          case when jsonb_typeof(p_diag) = 'object' and length(p_diag::text) < 20000 then p_diag else '{}' end,
          case when jsonb_typeof(p_log) = 'array' and length(p_log::text) < 200000 then p_log else '[]' end,
          public.beyond_clean_files(p_files, public.beyond_caller() || '/'), rk->>'plan', (rk->>'rank')::int, now())
  returning id into tid;
  update support_tickets set ref = 'BE-' || lpad(tid::text, 5, '0') where id = tid;
  return public.support_get(tid);
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
  return t || jsonb_build_object('as', a, 'messages',
    (select coalesce(jsonb_agg(case when a = 'staff' then to_jsonb(m) || jsonb_build_object('name', case when m.staff then public.beyond_staff_name(m.email) end)
                                    when m.staff then (to_jsonb(m) - 'email') || jsonb_build_object('email', '', 'name', public.beyond_staff_name(m.email))
                                    else to_jsonb(m) end order by m.id), '[]'::jsonb)
       from support_messages m where m.ticket_id = p_ticket and (a = 'staff' or not m.internal)));
end $$;

-- A message on a ticket. The team's answer starts (or resumes) the live chat; the customer's puts it back in the queue.
create or replace function public.support_reply(p_ticket bigint, p_body text, p_files jsonb, p_internal boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a text; is_staff boolean;
begin
  a := public.beyond_ticket_access(p_ticket);
  if a is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  is_staff := a = 'staff';
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

-- The customer closes their ticket (solved), or re-opens it.
create or replace function public.support_close(p_ticket bigint, p_solved boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  if public.beyond_ticket_access(p_ticket) is null then raise exception 'ticket not found' using errcode = '42501'; end if;
  update support_tickets set status = case when coalesce(p_solved, true) then 'resolved' else 'open' end,
         waiting_since = case when coalesce(p_solved, true) then null else now() end,
         staff_unread = staff_unread or not coalesce(p_solved, true), updated_at = now() where id = p_ticket;
  return public.support_get(p_ticket);
end $$;

-- ================================================================== the live chat
-- The team ends the chat (only the team can); p_solved marks the ticket solved as well.
create or replace function public.support_chat_end(p_ticket bigint, p_solved boolean) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
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

-- The caller's live chats (and those ended in the last day that they have not seen yet), with what is new since p_after.
create or replace function public.support_live(p_after bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'ref', t.ref, 'subject', t.subject, 'chat', t.chat, 'status', t.status,
           'ended_at', t.chat_ended_at, 'company_name', t.company_name,
           'unread', (select count(*) from support_messages m where m.ticket_id = t.id and m.staff and not m.internal and m.id > t.customer_seen),
           'last', (select max(id) from support_messages m where m.ticket_id = t.id and not m.internal),
           'agent', (select public.beyond_staff_name(m.email) from support_messages m where m.ticket_id = t.id and m.staff and not m.internal order by m.id desc limit 1),
           'new', (select coalesce(jsonb_agg(jsonb_build_object('id', m.id, 'staff', m.staff, 'kind', m.kind, 'body', m.body, 'files', m.files,
                     'created_at', m.created_at, 'name', case when m.staff then public.beyond_staff_name(m.email) end) order by m.id), '[]'::jsonb)
                   from support_messages m where m.ticket_id = t.id and not m.internal and m.id > coalesce(p_after, 0)))
         order by t.updated_at desc), '[]'::jsonb)
  from support_tickets t
  where lower(t.email) = public.beyond_caller()
    and (t.chat = 'live' or (t.chat = 'ended' and t.chat_ended_at > now() - interval '1 day'
                             and exists (select 1 from support_messages m where m.ticket_id = t.id and m.staff and not m.internal and m.id > t.customer_seen)))
$$;

-- The customer has seen the chat up to now (closing the "chat ended" note too).
create or replace function public.support_seen(p_ticket bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  update support_tickets set customer_unread = false,
         customer_seen = greatest(customer_seen, coalesce((select max(id) from support_messages where ticket_id = p_ticket), 0))
   where id = p_ticket and lower(email) = public.beyond_caller();
end $$;

-- ================================================================== the team's queue
create or replace function public.console_queue() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return jsonb_build_object(
    'rows', (select coalesce(jsonb_agg(x order by (x->>'turn') = 'team' desc, (x->>'rank')::int desc, x->>'waiting_since' nulls last, x->>'updated_at' desc), '[]'::jsonb)
      from (select jsonb_build_object('id', t.id, 'ref', t.ref, 'company_id', t.company_id, 'company_name', t.company_name, 'email', t.email,
              'app_user', t.app_user, 'kind', t.kind, 'subject', t.subject, 'priority', t.priority, 'status', t.status, 'assignee', t.assignee,
              'unread', t.staff_unread, 'chat', t.chat, 'created_at', t.created_at, 'updated_at', t.updated_at, 'first_reply_at', t.first_reply_at,
              'waiting_since', t.waiting_since, 'turn', case when t.status = 'open' then 'team' else 'customer' end,
              'files', jsonb_array_length(t.files), 'messages', (select count(*) from support_messages m where m.ticket_id = t.id and m.kind = 'msg'),
              'plan', r->>'plan', 'plan_name', r->>'plan_name', 'rank', coalesce((r->>'rank')::int, t.rank), 'reply_hours', r->'reply_hours',
              'due_at', case when t.first_reply_at is null and r ? 'reply_hours' and jsonb_typeof(r->'reply_hours') = 'number'
                             then t.created_at + make_interval(secs => (r->>'reply_hours')::numeric * 3600) end) x
            from (select t.*, public.beyond_support_rank(t.company_id) r from support_tickets t where t.status <> 'closed'
                  order by t.updated_at desc limit 500) t) q),
    'closed', (select coalesce(jsonb_agg(jsonb_build_object('id', id, 'ref', ref, 'company_name', company_name, 'email', email, 'subject', subject,
                 'status', status, 'assignee', assignee, 'updated_at', updated_at, 'rank', rank, 'plan', plan) order by updated_at desc), '[]'::jsonb)
               from (select * from support_tickets where status = 'closed' order by updated_at desc limit 200) c),
    'replies', coalesce((select value from platform_settings where key = 'support_replies'), '[]'::jsonb),
    'team', (select coalesce(jsonb_agg(jsonb_build_object('email', email, 'name', name) order by name), '[]'::jsonb) from platform_admins
             where active and (perms is null or coalesce((perms->>'support')::boolean, false))));
end $$;

-- What support may know about the customer behind a ticket (no payments or prices).
create or replace function public.console_ticket_customer(p_ticket bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare t support_tickets; st jsonb;
begin
  perform public.beyond_require_staff('support');
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

-- Saved replies the team picks from: [{title, body}]
create or replace function public.console_replies_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
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

revoke all on function public.beyond_support_rank(uuid), public.beyond_staff_name(text) from public, anon, authenticated;
grant execute on function public.support_chat_end(bigint, boolean), public.support_live(bigint), public.support_seen(bigint),
  public.console_queue(), public.console_ticket_customer(bigint), public.console_replies_save(jsonb) to authenticated;
revoke all on function public.support_chat_end(bigint, boolean), public.support_live(bigint), public.support_seen(bigint),
  public.console_queue(), public.console_ticket_customer(bigint), public.console_replies_save(jsonb) from public, anon;
