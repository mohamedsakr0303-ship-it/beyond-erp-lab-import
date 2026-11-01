-- Support access to a customer's books: the customer allows it, for a while, and can end it at any time.
--
--   * A company administrator allows the team member answering their ticket in, for 1 to 72 hours, either to view
--     the books only or to view and fix. Nobody on the platform team can let themselves in.
--   * While it lasts, the team member opens the company like one of its people. They are not a member: they take
--     no user seat and are not listed among its people; when the time is up or the customer ends it, they are out.
--   * Inside the books they are a user of their own ("Support · name"), which the customer's app adds when access is
--     first allowed, so every change they make is a normal entry signed with their name in the audit trail, made
--     through the same screens and rules as anyone's, and reversible the same way. Changes are refused on the
--     server when the access is view only or has ended.

create table if not exists public.support_access (
  id          bigserial primary key,
  company_id  uuid not null references public.companies(id) on delete cascade,
  ticket_id   bigint references public.support_tickets(id) on delete set null,
  agent       text not null,
  mode        text not null check (mode in ('view', 'fix')),
  granted_by  text not null,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  ended_at    timestamptz,
  ended_by    text
);
create index if not exists support_access_company on public.support_access(company_id, lower(agent));
alter table public.support_access enable row level security;

-- the caller's access to a company through the support desk, if it is on now
create or replace function public.beyond_support_grant(p_company uuid) returns public.support_access
language sql stable security definer set search_path = public as $$
  select a.* from support_access a
   where a.company_id = p_company and lower(a.agent) = public.beyond_caller() and a.ended_at is null and a.expires_at > now()
     and public.beyond_staff_can('support')
   order by a.expires_at desc limit 1
$$;

-- a member of the company — or, while support access is on, the team member let in (never an administrator; the
-- username says what they may do: 'support:view' or 'support:fix')
create or replace function public.beyond_member(p_company uuid) returns public.members
language plpgsql stable security definer set search_path = public as $$
declare m members; g support_access;
begin
  select * into m from members where company_id = p_company and lower(email) = public.beyond_caller() and active and not pending;
  if m.email is not null then return m; end if;
  g := public.beyond_support_grant(p_company);
  if g.id is null then return m; end if;
  return jsonb_populate_record(null::members, jsonb_build_object('email', public.beyond_caller(), 'company_id', p_company, 'active', true,
           'pending', false, 'is_admin', false, 'gateway', false, 'is_super', false, 'username', 'support:' || g.mode));
end $$;

create or replace function public.beyond_in(p_company uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from members where company_id = p_company and lower(email) = public.beyond_caller() and active and not pending)
      or (public.beyond_support_grant(p_company)).id is not null
$$;

-- a change that adds the support desk's own user to the books does not take one of the company's seats
create or replace function public.beyond_support_user(p_company uuid, p_body text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from support_access a where a.company_id = p_company and a.ended_at is null and a.expires_at > now()
                 and position(lower('"' || a.agent || '"') in lower(coalesce(p_body, ''))) > 0)
$$;

create or replace function public.append_op(p_company uuid, p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language plpgsql security definer set search_path = public as $$
declare m members; last bigint; who text; st jsonb; mt jsonb;
begin
  m := public.beyond_member(p_company);
  if m.email is null then
    if exists (select 1 from support_access where company_id = p_company and lower(agent) = public.beyond_caller()) then
      raise exception 'support access to this company has ended — ask the customer to allow it again' using errcode = '42501';
    end if;
    raise exception 'not a member of this company' using errcode = '42501';
  end if;
  if m.username = 'support:view' then
    raise exception 'support access to this company is view only — the customer can allow changes' using errcode = '42501';
  end if;
  if p_method not in ('POST', 'PUT', 'DELETE') or left(p_path, 5) <> '/api/' then
    raise exception 'invalid change' using errcode = '22023';
  end if;
  select maintenance into mt from billing_config where id = 1;
  if coalesce((mt->>'on')::boolean, false) and not public.beyond_is_owner() then
    raise exception 'maintenance: %', coalesce(nullif(mt->>'message', ''), 'BeyondERP is under maintenance — changes are paused for a short while. Everything stays available to view.')
      using errcode = 'P0503';
  end if;
  st := public.beyond_billing_state(p_company);
  if not (st->>'can_write')::boolean then
    if st->>'status' = 'archived' then
      raise exception 'subscription_ended: this company is archived — contact BeyondERP to reopen it. Everything stays available to view, print and export.'
        using errcode = 'P0402';
    end if;
    if st->>'status' = 'suspended' then
      raise exception 'subscription_ended: this company is suspended%. Everything stays available to view, print and export.',
        coalesce(' — ' || nullif(st->>'suspend_note', ''), '') using errcode = 'P0402';
    end if;
    if st->>'status' = 'unpaid' then
      raise exception 'subscription_ended: this company is not in a subscription yet — an administrator adds it in Settings → Subscription. Everything stays available to view, print and export.'
        using errcode = 'P0402';
    end if;
    raise exception 'subscription_ended: the free trial / paid period of this company has ended — an administrator can subscribe in Settings → Subscription. Everything stays available to view, print and export.'
      using errcode = 'P0402';
  end if;
  if p_method = 'POST' and p_path ~ '^/api/users/?$' and st->>'users_allowed' is not null
     and not public.beyond_support_user(p_company, p_body)
     and (st->>'users')::int >= (st->>'users_allowed')::int then
    raise exception 'seat_limit: % — this company may have % user(s) and all are taken. An administrator can add users in Settings → Subscription, or deactivate someone first.',
      coalesce(st->>'plan_name', 'current plan'), st->>'users_allowed' using errcode = 'P0402';
  end if;
  perform pg_advisory_xact_lock(424202, hashtext(p_company::text));
  select coalesce(max(seq), 0) into last from ops where company_id = p_company;
  if p_seq <> last + 1 then
    raise exception 'conflict: next change is %', last + 1 using errcode = '40001';
  end if;
  who := case when m.gateway and coalesce(p_app_user, '') <> '' then p_app_user else m.email end;
  insert into ops(company_id, seq, version, actor, app_user, device, method, path, body, content_type, clock)
  values (p_company, p_seq, p_version, m.email, who, left(p_device, 80), p_method, p_path, p_body, p_content_type, p_clock);
  return json_build_object('seq', p_seq, 'app_user', who);
end $$;

-- The companies the caller can open: their own, and those they are let into for support (marked as such).
create or replace function public.my_companies() returns json
language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(x order by x->>'support' nulls first, x->>'name'), '[]'::json) from (
    select json_build_object('id', c.id, 'name', c.name, 'is_admin', m.is_admin, 'pending', m.pending,
                             'active', m.active, 'created_at', c.created_at,
                             'is_super', m.active and not m.pending and (m.is_super or lower(m.email) = lower(coalesce(c.created_by, ''))),
                             'changes', case when m.active and not m.pending then (select coalesce(max(seq), 0) from ops o where o.company_id = c.id) end)::jsonb x
      from members m join companies c on c.id = m.company_id
     where lower(m.email) = public.beyond_caller() and (m.active or m.pending)
    union all
    select json_build_object('id', c.id, 'name', c.name, 'is_admin', false, 'pending', false, 'active', true, 'created_at', c.created_at,
                             'is_super', false, 'support', a.mode, 'support_until', a.expires_at,
                             'changes', (select coalesce(max(seq), 0) from ops o where o.company_id = c.id))::jsonb
      from support_access a join companies c on c.id = a.company_id
     where lower(a.agent) = public.beyond_caller() and a.ended_at is null and a.expires_at > now() and public.beyond_staff_can('support')
       and not exists (select 1 from members m where m.company_id = c.id and lower(m.email) = public.beyond_caller() and m.active)) q
$$;

-- ================================================================== the customer allows, the customer (or the team) ends
-- A company administrator lets the team member on their ticket into the company's books.
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
  if ag = '' or not exists (select 1 from platform_admins where lower(email) = ag and active and (perms is null or coalesce((perms->>'support')::boolean, false))) then
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

create or replace function public.beyond_access_row(p_id bigint) returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('id', a.id, 'company_id', a.company_id, 'ticket_id', a.ticket_id, 'mode', a.mode, 'agent', a.agent,
           'agent_name', public.beyond_staff_name(a.agent), 'granted_by', a.granted_by, 'created_at', a.created_at, 'expires_at', a.expires_at,
           'ended_at', a.ended_at, 'on', a.ended_at is null and a.expires_at > now())
    from support_access a where a.id = p_id
$$;

-- Ends it now: an administrator of the company, the team member let in, or anyone on the team with support access.
create or replace function public.support_access_end(p_id bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare g support_access;
begin
  select * into g from support_access where id = p_id;
  if g.id is null then raise exception 'not found' using errcode = '42501'; end if;
  if not (exists (select 1 from members where company_id = g.company_id and lower(email) = public.beyond_caller() and active and not pending and is_admin)
          or lower(g.agent) = public.beyond_caller() or public.beyond_staff_can('support')) then
    raise exception 'not found' using errcode = '42501';
  end if;
  update support_access set ended_at = coalesce(ended_at, now()), ended_by = coalesce(ended_by, public.beyond_caller()) where id = p_id;
  if g.ticket_id is not null and g.ended_at is null then
    insert into support_messages(ticket_id, email, staff, kind, body) values (g.ticket_id, public.beyond_caller(), public.beyond_staff_can('support'), 'event', 'access_ended');
    update support_tickets set staff_unread = true, customer_unread = true, updated_at = now() where id = g.ticket_id;
  end if;
  perform public.beyond_log('support.access_end', null, jsonb_build_object('company', g.company_id, 'agent', g.agent));
  return public.beyond_access_row(p_id);
end $$;

-- Who is let into a company now (its administrators see it; so does the team) and, for the team, a ticket's access.
create or replace function public.support_access_list(p_company uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if not (exists (select 1 from members where company_id = p_company and lower(email) = public.beyond_caller() and active and not pending and is_admin)
          or public.beyond_staff_can('support')) then
    raise exception 'not found' using errcode = '42501';
  end if;
  return (select coalesce(jsonb_agg(public.beyond_access_row(id) order by created_at desc), '[]'::jsonb)
            from (select id, created_at from support_access where company_id = p_company order by created_at desc limit 50) a);
end $$;

revoke all on function public.beyond_support_grant(uuid), public.beyond_support_user(uuid, text), public.beyond_access_row(bigint)
  from public, anon, authenticated;
grant execute on function public.support_access_grant(bigint, int, text), public.support_access_end(bigint), public.support_access_list(uuid)
  to authenticated;
revoke all on function public.support_access_grant(bigint, int, text), public.support_access_end(bigint), public.support_access_list(uuid)
  from public, anon;
