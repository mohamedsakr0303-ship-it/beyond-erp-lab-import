-- Full control from the owner console: companies per plan, user and notification permissions per plan, the trial,
-- and the website's texts.
--
-- 1. plan.companies: how many companies a customer on the plan may own (blank: the general setting, 0 there = no
--    limit). A person's limit is the most generous one among the companies they already own (a company on its trial
--    counts with the trial's plan). A number set for the person in the console still beats it. With billing off
--    only the general setting and the person's number apply.
-- 2. plan.perms / plan.notify_perms (on unless switched off): whether the company may set what each user sees (user
--    permissions for menus and screens) and who receives which notifications. Off: the ERP locks those screens, new
--    users get full use of what the plan includes, choices made before stay as they were, and the workspace refuses
--    any change that restricts a user or changes the notification rules. Full use (billing off, free, a trial that
--    shows everything) includes both. One company can be given either, or have it taken, on its own page
--    (overrides.perms / overrides.notify_perms), like the assistant and support.
-- 3. The trial follows "everything" or one plan (trial_plan): a hidden plan made for the trial sets exactly what the
--    trial includes (sections, screens, users, companies, assistant, permissions).
-- 4. Website texts (site_texts): the home page, the sign-in screens and the public pages read their wording from here
--    (English and Arabic), falling back to the built-in wording. The owner edits them in the console, and so may a team
--    member given the "site" area. Anyone may read them (they are what the public pages show).

-- ================================================================== what a company may use (as before, plus permissions)
create or replace function public.beyond_entitle(cfg public.billing_config, plan jsonb, status text, b public.company_billing) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare full_access boolean; mods jsonb; ai boolean; sup boolean; o jsonb := coalesce(b.overrides, '{}'::jsonb); a jsonb := coalesce(b.addons, '{}'::jsonb);
        deny jsonb; prm boolean; nprm boolean;
begin
  full_access := cfg.id is null or not cfg.enabled or status = 'comped' or (status = 'trial' and cfg.trial_full);
  if full_access or o->>'modules' = 'all' or coalesce(plan->>'modules', 'all') = 'all' or jsonb_typeof(plan->'modules') <> 'array' then
    mods := null;
  else
    select coalesce(jsonb_agg(distinct m), '[]'::jsonb) into mods
      from (select jsonb_array_elements_text(coalesce(cfg.core_modules, '[]'::jsonb)) m
            union select jsonb_array_elements_text(public.beyond_keys(cfg.core_by_industry -> coalesce(b.industry, '') -> 'modules'))
            union select jsonb_array_elements_text(plan->'modules')
            union select jsonb_array_elements_text(case when jsonb_typeof(a->'modules') = 'array' then a->'modules' else '[]'::jsonb end)
            union select jsonb_array_elements_text(case when jsonb_typeof(o->'modules') = 'array' then o->'modules' else '[]'::jsonb end)) x;
  end if;
  -- switched off: by the plan (unless the company has it back or has full use), and for this company alone (always)
  select coalesce(jsonb_agg(distinct k), '[]'::jsonb) into deny
    from (select jsonb_array_elements_text(case when full_access then '[]'::jsonb else public.beyond_keys(plan->'deny') end) k
          except select jsonb_array_elements_text(public.beyond_keys(o->'allow'))
          union select jsonb_array_elements_text(public.beyond_keys(o->'deny'))) x;
  ai := case when cfg.id is not null and cfg.ai_mode = 'off' then false
             when o ? 'ai' and jsonb_typeof(o->'ai') = 'boolean' then (o->>'ai')::boolean
             when cfg.id is null or cfg.ai_mode = 'free' or full_access then true
             else coalesce((plan->>'ai')::boolean, false) or coalesce((a->>'ai')::boolean, false) end;
  sup := case when o ? 'support' and jsonb_typeof(o->'support') = 'boolean' then (o->>'support')::boolean
              when cfg.id is null or cfg.support_mode = 'free' or full_access then true
              else coalesce((plan->>'support')::boolean, true) or coalesce((a->>'support')::boolean, false) end;
  prm := case when jsonb_typeof(o->'perms') = 'boolean' then (o->>'perms')::boolean
              when full_access then true
              else coalesce((plan->>'perms')::boolean, true) end;
  nprm := case when jsonb_typeof(o->'notify_perms') = 'boolean' then (o->>'notify_perms')::boolean
               when full_access then true
               else coalesce((plan->>'notify_perms')::boolean, true) end;
  return jsonb_build_object('modules', mods, 'ai', ai, 'support', sup, 'deny', deny, 'industry', b.industry,
                            'perms', prm, 'notify_perms', nprm);
end $$;

-- ================================================================== the plans' new switches are checked when saved
create or replace function public.beyond_plans_check() returns trigger
language plpgsql as $$
begin
  if exists (select 1 from jsonb_array_elements(new.plans) x
             where (x ? 'companies' and jsonb_typeof(x->'companies') not in ('number', 'null'))
                or (jsonb_typeof(x->'companies') = 'number' and ((x->>'companies')::numeric < 1 or (x->>'companies')::numeric <> trunc((x->>'companies')::numeric)))
                or (x ? 'perms' and jsonb_typeof(x->'perms') <> 'boolean')
                or (x ? 'notify_perms' and jsonb_typeof(x->'notify_perms') <> 'boolean')) then
    raise exception 'a plan''s companies must be a whole number of 1 or more (or blank), and its permission switches on or off';
  end if;
  if not exists (select 1 from jsonb_array_elements(new.plans) x where x->>'code' = new.trial_plan) then
    raise exception 'the trial follows a plan that is not in the list — choose the trial''s plan again';
  end if;
  return new;
end $$;
drop trigger if exists billing_plans_check on public.billing_config;
create trigger billing_plans_check before insert or update of plans, trial_plan on public.billing_config
  for each row execute function public.beyond_plans_check();

-- ================================================================== new companies: limits per person, per plan, new customers
create or replace function public.beyond_company_rules() returns trigger
language plpgsql security definer set search_path = public as $$
declare cfg billing_config; who text := lower(coalesce(new.created_by, '')); have int; lim int; person int; unlimited boolean; best int;
begin
  if who = '' or exists (select 1 from platform_admins where lower(email) = who and active) then return new; end if;
  select * into cfg from billing_config where id = 1;
  select count(*) into have from companies where lower(coalesce(created_by, '')) = who;
  if have = 0 and cfg.id is not null and not cfg.accept_new then
    raise exception 'BeyondERP is not taking new customers at the moment — please contact us' using errcode = 'P0402';
  end if;
  select pp.max_companies into person from platform_people pp where pp.email = who;
  if person is not null then
    lim := person;                                   -- the number set for this person in the console
  else
    lim := coalesce(cfg.max_companies, 0);
    if cfg.id is not null and cfg.enabled and have > 0 then
      -- each company the person owns allows its plan's number (or the general setting); the most generous one counts
      select bool_or(n = 0), max(n) into unlimited, best
        from (select coalesce(case when jsonb_typeof(pl->'companies') = 'number' then (pl->>'companies')::int end, cfg.max_companies, 0) n
                from companies c
                cross join lateral (select public.beyond_plan(public.beyond_billing_state(c.id)->>'plan') pl) x
               where lower(coalesce(c.created_by, '')) = who) y;
      lim := case when unlimited then 0 else coalesce(best, lim) end;
    end if;
  end if;
  if lim > 0 and have >= lim then
    raise exception 'You can have up to % compan% on your plan — upgrade the plan or contact us to add more', lim, case when lim = 1 then 'y' else 'ies' end
      using errcode = 'P0402';
  end if;
  return new;
end $$;
drop trigger if exists beyond_company_rules on public.companies;
create trigger beyond_company_rules before insert on public.companies for each row execute function public.beyond_company_rules();

-- ================================================================== changes: no permission editing where the plan has none
-- A change restricts someone when it sets a user's permissions to anything less than full use of a section, screen or
-- tab, switches an action off, or limits the projects they see.
create or replace function public.beyond_restricts(p_body text) returns boolean
language plpgsql immutable as $$
declare b jsonb; pm jsonb;
begin
  begin
    b := p_body::jsonb;
  exception when others then
    return false;
  end;
  if jsonb_typeof(b) <> 'object' then return false; end if;
  if jsonb_typeof(b->'projects') = 'array' and jsonb_array_length(b->'projects') > 0 then return true; end if;
  pm := b->'perms';
  if jsonb_typeof(pm) <> 'object' then return false; end if;
  return exists (select 1 from jsonb_each(pm) e
                  where (left(e.key, 1) <> '_' and e.value <> '"edit"'::jsonb)
                     or (e.key = '_bus' and jsonb_typeof(e.value) = 'array' and jsonb_array_length(e.value) > 0));
end $$;

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
  if p_method in ('POST', 'PUT') and p_path ~ '^/api/users(/[0-9]+)?/?$' and st->>'perms' = 'false' and public.beyond_restricts(p_body) then
    raise exception 'not_in_plan: % does not include user permissions — every user has full use of the plan''s sections. Choosing what each user sees comes with a plan that includes it.',
      coalesce(st->>'plan_name', 'this plan') using errcode = 'P0402';
  end if;
  if p_method = 'PUT' and p_path ~ '^/api/alert-access/?$' and st->>'notify_perms' = 'false' then
    raise exception 'not_in_plan: % does not include notification permissions — everyone receives the standard notifications. Choosing who receives which comes with a plan that includes it.',
      coalesce(st->>'plan_name', 'this plan') using errcode = 'P0402';
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

-- ================================================================== the website's texts
create table if not exists public.site_texts (
  key        text primary key check (key ~ '^[a-z0-9_]+(\.[a-z0-9_]+){1,3}$'),
  en         text not null default '' check (length(en) <= 4000),
  ar         text not null default '' check (length(ar) <= 4000),
  updated_at timestamptz not null default now(),
  updated_by text
);
alter table public.site_texts enable row level security;          -- read and written through the functions below only
revoke all on public.site_texts from anon, authenticated;

-- what the public pages show instead of their built-in wording: {key: {en, ar}} (a blank language keeps the built-in one)
create or replace function public.site_texts() returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_object_agg(key, jsonb_build_object('en', en, 'ar', ar)), '{}'::jsonb) from site_texts
$$;
grant execute on function public.site_texts() to anon, authenticated;

-- for the console: every text with who changed it and when
create or replace function public.site_texts_admin() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('site');
  return coalesce((select jsonb_object_agg(key, jsonb_build_object('en', en, 'ar', ar, 'updated_at', updated_at, 'updated_by', updated_by))
                     from site_texts), '{}'::jsonb);
end $$;

-- p: {key: {en, ar}}. Both blank: back to the built-in wording.
create or replace function public.site_texts_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare e record; en text; ar text; n int := 0;
begin
  perform public.beyond_require_staff('site');
  if jsonb_typeof(p) <> 'object' then raise exception 'send the texts as {key: {en, ar}}'; end if;
  if (select count(*) from jsonb_object_keys(p)) > 500 then raise exception 'up to 500 texts at a time'; end if;
  for e in select key, value from jsonb_each(p) loop
    if e.key !~ '^[a-z0-9_]+(\.[a-z0-9_]+){1,3}$' then raise exception 'unknown text %', e.key; end if;
    if jsonb_typeof(e.value) <> 'object' then raise exception 'text % needs {en, ar}', e.key; end if;
    en := btrim(coalesce(e.value->>'en', ''));
    ar := btrim(coalesce(e.value->>'ar', ''));
    if length(en) > 4000 or length(ar) > 4000 then raise exception 'text % is too long (4000 characters at most)', e.key; end if;
    if en = '' and ar = '' then
      delete from site_texts where key = e.key;
    else
      insert into site_texts(key, en, ar, updated_at, updated_by) values (e.key, en, ar, now(), public.beyond_caller())
        on conflict (key) do update set en = excluded.en, ar = excluded.ar, updated_at = now(), updated_by = excluded.updated_by
        where site_texts.en is distinct from excluded.en or site_texts.ar is distinct from excluded.ar;
    end if;
    n := n + 1;
  end loop;
  perform public.beyond_log('site.texts', n || ' text(s)', (select jsonb_agg(k) from jsonb_object_keys(p) k));
  return public.site_texts();
end $$;
revoke execute on function public.site_texts_admin(), public.site_texts_save(jsonb) from public, anon;
grant execute on function public.site_texts_admin(), public.site_texts_save(jsonb) to authenticated;

-- ================================================================== the website's layout, links, pictures and clips
-- One record the console edits (Website → Layout, Pictures & clips, Links), read by the public pages with the texts:
--   announcement {on, en, ar, link, link_en, link_ar, tone: info|ok|warn, where: home|signin|both}
--   hide         [books, projects, systems, forecast, end, band, email, links]   home page parts not shown
--   showcase     {on, media, kind: image|video}       a block with a picture or a clip (its words are texts)
--   media        {logo, hero, hero_kind, signin}      the platform's logo, the home page's background, the sign-in background
--   links        [{en, ar, url}]                      the footer's links (none set: the built-in ones)
-- Pictures and clips live in the public bucket site-media under site/…; only the owner and team members given
-- "Website texts" may add or remove them.
create table if not exists public.site_config (
  id         int primary key default 1 check (id = 1),
  value      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  updated_by text
);
insert into public.site_config(id) values (1) on conflict (id) do nothing;
alter table public.site_config enable row level security;
revoke all on public.site_config from anon, authenticated;

create or replace function public.beyond_site_path(v jsonb) returns boolean
language sql immutable as $$
  select v is null or v = 'null'::jsonb or v = '""'::jsonb
      or (jsonb_typeof(v) = 'string' and (v #>> '{}') ~ '^site/[A-Za-z0-9][A-Za-z0-9._-]{0,150}$')
$$;

create or replace function public.beyond_site_url(v jsonb) returns boolean
language sql immutable as $$
  select v is null or v = 'null'::jsonb or v = '""'::jsonb
      or (jsonb_typeof(v) = 'string' and length(v #>> '{}') between 11 and 500 and (v #>> '{}') ~ '^https://[^\s"''<>]+$')
$$;

create or replace function public.site_config_save(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare a jsonb := p->'announcement'; m jsonb := p->'media'; sc jsonb := p->'showcase'; x jsonb;
begin
  perform public.beyond_require_staff('site');
  if jsonb_typeof(p) <> 'object' then raise exception 'send the website settings as an object'; end if;
  if exists (select 1 from jsonb_object_keys(p) k where k not in ('announcement', 'hide', 'showcase', 'media', 'links')) then
    raise exception 'unknown website setting';
  end if;
  if a is not null and (jsonb_typeof(a) <> 'object'
      or exists (select 1 from jsonb_object_keys(a) k where k not in ('on', 'en', 'ar', 'link', 'link_en', 'link_ar', 'tone', 'where'))
      or (a ? 'on' and jsonb_typeof(a->'on') <> 'boolean')
      or length(coalesce(a->>'en', '')) > 300 or length(coalesce(a->>'ar', '')) > 300
      or length(coalesce(a->>'link_en', '')) > 40 or length(coalesce(a->>'link_ar', '')) > 40
      or not public.beyond_site_url(a->'link')
      or coalesce(a->>'tone', 'info') not in ('info', 'ok', 'warn') or coalesce(a->>'where', 'home') not in ('home', 'signin', 'both')) then
    raise exception 'the announcement needs its text (300 characters at most), an https:// link or none, a style and where it shows';
  end if;
  if p ? 'hide' and (jsonb_typeof(p->'hide') <> 'array' or exists (select 1 from jsonb_array_elements_text(p->'hide') h
      where h not in ('books', 'projects', 'systems', 'forecast', 'end', 'band', 'email', 'links'))) then
    raise exception 'unknown part of the home page';
  end if;
  if sc is not null and (jsonb_typeof(sc) <> 'object'
      or exists (select 1 from jsonb_object_keys(sc) k where k not in ('on', 'media', 'kind'))
      or not public.beyond_site_path(sc->'media') or coalesce(sc->>'kind', 'image') not in ('image', 'video')) then
    raise exception 'the showcase needs a picture or a clip from Pictures & clips';
  end if;
  if m is not null and (jsonb_typeof(m) <> 'object'
      or exists (select 1 from jsonb_object_keys(m) k where k not in ('logo', 'hero', 'hero_kind', 'signin'))
      or not public.beyond_site_path(m->'logo') or not public.beyond_site_path(m->'hero') or not public.beyond_site_path(m->'signin')
      or coalesce(m->>'hero_kind', 'image') not in ('image', 'video')) then
    raise exception 'pictures and clips must be ones uploaded in Pictures & clips';
  end if;
  if p ? 'links' then
    if jsonb_typeof(p->'links') <> 'array' or jsonb_array_length(p->'links') > 12 then raise exception 'up to 12 links'; end if;
    for x in select value from jsonb_array_elements(p->'links') loop
      if jsonb_typeof(x) <> 'object' or coalesce(btrim(x->>'en'), '') = '' or length(x->>'en') > 40 or length(coalesce(x->>'ar', '')) > 40
         or coalesce(x->>'url', '') = '' or not public.beyond_site_url(x->'url') then
        raise exception 'each link needs a name (40 characters at most) and an https:// address';
      end if;
    end loop;
  end if;
  update site_config set value = p, updated_at = now(), updated_by = public.beyond_caller() where id = 1;
  perform public.beyond_log('site.config', null, p);
  return p;
end $$;

-- everything the public pages show from the console: {texts: {key: {en, ar}}, site: {…}}
create or replace function public.site_public() returns jsonb
language sql stable security definer set search_path = public as $$
  select jsonb_build_object('texts', public.site_texts(), 'site', coalesce((select value from site_config where id = 1), '{}'::jsonb))
$$;
grant execute on function public.site_public() to anon, authenticated;
revoke execute on function public.site_config_save(jsonb) from public, anon;
grant execute on function public.site_config_save(jsonb) to authenticated;

insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('site-media', 'site-media', true, 31457280, array['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'video/mp4', 'video/webm'])
on conflict (id) do update set public = true, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;
drop policy if exists "beyond site media add" on storage.objects;
create policy "beyond site media add" on storage.objects for insert to authenticated
  with check (bucket_id = 'site-media' and (storage.foldername(name))[1] = 'site' and public.beyond_staff_can('site'));
drop policy if exists "beyond site media change" on storage.objects;
create policy "beyond site media change" on storage.objects for update to authenticated
  using (bucket_id = 'site-media' and public.beyond_staff_can('site'));
drop policy if exists "beyond site media remove" on storage.objects;
create policy "beyond site media remove" on storage.objects for delete to authenticated
  using (bucket_id = 'site-media' and public.beyond_staff_can('site'));
drop policy if exists "beyond site media read" on storage.objects;
create policy "beyond site media read" on storage.objects for select to authenticated
  using (bucket_id = 'site-media' and public.beyond_staff_can('site'));
