-- Limits on what anyone can do over and over: without them a script can guess passwords, fill a company's public
-- page with fake bookings, open thousands of tickets or run up the AI bill.
--
--   * Sign in by username (the login function): 10 wrong tries for one username, or 50 from one address, in 15
--     minutes, then "too many attempts" for the rest of the 15 minutes. All sign-ins reach Supabase from the same
--     server, so Supabase's own per-address limit cannot tell people apart: this one can.
--   * The AI assistant: 12 requests a minute per person (on top of the company's daily limit), so a stuck page or a
--     script cannot spend the company's key.
--   * Public pages (bookings, messages, signatures): 20 from one address to one page in 10 minutes (a page already
--     takes at most 120 an hour in all, so one visitor cannot use them all up).
--   * Signed-in people: tickets 10 an hour, support messages 60 in 10 minutes, visits 5 an hour, referral codes 10 an
--     hour, chat-link messages 60 a minute, app error reports 120 in 10 minutes.
-- The platform team is not limited (they answer many tickets at once); the database owner and the server are not either.
-- Over the limit: HTTP 429 with a message saying to wait.

create table if not exists public.rate_hits (
  key text not null,
  at  timestamptz not null default now()
);
create index if not exists rate_hits_key on public.rate_hits (key, at);
alter table public.rate_hits enable row level security;         -- no policies: only through the functions below

-- the address the request came from (Supabase passes it on), '' when not known
create or replace function public.beyond_client_ip() returns text
language sql stable as $$
  select coalesce(trim(split_part(coalesce(nullif(current_setting('request.headers', true), '')::jsonb->>'x-forwarded-for', ''), ',', 1)), '')
$$;

-- one more use of key, refused (HTTP 429) when p_max were already used in the last p_seconds
create or replace function public.beyond_rate(p_key text, p_max int, p_seconds int) returns void
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  delete from rate_hits where key = p_key and at < now() - make_interval(secs => p_seconds);
  select count(*) into n from rate_hits where key = p_key;
  if n >= p_max then
    raise exception 'Too many requests — wait a few minutes and try again' using errcode = 'PT429',
      hint = 'طلبات كثيرة — انتظر بضع دقائق ثم حاول مرة أخرى';
  end if;
  insert into rate_hits(key) values (p_key);
  if random() < 0.01 then delete from rate_hits where at < now() - interval '1 day'; end if;   -- keys no longer used
end $$;

-- who is asking: a signed-in person or a visitor (not the team, the server or the database owner)
create or replace function public.beyond_rate_applies() returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb->>'role', '') in ('authenticated', 'anon')
     and (public.beyond_staff()).email is null
$$;

create or replace function public.beyond_rate_rows() returns trigger
language plpgsql security definer set search_path = public as $$
declare who text := coalesce(nullif(public.beyond_caller(), ''), 'ip:' || public.beyond_client_ip());
begin
  if not public.beyond_rate_applies() then return new; end if;
  case tg_table_name
    when 'public_inbox' then
      if new.direction = 'in' then
        perform public.beyond_rate('pub:' || new.page_id || ':' || public.beyond_client_ip(), 20, 600);
      end if;
    when 'support_tickets' then perform public.beyond_rate('ticket:' || who, 10, 3600);
    when 'support_messages' then perform public.beyond_rate('msg:' || who, 60, 600);
    when 'support_visits' then perform public.beyond_rate('visit:' || who, 5, 3600);
    when 'referrals' then perform public.beyond_rate('ref:' || who, 10, 3600);
    when 'chat_link_messages' then perform public.beyond_rate('chat:' || who, 60, 60);
    when 'app_errors' then perform public.beyond_rate('err:' || who, 120, 600);
    else null;
  end case;
  return new;
end $$;

do $$
declare t text;
begin
  foreach t in array array['public_inbox', 'support_tickets', 'support_messages', 'support_visits', 'referrals',
                           'chat_link_messages', 'app_errors'] loop
    if to_regclass('public.' || t) is not null then
      execute format('drop trigger if exists beyond_rate on public.%I', t);
      execute format('create trigger beyond_rate before insert on public.%I for each row execute function public.beyond_rate_rows()', t);
    end if;
  end loop;
end $$;

-- the login function (server only): may this username / address try now? Then: it failed, or it worked.
create or replace function public.login_gate(p_username text, p_ip text, p_result text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare u text := 'login:u:' || lower(coalesce(p_username, '')); i text := 'login:ip:' || coalesce(p_ip, '');
        nu int; ni int;
begin
  delete from rate_hits where key in (u, i) and at < now() - interval '15 minutes';
  if p_result = 'ok' then delete from rate_hits where key = u; return jsonb_build_object('ok', true); end if;
  if p_result = 'failed' then
    insert into rate_hits(key) values (u);
    if coalesce(p_ip, '') <> '' then insert into rate_hits(key) values (i); end if;
  end if;
  select count(*) into nu from rate_hits where key = u;
  select count(*) into ni from rate_hits where key = i and coalesce(p_ip, '') <> '';
  return jsonb_build_object('ok', nu < 10 and ni < 50);
end $$;

-- the AI function (server only): one more request for this person of this company, or refused
create or replace function public.ai_rate(p_company uuid, p_email text) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_rate('ai:' || p_company || ':' || lower(coalesce(p_email, '')), 12, 60);
  return jsonb_build_object('ok', true);
exception when sqlstate 'PT429' then
  return jsonb_build_object('ok', false);
end $$;

revoke all on function public.beyond_rate(text, int, int), public.beyond_rate_applies(), public.beyond_rate_rows(),
  public.login_gate(text, text, text), public.ai_rate(uuid, text), public.beyond_client_ip() from public, anon, authenticated;
grant execute on function public.login_gate(text, text, text), public.ai_rate(uuid, text) to service_role;
