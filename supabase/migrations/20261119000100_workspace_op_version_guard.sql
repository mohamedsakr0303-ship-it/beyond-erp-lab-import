-- Reject unsupported client versions at the database boundary, not only in the desktop relay.
-- This prevents an authenticated workspace member from writing a future version into the shared log
-- and making every replica refuse to replay it.
--
-- Keep the 1.3.0 ceiling in sync with erp.__version__ when a new application release is approved.
create or replace function public.append_op(p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp) returns json
language plpgsql security definer set search_path = public as $$
declare m members; last bigint; who text; route text;
begin
  select * into m from members where lower(email) = public.beyond_caller() and active;
  if m.email is null then raise exception 'not a member of this workspace' using errcode = '42501'; end if;

  -- Exactly three numeric components; reject malformed and future versions before taking a sequence number.
  if p_version is null or p_version !~ '^[0-9]+\.[0-9]+\.[0-9]+$' then
    raise exception 'invalid workspace client version' using errcode = '22023';
  end if;
  if string_to_array(p_version, '.')::integer[] > array[1, 3, 0]::integer[] then
    raise exception 'workspace client version % is newer than the supported version 1.3.0', p_version
      using errcode = '22023';
  end if;

  if p_method not in ('POST', 'PUT', 'DELETE') or left(p_path, 5) <> '/api/' then
    raise exception 'invalid change' using errcode = '22023';
  end if;

  -- Mirror cloud.LOCAL_ONLY at the database boundary: callers must not be able to make every replica
  -- repeat device-local work, leak a device secret, or submit the same government e-invoice repeatedly.
  route := split_part(p_path, '?', 1);
  if (p_method = 'POST' and route in (
        '/api/login', '/api/logout', '/api/setup', '/api/api-keys', '/api/maintenance',
        '/api/offers-preview', '/api/einvoice/test', '/api/einvoice/submit', '/api/ereceipts-submit',
        '/api/me/two-step', '/api/me/two-step/off'
      ))
     or (p_method = 'PUT' and route = '/api/me/password')
     or (p_method = 'DELETE' and route ~ '^/api/api-keys/[0-9]+
  perform pg_advisory_xact_lock(424202);
  select coalesce(max(seq), 0) into last from ops;
  if p_seq <> last + 1 then
    raise exception 'conflict: next change is %', last + 1 using errcode = '40001';
  end if;
  who := case when m.gateway and coalesce(p_app_user, '') <> '' then p_app_user else m.email end;
  insert into ops(seq, version, actor, app_user, device, method, path, body, content_type, clock)
  values (p_seq, p_version, m.email, who, left(p_device, 80), p_method, p_path, p_body, p_content_type, p_clock);
  return json_build_object('seq', p_seq, 'app_user', who);
end $$;
)
     or (p_method = 'POST' and route ~ '^/api/einvoice/[0-9]+/(refresh|cancel)
  perform pg_advisory_xact_lock(424202);
  select coalesce(max(seq), 0) into last from ops;
  if p_seq <> last + 1 then
    raise exception 'conflict: next change is %', last + 1 using errcode = '40001';
  end if;
  who := case when m.gateway and coalesce(p_app_user, '') <> '' then p_app_user else m.email end;
  insert into ops(seq, version, actor, app_user, device, method, path, body, content_type, clock)
  values (p_seq, p_version, m.email, who, left(p_device, 80), p_method, p_path, p_body, p_content_type, p_clock);
  return json_build_object('seq', p_seq, 'app_user', who);
end $$;
)
     or (p_method = 'POST' and route ~ '^/api/[[:alnum:]_-]+-preview(/[[:alnum:]_-]+)?
  perform pg_advisory_xact_lock(424202);
  select coalesce(max(seq), 0) into last from ops;
  if p_seq <> last + 1 then
    raise exception 'conflict: next change is %', last + 1 using errcode = '40001';
  end if;
  who := case when m.gateway and coalesce(p_app_user, '') <> '' then p_app_user else m.email end;
  insert into ops(seq, version, actor, app_user, device, method, path, body, content_type, clock)
  values (p_seq, p_version, m.email, who, left(p_device, 80), p_method, p_path, p_body, p_content_type, p_clock);
  return json_build_object('seq', p_seq, 'app_user', who);
end $$;
)
     or route ~ '^/api/(cloud|ai|lock)(/|$)'
     or (p_method = 'POST' and route = '/api/import' and p_path ~ '([?&])dry_run=1(&|$)') then
    raise exception 'device-local operations cannot be appended to the shared workspace log' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(424202);
  select coalesce(max(seq), 0) into last from ops;
  if p_seq <> last + 1 then
    raise exception 'conflict: next change is %', last + 1 using errcode = '40001';
  end if;
  who := case when m.gateway and coalesce(p_app_user, '') <> '' then p_app_user else m.email end;
  insert into ops(seq, version, actor, app_user, device, method, path, body, content_type, clock)
  values (p_seq, p_version, m.email, who, left(p_device, 80), p_method, p_path, p_body, p_content_type, p_clock);
  return json_build_object('seq', p_seq, 'app_user', who);
end $$;


-- Snapshot writes are privileged, but their version is still supplied by the caller. Do not let a gateway or
-- administrator poison future joins with a snapshot version unsupported by this application.
create or replace function public.put_snapshot(p_seq bigint, p_version text, p_data text) returns bigint
language plpgsql security definer set search_path = public as $$
declare m members;
begin
  select * into m from members where lower(email) = public.beyond_caller() and active;
  if m.email is null or not (m.is_admin or m.gateway) then
    raise exception 'only administrators can save workspace copies' using errcode = '42501';
  end if;
  if p_version is null or p_version !~ '^[0-9]+\.[0-9]+\.[0-9]+$' then
    raise exception 'invalid workspace client version' using errcode = '22023';
  end if;
  if string_to_array(p_version, '.')::integer[] > array[1, 3, 0]::integer[] then
    raise exception 'workspace client version % is newer than the supported version 1.3.0', p_version
      using errcode = '22023';
  end if;
  if p_seq > (select coalesce(max(seq), 0) from ops) then raise exception 'snapshot is ahead of the log'; end if;
  insert into snapshots(seq, version, data, created_by) values (p_seq, p_version, p_data, m.email)
  on conflict (seq) do update set version = excluded.version, data = excluded.data, created_by = excluded.created_by,
                                  created_at = now();
  delete from snapshots where seq not in (select seq from snapshots order by seq desc limit 3);
  return p_seq;
end $$;
