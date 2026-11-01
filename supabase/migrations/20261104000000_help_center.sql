-- Help & FAQ, bug reports and solved issues others can learn from.
--
--   * FAQ: questions and answers in both languages, under categories the team names and orders. Everyone can read
--     the published ones (also before signing in).
--   * Bug reports are support tickets of the kind "bug": they reach the queue on the support desk and the owner console
--     like any ticket. The team answers each one: a ticket cannot be closed before the customer has had an answer.
--   * Closing a ticket the team decides whether to share it. Sharing publishes only a title and how it was resolved,
--     written by the team, in a list of its own: nothing about the customer (no name, company, email, message or file)
--     is copied, so nothing private can leak. Shared issues can be edited or hidden later.
--   * Search: the words typed are looked up in the FAQ and the shared issues, in either language.

create table if not exists public.help_categories (
  key        text primary key check (key ~ '^[a-z0-9_-]{1,40}$'),
  name       text not null,
  name_ar    text not null default '',
  icon       text not null default '',
  sort       int not null default 0
);
create table if not exists public.help_faq (
  id         bigserial primary key,
  category   text references public.help_categories(key) on update cascade on delete set null,
  q          text not null,
  q_ar       text not null default '',
  a          text not null,
  a_ar       text not null default '',
  sort       int not null default 0,
  published  boolean not null default true,
  updated_by text,
  updated_at timestamptz not null default now()
);
create table if not exists public.help_solved (
  id          bigserial primary key,
  ticket_id   bigint unique references public.support_tickets(id) on delete set null,   -- for the team only, never shown
  category    text references public.help_categories(key) on update cascade on delete set null,
  kind        text not null default 'issue',
  title       text not null,
  title_ar    text not null default '',
  resolution  text not null,
  resolution_ar text not null default '',
  published   boolean not null default true,
  created_by  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
alter table public.help_categories enable row level security;
alter table public.help_faq enable row level security;
alter table public.help_solved enable row level security;
alter table public.support_tickets add column if not exists shared boolean;      -- null: not decided; true / false when closed

-- a first set of categories (the team renames, reorders or removes them)
insert into public.help_categories(key, name, name_ar, icon, sort) values
  ('start', 'Getting started', 'البداية', '🚀', 1),
  ('accounts', 'Accounts and entries', 'الحسابات والقيود', '📒', 2),
  ('sales', 'Sales and invoices', 'المبيعات والفواتير', '🧾', 3),
  ('stock', 'Stock and purchasing', 'المخزون والمشتريات', '📦', 4),
  ('people', 'Users and permissions', 'المستخدمون والصلاحيات', '👥', 5),
  ('files', 'Import, export and printing', 'الاستيراد والتصدير والطباعة', '📤', 6),
  ('billing', 'Subscription and billing', 'الاشتراك والفوترة', '💳', 7),
  ('other', 'Other', 'أخرى', '💡', 9)
on conflict (key) do nothing;

-- ================================================================== reading (everyone)
create or replace function public.help_center() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object(
    'categories', (select coalesce(jsonb_agg(to_jsonb(c) order by c.sort, c.name), '[]'::jsonb) from help_categories c),
    'faq', (select coalesce(jsonb_agg(jsonb_build_object('id', f.id, 'category', f.category, 'q', f.q, 'q_ar', f.q_ar, 'a', f.a, 'a_ar', f.a_ar)
              order by f.sort, f.id), '[]'::jsonb) from help_faq f where f.published),
    'solved', (select coalesce(jsonb_agg(jsonb_build_object('id', s.id, 'category', s.category, 'kind', s.kind, 'title', s.title, 'title_ar', s.title_ar,
                 'resolution', s.resolution, 'resolution_ar', s.resolution_ar, 'date', s.created_at::date) order by s.created_at desc), '[]'::jsonb)
               from (select * from help_solved where published order by created_at desc limit 500) s))
$$;

-- The FAQ and shared issues that match the words typed (each word of 2+ letters counts once; more matches first).
create or replace function public.help_search(p_q text) returns jsonb
language sql stable security definer set search_path = public as $$
  with w as (select distinct lower(x) x from regexp_split_to_table(left(coalesce(p_q, ''), 300), '[^[:alnum:]؀-ۿ]+') x
             where length(x) >= 2 limit 12),
  f as (select 'faq' src, f.id, f.category, f.q title, f.q_ar title_ar, f.a body, f.a_ar body_ar, null::date date,
               (select count(*) from w where lower(f.q || ' ' || f.q_ar || ' ' || f.a || ' ' || f.a_ar) like '%' || w.x || '%') hits,
               (select count(*) from w where lower(f.q || ' ' || f.q_ar) like '%' || w.x || '%') thits
          from help_faq f where f.published),
  s as (select 'solved' src, s.id, s.category, s.title, s.title_ar, s.resolution body, s.resolution_ar body_ar, s.created_at::date date,
               (select count(*) from w where lower(s.title || ' ' || s.title_ar || ' ' || s.resolution || ' ' || s.resolution_ar) like '%' || w.x || '%') hits,
               (select count(*) from w where lower(s.title || ' ' || s.title_ar) like '%' || w.x || '%') thits
          from help_solved s where s.published)
  select coalesce(jsonb_agg(to_jsonb(r) - 'thits' order by r.hits desc, r.thits desc, r.src, r.id desc), '[]'::jsonb)
    from (select * from (select * from f union all select * from s) u where u.hits > 0 and (select count(*) from w) > 0
          order by hits desc, thits desc limit 20) r
$$;

-- ================================================================== the team
create or replace function public.console_help() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return jsonb_build_object(
    'categories', (select coalesce(jsonb_agg(to_jsonb(c) order by c.sort, c.name), '[]'::jsonb) from help_categories c),
    'faq', (select coalesce(jsonb_agg(to_jsonb(f) order by f.sort, f.id), '[]'::jsonb) from help_faq f),
    'solved', (select coalesce(jsonb_agg(to_jsonb(s) || jsonb_build_object('ticket_ref', (select ref from support_tickets where id = s.ticket_id))
                 order by s.created_at desc), '[]'::jsonb) from help_solved s));
end $$;

create or replace function public.console_help_category(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
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
  perform public.beyond_require_staff('support');
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

-- Close a ticket, deciding whether to share it: when shared, only the title and the resolution given here are published.
create or replace function public.console_ticket_close(p_ticket bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare t support_tickets; share boolean := coalesce((p->>'share')::boolean, false);
begin
  perform public.beyond_require_staff('support');
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

create or replace function public.console_help_solved(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare sid bigint := (p->>'id')::bigint;
begin
  perform public.beyond_require_staff('support');
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

-- every bug report, newest first (open and closed)
create or replace function public.console_bugs() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('support');
  return (select coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'ref', t.ref, 'subject', t.subject, 'company_name', t.company_name, 'email', t.email,
            'status', t.status, 'assignee', t.assignee, 'shared', t.shared, 'created_at', t.created_at, 'updated_at', t.updated_at, 'unread', t.staff_unread,
            'version', t.diag->>'version', 'screen', t.diag->>'screen', 'files', jsonb_array_length(t.files),
            'errors', (select count(*) from jsonb_array_elements(t.log) e where e->>'t' = 'error'))
          order by t.created_at desc), '[]'::jsonb)
          from (select * from support_tickets where kind = 'bug' order by created_at desc limit 500) t);
end $$;

grant execute on function public.help_center(), public.help_search(text) to anon, authenticated;
grant execute on function public.console_help(), public.console_help_category(jsonb), public.console_help_faq(jsonb),
  public.console_ticket_close(bigint, jsonb), public.console_help_solved(jsonb), public.console_bugs() to authenticated;
revoke all on function public.console_help(), public.console_help_category(jsonb), public.console_help_faq(jsonb),
  public.console_ticket_close(bigint, jsonb), public.console_help_solved(jsonb), public.console_bugs() from public, anon;
