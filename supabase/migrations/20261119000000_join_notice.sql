-- Someone added to a company (Users → + New or Invite by email) is told: a card in the app the next time they open it,
-- a push notification on their devices and an email (supabase/functions/manage-user). The card shows once.
--
-- members.welcome_seen: false only for a membership the sign-in function has just added for someone; every membership
-- that already exists (and those made any other way: a company's creator, a join request) counts as seen.
alter table public.members add column if not exists welcome_seen boolean not null default true;

-- The companies the caller can open: their own, and those they are let into for support (marked as such); a company
-- they were just added to carries who added them (welcome) until they have seen the card.
create or replace function public.my_companies() returns json
language sql stable security definer set search_path = public as $$
  select coalesce(json_agg(x order by x->>'support' nulls first, x->>'name'), '[]'::json) from (
    select (json_build_object('id', c.id, 'name', c.name, 'is_admin', m.is_admin, 'pending', m.pending,
                             'active', m.active, 'created_at', c.created_at,
                             'is_super', m.active and not m.pending and (m.is_super or lower(m.email) = lower(coalesce(c.created_by, ''))),
                             'changes', case when m.active and not m.pending then (select coalesce(max(seq), 0) from ops o where o.company_id = c.id) end)::jsonb
            || case when not m.welcome_seen and m.active and not m.pending then jsonb_build_object('welcome', jsonb_build_object(
                 'by', coalesce(nullif((select p.username from people p where lower(p.email) = lower(m.added_by)), ''), m.added_by, ''),
                 'at', m.added_at)) else '{}'::jsonb end) x
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

-- The person has seen the card (switched to the company, or closed it): it does not come back.
create or replace function public.welcome_seen(p_company uuid) returns void
language sql security definer set search_path = public as $$
  update members set welcome_seen = true where company_id = p_company and lower(email) = public.beyond_caller() and not welcome_seen
$$;
revoke all on function public.welcome_seen(uuid) from public, anon;
grant execute on function public.welcome_seen(uuid) to authenticated, service_role;
