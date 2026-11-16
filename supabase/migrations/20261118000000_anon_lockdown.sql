-- A visitor who is not signed in (the anon role, the key every web page carries) may run only what the public site
-- and the public link pages need. PostgreSQL lets everyone run a new function unless told otherwise; each function
-- here already refuses a caller it does not know, but one forgotten check would then be open to the whole internet.
-- Signed-in people keep exactly what they had: where everyone could run a function, the signed-in role is given it.
do $$
declare f record;
  public_ok text[] := array['billing_public', 'help_center', 'help_search', 'patch_notes_published', 'site_public',
                            'site_texts', 'public_page', 'public_send', 'public_manage'];
begin
  for f in select p.oid::regprocedure as sig, p.proname
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.prokind = 'f' and has_function_privilege('anon', p.oid, 'execute')
              and not (p.proname = any(public_ok))
  loop
    if has_function_privilege('authenticated', f.sig, 'execute') then
      execute format('grant execute on function %s to authenticated', f.sig);
    end if;
    if has_function_privilege('service_role', f.sig, 'execute') then     -- the server's own functions (mail, push, billing)
      execute format('grant execute on function %s to service_role', f.sig);
    end if;
    execute format('revoke execute on function %s from public, anon', f.sig);
  end loop;
end $$;

-- and from now on: a new function is for signed-in people only, unless its migration grants it to anon
-- (PostgreSQL's own "everyone" grant can only be taken back globally, not for one schema)
alter default privileges revoke execute on functions from public;
alter default privileges in schema public revoke execute on functions from anon;
alter default privileges in schema public grant execute on functions to authenticated, service_role;
