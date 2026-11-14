-- Large changes and company copies arrive in pieces.
-- A single call to the database is stopped after a few seconds (the statement timeout), so a big import or a big copy
-- of the books sent in one call failed. The app now sends anything large as pieces of about 250 KB (put_part), each its
-- own short call, and then the change (append_op) or the copy (put_snapshot) whose text is only "parts:<id>:<count>".
-- Both check that every piece arrived before accepting it. Devices read the pieces back one by one.
-- append_op and put_snapshot keep all their checks: the earlier functions are renamed and called from thin wrappers.

create table if not exists public.op_parts (
  company_id uuid not null references public.companies(id) on delete cascade,
  part_id    text not null check (part_id ~ '^[a-z0-9]{16,40}$'),
  idx        int  not null check (idx >= 0 and idx < 4096),
  data       text not null check (length(data) <= 1200000),
  kind       text not null default '' check (kind in ('', 'op', 'snap')),   -- '' = not used yet
  created_by text,
  created_at timestamptz not null default now(),
  primary key (company_id, part_id, idx)
);
alter table public.op_parts enable row level security;
revoke all on public.op_parts from anon, authenticated;
grant select on public.op_parts to authenticated;
drop policy if exists "members read parts" on public.op_parts;
create policy "members read parts" on public.op_parts for select to authenticated using (public.beyond_in(company_id));

-- one piece of a large change or copy (any active member; pieces nothing ends up using are removed after a day)
create or replace function public.put_part(p_company uuid, p_id text, p_idx int, p_data text) returns int
language plpgsql security definer set search_path = public as $$
declare m members; waiting int;
begin
  m := public.beyond_member(p_company);
  if m.email is null or m.username = 'support:view' then
    raise exception 'not a member of this company' using errcode = '42501';
  end if;
  if coalesce(p_id, '') !~ '^[a-z0-9]{16,40}$' or p_idx is null or p_idx < 0 or p_idx >= 4096
     or p_data is null or length(p_data) > 1200000 then
    raise exception 'invalid part' using errcode = '22023';
  end if;
  if p_idx = 0 then
    delete from op_parts where company_id = p_company and kind = '' and created_at < now() - interval '1 day';
  end if;
  select count(*) into waiting from op_parts where company_id = p_company and kind = '';
  if waiting >= 2000 then
    raise exception 'too many unfinished uploads for this company — try again tomorrow' using errcode = '54000';
  end if;
  insert into op_parts(company_id, part_id, idx, data, created_by) values (p_company, p_id, p_idx, p_data, m.email)
  on conflict (company_id, part_id, idx) do update set data = excluded.data where op_parts.kind = '';
  return p_idx;
end $$;

-- "parts:<id>:<count>": every piece there (and no more) → marked as used by an op or a copy. Anything else: nothing.
create or replace function public.beyond_take_parts(p_company uuid, p_ref text, p_kind text) returns void
language plpgsql security definer set search_path = public as $$
declare pid text; n int; have int; extra boolean;
begin
  if p_ref is null or left(p_ref, 6) <> 'parts:' then return; end if;
  if p_ref !~ '^parts:[a-z0-9]{16,40}:[1-9][0-9]{0,3}$' then
    raise exception 'invalid parts' using errcode = '22023';
  end if;
  pid := split_part(p_ref, ':', 2); n := split_part(p_ref, ':', 3)::int;
  select count(*) filter (where idx < n), bool_or(idx >= n) into have, extra
    from op_parts where company_id = p_company and part_id = pid;
  if have <> n or coalesce(extra, false) then
    raise exception 'some pieces of this upload did not arrive — please try again' using errcode = '22023';
  end if;
  update op_parts set kind = p_kind where company_id = p_company and part_id = pid and kind = '';
end $$;

alter function public.append_op(uuid, bigint, text, text, text, text, text, text, text, timestamp) rename to beyond_append_op;
create or replace function public.append_op(p_company uuid, p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language plpgsql security definer set search_path = public as $$
declare r json;
begin
  r := public.beyond_append_op(p_company, p_seq, p_version, p_app_user, p_device, p_method, p_path, p_body, p_content_type, p_clock);
  perform public.beyond_take_parts(p_company, p_body, 'op');
  return r;
end $$;

alter function public.put_snapshot(uuid, bigint, text, text) rename to beyond_put_snapshot;
create or replace function public.put_snapshot(p_company uuid, p_seq bigint, p_version text, p_data text) returns bigint
language plpgsql security definer set search_path = public as $$
declare r bigint;
begin
  r := public.beyond_put_snapshot(p_company, p_seq, p_version, p_data);
  perform public.beyond_take_parts(p_company, p_data, 'snap');
  -- the pieces of copies no longer kept (only the latest three are)
  delete from op_parts p where p.company_id = p_company and p.kind = 'snap'
    and not exists (select 1 from snapshots s where s.company_id = p_company and left(s.data, 64) like 'parts:' || p.part_id || ':%');
  return r;
end $$;

revoke execute on function public.beyond_append_op(uuid, bigint, text, text, text, text, text, text, text, timestamp),
  public.beyond_put_snapshot(uuid, bigint, text, text), public.beyond_take_parts(uuid, text, text)
  from public, anon, authenticated;
revoke execute on function public.put_part(uuid, text, int, text), public.append_op(uuid, bigint, text, text, text, text, text, text, text, timestamp),
  public.put_snapshot(uuid, bigint, text, text) from public, anon;
grant execute on function public.put_part(uuid, text, int, text),
  public.append_op(uuid, bigint, text, text, text, text, text, text, text, timestamp),
  public.put_snapshot(uuid, bigint, text, text) to authenticated;
