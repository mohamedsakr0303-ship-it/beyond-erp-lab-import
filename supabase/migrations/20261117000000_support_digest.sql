-- More for the team to see what is going on:
--
--   * Notifications: a new app error, and an error that hits several companies at once (a spike: 3 or more in a day),
--     go to the team members with the bug reports right, like any other notification (read, dismissed, muted).
--   * The last 24 hours at a glance (console_digest): new and spiking app errors, tickets waiting, payments to check
--     and received, new companies, trials ending.
--   * A company's recent changes (console_company_changes): who changed what in its online books and when — the screen
--     or call only (the path, without its query), never the figures; for support working a ticket and for customers.

-- the notifications as they were, kept under another name; console_alerts adds the app errors to them
do $$ begin
  if not exists (select 1 from pg_proc where proname = 'beyond_console_alerts_base' and pronamespace = 'public'::regnamespace) then
    alter function public.console_alerts() rename to beyond_console_alerts_base;
  end if;
end $$;
revoke all on function public.beyond_console_alerts_base() from public, anon, authenticated;

create or replace function public.console_alerts() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins; base jsonb; extra jsonb := '[]'::jsonb; r record; mute jsonb;
begin
  base := public.beyond_console_alerts_base();          -- (checks the caller is on the team)
  s := public.beyond_staff();
  mute := coalesce(s.notify, '{}'::jsonb);
  if public.beyond_desk_can('bugs') then
    for r in select e.*, (select count(*) from app_error_companies c where c.error_id = e.id and c.last_seen > now() - interval '1 day') day_cos
               from app_errors e where e.status = 'open' and e.last_seen > now() - interval '7 days' order by e.last_seen desc limit 200 loop
      if r.day_cos >= 3 then
        extra := extra || jsonb_build_object('key', 'es:' || r.id || ':' || current_date, 'kind', 'error_spike', 'area', 'support', 'level', 'critical',
          'at', r.last_seen, 'title', left(r.message, 140), 'detail', r.day_cos || ' companies today · ' || r.place, 'link', 'errors?id=' || r.id);
      elsif r.first_seen > now() - interval '3 days' then
        extra := extra || jsonb_build_object('key', 'ne:' || r.id, 'kind', 'new_error', 'area', 'support', 'level', 'warn',
          'at', r.first_seen, 'title', left(r.message, 140), 'detail', coalesce(nullif(r.last_version, ''), '?') || ' · ' || r.place, 'link', 'errors?id=' || r.id);
      end if;
    end loop;
  end if;
  if jsonb_array_length(extra) = 0 then return base; end if;
  return (select coalesce(jsonb_agg(a order by (a->>'at')::timestamptz desc), '[]'::jsonb) from (
            select a from jsonb_array_elements(base) a
            union all
            select a || jsonb_build_object('read', st.state = 'read') from jsonb_array_elements(extra) a
              left join platform_alert_state st on st.email = lower(s.email) and st.key = a->>'key'
             where coalesce(st.state, '') <> 'dismissed' and coalesce((mute->>(a->>'kind'))::boolean, true)) x);
end $$;

-- the last 24 hours, for the overview and the support queue (each figure only for those allowed to see it)
create or replace function public.console_digest() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare s platform_admins; out jsonb := '{}'::jsonb;
begin
  s := public.beyond_staff();
  if s.email is null then raise exception 'not a member of the platform team' using errcode = '42501'; end if;
  if public.beyond_desk_can('bugs') then
    out := out || jsonb_build_object(
      'errors_new', (select count(*) from app_errors where first_seen > now() - interval '1 day'),
      'errors_open', (select count(*) from app_errors where status = 'open' and last_seen > now() - interval '1 day'),
      'errors_spiking', (select count(*) from app_errors e where e.status = 'open'
                          and (select count(*) from app_error_companies c where c.error_id = e.id and c.last_seen > now() - interval '1 day') >= 3),
      'companies_with_errors', (select count(distinct company_id) from app_error_companies where last_seen > now() - interval '1 day'));
  end if;
  if public.beyond_staff_can('support') then
    out := out || jsonb_build_object(
      'tickets_new', (select count(*) from support_tickets where created_at > now() - interval '1 day'),
      'tickets_unread', (select count(*) from support_tickets where staff_unread and status <> 'closed'),
      'tickets_closed', (select count(*) from support_tickets where status = 'closed' and updated_at > now() - interval '1 day'));
  end if;
  if public.beyond_staff_can('billing') then
    out := out || jsonb_build_object(
      'orders_waiting', (select count(*) from billing_orders where status = 'submitted'),
      'received', (select coalesce(sum(amount), 0) from billing_payments where status = 'paid' and paid_at > now() - interval '1 day'),
      'currency', (select currency from billing_config where id = 1));
  end if;
  if public.beyond_staff_can('customers') then
    out := out || jsonb_build_object(
      'companies_new', (select count(*) from companies where created_at > now() - interval '1 day'),
      'trials_ending', (select count(*) from companies c, lateral (select public.beyond_billing_state(c.id) st) x
                         where x.st->>'billing_status' = 'trial' and (x.st->>'ends')::timestamptz < now() + interval '3 days'),
      'active_companies', (select count(distinct company_id) from ops where created_at > now() - interval '1 day'));
  end if;
  return out;
end $$;

-- a company's last changes in its online books: when, who, on which device, which screen or call (no figures)
create or replace function public.console_company_changes(p_company uuid, p_limit int) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if not (public.beyond_staff_can('customers') or public.beyond_staff_can('support')) then
    raise exception 'your team account is not allowed to do this' using errcode = '42501';
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('seq', o.seq, 'at', o.created_at, 'clock', o.clock, 'who', public.beyond_desk_email(o.actor),
            'app_user', o.app_user, 'device', coalesce(o.device, ''), 'version', o.version, 'method', o.method,
            'path', regexp_replace(o.path, '\?.*$', '')) order by o.seq desc), '[]'::jsonb)
          from (select * from ops where company_id = p_company order by seq desc limit least(greatest(coalesce(p_limit, 50), 1), 300)) o);
end $$;

revoke all on function public.console_digest(), public.console_company_changes(uuid, int) from public, anon;
grant execute on function public.console_alerts(), public.console_digest(), public.console_company_changes(uuid, int) to authenticated;
