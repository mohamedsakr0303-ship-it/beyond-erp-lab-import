-- Tools for the team to find and fix problems before (or while) customers report them.
--
--   * App errors: the app reports its own crashes (a screen that failed to draw, a call the server could not answer)
--     to the workspace — the message and where it happened, with numbers and e-mail addresses masked, never the books'
--     data. The same error from many companies is one row: how often, since when, in which versions, which companies.
--     The team (support desk, bug reports right) marks it fixed in a version; seen again from that version or a later
--     one, it opens again by itself.
--   * Devices: a company's page lists the devices that write to its books, the app version on each, who used it last
--     and when — an old version or a device that stopped syncing shows at a glance.
--   * Asking for access: on a ticket, the team member answering it asks the customer to let them into the books; the
--     customer sees the request with the button that does it (only an administrator of the company can allow it).

create table if not exists public.app_errors (
  id            bigserial primary key,
  fingerprint   text not null unique,              -- md5 of kind + message + where, once masked
  kind          text not null default 'js' check (kind in ('js', 'server')),
  message       text not null,
  place         text not null default '',          -- the screen or call it happened on
  total         bigint not null default 0,
  first_seen    timestamptz not null default now(),
  last_seen     timestamptz not null default now(),
  first_version text not null default '',
  last_version  text not null default '',
  status        text not null default 'open' check (status in ('open', 'fixed', 'ignored')),
  fixed_in      text not null default '',
  note          text not null default '',
  updated_by    text not null default '',
  updated_at    timestamptz
);
create table if not exists public.app_error_companies (
  error_id     bigint not null references public.app_errors(id) on delete cascade,
  company_id   uuid not null references public.companies(id) on delete cascade,
  hits         bigint not null default 0,
  last_seen    timestamptz not null default now(),
  last_version text not null default '',
  last_user    text not null default '',
  primary key (error_id, company_id)
);
create index if not exists app_errors_seen on public.app_errors(status, last_seen desc);
create index if not exists app_error_companies_company on public.app_error_companies(company_id, last_seen desc);
alter table public.app_errors enable row level security;            -- no policies: only through the functions below
alter table public.app_error_companies enable row level security;

-- version text → numbers to compare ("2.10.1" after "2.9")
create or replace function public.beyond_ver(v text) returns int[]
language sql immutable as $$
  select coalesce(array(select x::int from unnest(regexp_split_to_array(coalesce(v, ''), '[^0-9]+')) x where x <> '' and length(x) < 9), '{}')
$$;

-- numbers of 4+ digits and e-mail addresses masked, so an error never carries an amount, a phone or a person
create or replace function public.beyond_mask(t text, n int) returns text
language sql immutable as $$
  select left(regexp_replace(regexp_replace(coalesce(t, ''), '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+', '…@…', 'g'), '[0-9]{4,}', '#', 'g'), n)
$$;

-- The app reports up to 20 errors at once: [{kind, message, place, version, n}]. A person of the company (or support
-- let in) only; repeats are counted, not stored again.
create or replace function public.app_errors_report(p_company uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare it jsonb; k text; msg text; pl text; ver text; n int; fp text; eid bigint; done int := 0; e app_errors;
begin
  if p_company is null or not public.beyond_in(p_company) then raise exception 'not a member of this company' using errcode = '42501'; end if;
  if jsonb_typeof(p_items) <> 'array' then return jsonb_build_object('saved', 0); end if;
  for it in select * from jsonb_array_elements(p_items) limit 20 loop
    k := case when it->>'kind' = 'server' then 'server' else 'js' end;
    msg := public.beyond_mask(it->>'message', 500);
    pl := public.beyond_mask(regexp_replace(coalesce(it->>'place', ''), '\?.*$', ''), 200);
    ver := left(coalesce(it->>'version', ''), 30);
    n := least(greatest(coalesce((it->>'n')::int, 1), 1), 1000);
    if msg = '' then continue; end if;
    fp := md5(k || '|' || msg || '|' || pl);
    insert into app_errors(fingerprint, kind, message, place, total, first_version, last_version)
    values (fp, k, msg, pl, n, ver, ver)
    on conflict (fingerprint) do update set total = app_errors.total + n, last_seen = now(),
      last_version = case when public.beyond_ver(excluded.last_version) >= public.beyond_ver(app_errors.last_version) then excluded.last_version else app_errors.last_version end
    returning * into e;
    -- fixed in a version, seen again from that one or a later one: open again
    if e.status = 'fixed' and e.fixed_in <> '' and ver <> '' and public.beyond_ver(ver) >= public.beyond_ver(e.fixed_in) then
      update app_errors set status = 'open', note = trim(both from note || E'\n' || 'Seen again in ' || ver || ' after the fix.'), updated_at = now() where id = e.id;
    end if;
    insert into app_error_companies(error_id, company_id, hits, last_version, last_user)
    values (e.id, p_company, n, ver, left(public.beyond_caller(), 120))
    on conflict (error_id, company_id) do update set hits = app_error_companies.hits + n, last_seen = now(),
      last_version = excluded.last_version, last_user = excluded.last_user;
    done := done + 1;
  end loop;
  return jsonb_build_object('saved', done);
end $$;

create or replace function public.beyond_app_error_row(e public.app_errors) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('id', e.id, 'kind', e.kind, 'message', e.message, 'place', e.place, 'total', e.total,
           'first_seen', e.first_seen, 'last_seen', e.last_seen, 'first_version', e.first_version, 'last_version', e.last_version,
           'status', e.status, 'fixed_in', e.fixed_in, 'note', e.note, 'updated_by', e.updated_by, 'updated_at', e.updated_at,
           'companies', (select count(*) from app_error_companies c where c.error_id = e.id),
           'companies_7d', (select count(*) from app_error_companies c where c.error_id = e.id and c.last_seen > now() - interval '7 days'))
$$;

-- the team: every error (open first, the most widespread on top)
create or replace function public.console_app_errors(p_status text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('bugs');
  return (select coalesce(jsonb_agg(public.beyond_app_error_row(e) order by (e.status = 'open') desc, e.last_seen desc), '[]'::jsonb)
          from (select * from app_errors where coalesce(p_status, '') = '' or status = p_status order by last_seen desc limit 500) e);
end $$;

-- one error with the companies it happened to
create or replace function public.console_app_error(p_id bigint) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare e app_errors;
begin
  perform public.beyond_require_desk('bugs');
  select * into e from app_errors where id = p_id;
  if e.id is null then raise exception 'not found'; end if;
  return public.beyond_app_error_row(e) || jsonb_build_object('where', (select coalesce(jsonb_agg(jsonb_build_object(
           'company_id', c.company_id, 'company', co.name, 'hits', c.hits, 'last_seen', c.last_seen, 'version', c.last_version,
           'user', public.beyond_desk_email(c.last_user)) order by c.last_seen desc), '[]'::jsonb)
         from app_error_companies c join companies co on co.id = c.company_id where c.error_id = e.id));
end $$;

-- mark it fixed (in a version), ignored or open again, with a note
create or replace function public.console_app_error_set(p_id bigint, p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare e app_errors; st text := coalesce(p->>'status', '');
begin
  perform public.beyond_require_desk('bugs');
  if st not in ('', 'open', 'fixed', 'ignored') then raise exception 'status must be open, fixed or ignored'; end if;
  update app_errors set status = coalesce(nullif(st, ''), status),
         fixed_in = case when p ? 'fixed_in' then left(coalesce(p->>'fixed_in', ''), 30) when st = 'open' then '' else fixed_in end,
         note = case when p ? 'note' then left(coalesce(p->>'note', ''), 4000) else note end,
         updated_by = public.beyond_caller(), updated_at = now()
   where id = p_id returning * into e;
  if e.id is null then raise exception 'not found'; end if;
  perform public.beyond_log('app_error.set', e.id::text, jsonb_build_object('status', e.status, 'fixed_in', e.fixed_in));
  return public.beyond_app_error_row(e);
end $$;

-- a company's devices and its recent app errors, for its page in the console
create or replace function public.console_company_health(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if not (public.beyond_staff_can('customers') or public.beyond_desk_can('bugs')) then
    raise exception 'your team account is not allowed to do this' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'latest', (select v from (select version v from patch_notes where version <> '' union select last_version from app_errors where last_version <> '') x
               order by public.beyond_ver(v) desc limit 1),
    'devices', (select coalesce(jsonb_agg(d order by d->>'last' desc), '[]'::jsonb) from (
        select jsonb_build_object('device', coalesce(o.device, ''), 'last', max(o.created_at), 'changes', count(*),
                 'version', (array_agg(o.version order by o.seq desc))[1],
                 'user', public.beyond_desk_email((array_agg(o.actor order by o.seq desc))[1]),
                 'app_user', (array_agg(o.app_user order by o.seq desc))[1]) d
          from ops o where o.company_id = p_company group by coalesce(o.device, '') order by max(o.created_at) desc limit 40) y),
    'errors', (select coalesce(jsonb_agg(jsonb_build_object('id', e.id, 'message', e.message, 'place', e.place, 'status', e.status,
                 'hits', c.hits, 'last_seen', c.last_seen, 'version', c.last_version) order by c.last_seen desc), '[]'::jsonb)
               from (select * from app_error_companies where company_id = p_company order by last_seen desc limit 30) c
               join app_errors e on e.id = c.error_id));
end $$;

-- the team member answering a ticket asks the customer to let them into the books
create or replace function public.support_access_ask(p_ticket bigint, p_mode text, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare t support_tickets;
begin
  perform public.beyond_require_desk('reply');
  select * into t from support_tickets where id = p_ticket;
  if t.id is null or t.company_id is null then raise exception 'this ticket is not from a company workspace'; end if;
  if coalesce(p_mode, '') not in ('view', 'fix') then raise exception 'choose view only, or view and fix'; end if;
  if coalesce(t.assignee, '') = '' then update support_tickets set assignee = public.beyond_caller() where id = t.id; end if;
  insert into support_messages(ticket_id, email, staff, kind, body)
  values (t.id, public.beyond_caller(), true, 'event', 'access_asked_' || p_mode || ':' || left(coalesce(p_note, ''), 300));
  update support_tickets set customer_unread = true, updated_at = now(), status = case when status = 'open' then 'answered' else status end
   where id = t.id;
  perform public.beyond_log('support.access_ask', t.ref, jsonb_build_object('mode', p_mode));
  return public.support_get(p_ticket);
end $$;

revoke all on function public.app_errors_report(uuid, jsonb), public.console_app_errors(text), public.console_app_error(bigint),
  public.console_app_error_set(bigint, jsonb), public.console_company_health(uuid), public.support_access_ask(bigint, text, text),
  public.beyond_app_error_row(public.app_errors) from public, anon;
grant execute on function public.app_errors_report(uuid, jsonb), public.console_app_errors(text), public.console_app_error(bigint),
  public.console_app_error_set(bigint, jsonb), public.console_company_health(uuid), public.support_access_ask(bigint, text, text) to authenticated;
