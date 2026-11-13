-- Patch notes ("What's new"), in English and Arabic.
--
--   * Every release adds a draft, written from what changed (the deploy workflow calls patch_note_draft once per release;
--     running it again for the same release changes nothing, so an edited draft is never overwritten).
--   * The team reviews, edits and publishes them: the owner console and the support desk (the Help & FAQ permission).
--   * Customers see only what was published, in the app's What's new.

create table if not exists public.patch_notes (
  id            bigserial primary key,
  release_sha   text unique,                      -- the release a draft was written from (none for one written by hand)
  version       text not null default '',
  title         text not null default '',
  title_ar      text not null default '',
  body          text not null default '',
  body_ar       text not null default '',
  status        text not null default 'draft' check (status in ('draft', 'published')),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  updated_by    text not null default '',
  published_at  timestamptz,
  published_by  text not null default ''
);
alter table public.patch_notes enable row level security;     -- no policies: read and written only through the functions below

-- ================================================================== customers: what was published
create or replace function public.patch_notes_published() returns jsonb
language sql stable security definer set search_path = public as $$
  select coalesce(jsonb_agg(jsonb_build_object('id', n.id, 'version', n.version, 'title', n.title, 'title_ar', n.title_ar,
                                               'body', n.body, 'body_ar', n.body_ar, 'date', n.published_at::date)
                            order by n.published_at desc, n.id desc), '[]'::jsonb)
    from (select * from patch_notes where status = 'published' order by published_at desc, id desc limit 100) n
$$;

-- ================================================================== the team: review, edit, publish
create or replace function public.console_patch_notes() returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  perform public.beyond_require_desk('help');
  return coalesce((select jsonb_agg(to_jsonb(n) order by (n.status = 'published'), coalesce(n.published_at, n.created_at) desc, n.id desc)
                     from patch_notes n), '[]'::jsonb);
end $$;

create or replace function public.console_patch_note(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare nid bigint := nullif(p->>'id', '')::bigint; act text := coalesce(p->>'action', 'save');
begin
  perform public.beyond_require_desk('help');
  if act = 'delete' then
    delete from patch_notes where id = nid;
  elsif act in ('publish', 'unpublish') then
    if act = 'publish' and exists (select 1 from patch_notes where id = nid and (trim(title) = '' or trim(body) = '')) then
      raise exception 'write the title and the notes before publishing';
    end if;
    update patch_notes set status = case when act = 'publish' then 'published' else 'draft' end,
           published_at = case when act = 'publish' then coalesce(published_at, now()) else null end,
           published_by = case when act = 'publish' then public.beyond_caller() else '' end,
           updated_at = now(), updated_by = public.beyond_caller()
     where id = nid;
  else
    if coalesce(trim(p->>'title'), '') = '' then raise exception 'give the notes a title'; end if;
    if length(coalesce(p->>'body', '')) > 20000 or length(coalesce(p->>'body_ar', '')) > 20000 then raise exception 'the notes are too long'; end if;
    if nid is null then
      insert into patch_notes(version, title, title_ar, body, body_ar, updated_by)
      values (left(trim(coalesce(p->>'version', '')), 40), trim(p->>'title'), trim(coalesce(p->>'title_ar', '')), trim(coalesce(p->>'body', '')),
              trim(coalesce(p->>'body_ar', '')), public.beyond_caller())
      returning id into nid;
    else
      update patch_notes set version = left(trim(coalesce(p->>'version', version)), 40), title = trim(p->>'title'),
             title_ar = trim(coalesce(p->>'title_ar', '')), body = trim(coalesce(p->>'body', '')), body_ar = trim(coalesce(p->>'body_ar', '')),
             updated_at = now(), updated_by = public.beyond_caller()
       where id = nid;
    end if;
  end if;
  perform public.beyond_log('patch_notes.' || act, coalesce(nid::text, ''), p - 'body' - 'body_ar');
  return public.console_patch_notes();
end $$;

-- ================================================================== the deploy: a draft per release
create or replace function public.patch_note_draft(p jsonb) returns void
language sql security definer set search_path = public as $$
  insert into patch_notes(release_sha, version, title, body, updated_by)
  values (left(p->>'sha', 64), left(coalesce(p->>'version', ''), 40), left(coalesce(nullif(p->>'title', ''), 'New release'), 200),
          left(coalesce(p->>'body', ''), 20000), 'release')
  on conflict (release_sha) do nothing
$$;

grant execute on function public.patch_notes_published() to anon, authenticated;
grant execute on function public.console_patch_notes(), public.console_patch_note(jsonb) to authenticated;
revoke all on function public.console_patch_notes(), public.console_patch_note(jsonb) from public, anon;
revoke all on function public.patch_note_draft(jsonb) from public, anon, authenticated;
