-- A device whose books do not yet show its account as the company's super administrator (a desktop set up on its own
-- first, then connected) says so through the log: "POST /api/users/workspace-role", replayed on every device. Any
-- member could append such a change, so the workspace stores it only from an account it counts as a super
-- administrator of the company (its creator, or one marked so) — otherwise nobody could trust it.
create or replace function public.beyond_ops_guard() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if split_part(coalesce(new.path, ''), '?', 1) = '/api/users/workspace-role' and not public.beyond_is_super(new.company_id) then
    raise exception 'only a super administrator of this company can say so' using errcode = '42501';
  end if;
  return new;
end $$;
revoke all on function public.beyond_ops_guard() from public, anon, authenticated;
drop trigger if exists beyond_ops_guard on public.ops;
create trigger beyond_ops_guard before insert on public.ops for each row execute function public.beyond_ops_guard();
