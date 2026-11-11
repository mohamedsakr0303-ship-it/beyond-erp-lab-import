-- Who may change company members (security review): administrators add people; only a super administrator changes
-- someone already in the company, makes administrators or gateways (a gateway's changes are replayed as any user),
-- and nobody touches the username of a person who is not a member of their own company.

create or replace function public.set_member(p_company uuid, p_email text, p_is_admin boolean, p_gateway boolean,
  p_active boolean) returns void
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p_email, ''))); cur members%rowtype; creator text;
begin
  perform public.beyond_require_admin(p_company);
  if e !~ '^[^@\s]+@[^@\s]+$' then raise exception 'enter an email address'; end if;
  perform pg_advisory_xact_lock(424201, hashtext(p_company::text));
  select * into cur from members where company_id = p_company and lower(email) = e;
  -- changing someone already in the company (anyone else's flags)
  if found and not cur.pending and e <> public.beyond_caller()
     and (cur.is_admin is distinct from p_is_admin or cur.gateway is distinct from p_gateway or cur.active is distinct from p_active) then
    perform public.beyond_require_super(p_company);
  end if;
  -- making an administrator or a gateway, oneself included
  if (coalesce(p_is_admin, false) and not (found and not cur.pending and cur.is_admin))
     or (coalesce(p_gateway, false) and not (found and not cur.pending and cur.gateway)) then
    perform public.beyond_require_super(p_company);
  end if;
  select lower(coalesce(created_by, '')) into creator from companies where id = p_company;
  if e = creator and (not coalesce(p_active, true) or not coalesce(p_is_admin, true)) then
    raise exception 'the company''s creator stays an active administrator';
  end if;
  insert into members(company_id, email, is_admin, gateway, active, pending, added_by)
  values (p_company, e, p_is_admin, p_gateway, p_active, false, public.beyond_caller())
  on conflict (company_id, email) do update set is_admin = excluded.is_admin, gateway = excluded.gateway,
                                                active = excluded.active, pending = false;
  insert into people(email) values (e) on conflict (email) do nothing;
  if not exists (select 1 from members where company_id = p_company and is_admin and active and not pending) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;

create or replace function public.link_member(p_company uuid, p_email text, p_username text, p_active boolean default true)
returns void language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(coalesce(p_email, ''))); u text := nullif(lower(trim(coalesce(p_username, ''))), '');
        cur members%rowtype; mine boolean; known boolean; creator text;
begin
  perform public.beyond_require_admin(p_company);
  if e !~ '^[^@\s]+@[^@\s]+$' then raise exception 'enter an email address'; end if;
  if u is not null and u ~ '@' then u := null; end if;
  perform pg_advisory_xact_lock(424201, hashtext(p_company::text));
  select * into cur from members where company_id = p_company and lower(email) = e;
  mine := found and not cur.pending;
  known := exists (select 1 from people where email = e);
  -- an existing member's active flag: super administrators only; the creator is never switched off
  if mine and cur.active is distinct from coalesce(p_active, true) then
    perform public.beyond_require_super(p_company);
  end if;
  select lower(coalesce(created_by, '')) into creator from companies where id = p_company;
  if e = creator and not coalesce(p_active, true) then
    raise exception 'the company''s creator stays active';
  end if;
  -- a username is set for a new person, or for a member of this company; never for someone else's account
  if u is not null and known and not mine then
    u := null;
  end if;
  if u is not null and exists (select 1 from people where lower(username) = u and email <> e) then
    raise exception 'the username % is already used by another account — choose another', u;
  end if;
  insert into people(email, username) values (e, u)
  on conflict (email) do update set username = coalesce(excluded.username, people.username);
  insert into members(company_id, email, active, added_by) values (p_company, e, coalesce(p_active, true), public.beyond_caller())
  on conflict (company_id, email) do update set active = excluded.active, pending = false;
  if not exists (select 1 from members where company_id = p_company and is_admin and active and not pending) then
    raise exception 'at least one active administrator must remain';
  end if;
end $$;
