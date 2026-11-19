-- Reject unsupported client versions at the database boundary, not only in the desktop relay.
-- A direct authenticated RPC call must not be able to poison the company log or a new-device snapshot.
--
-- The company-scoped wrappers were introduced in 2026-11-14. Keep their signatures and large-payload
-- handling intact: validate first, then delegate to the original company-scoped functions.
-- Keep the 1.3.0 ceiling in sync with erp.__version__ when a new application release is approved.

create or replace function public.append_op(
  p_company uuid, p_seq bigint, p_version text, p_app_user text, p_device text,
  p_method text, p_path text, p_body text, p_content_type text, p_clock timestamp
) returns json
language plpgsql security definer set search_path = public as $$
declare
  r json;
  route text;
begin
  -- Reject malformed and unsupported versions before any sequence number or payload part is committed.
  if p_version is null or p_version !~ '^[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}$' then
    raise exception 'invalid workspace client version' using errcode = '22023';
  end if;
  if string_to_array(p_version, '.')::numeric[] > array[1, 3, 0]::numeric[] then
    raise exception 'workspace client version % is newer than the supported version 1.3.0', p_version
      using errcode = '22023';
  end if;

  if p_method is null or p_path is null
     or p_method not in ('POST', 'PUT', 'DELETE') or left(p_path, 5) <> '/api/' then
    raise exception 'invalid change' using errcode = '22023';
  end if;

  -- Mirror cloud.LOCAL_ONLY. The RPC is callable directly by authenticated members; do not trust client filters.
  route := split_part(p_path, '?', 1);
  if (p_method = 'POST' and route in (
        '/api/login', '/api/logout', '/api/setup', '/api/api-keys', '/api/maintenance',
        '/api/offers-preview', '/api/einvoice/test', '/api/einvoice/submit', '/api/ereceipts-submit',
        '/api/me/two-step', '/api/me/two-step/off'
      ))
     or (p_method = 'PUT' and route = '/api/me/password')
     or (p_method = 'DELETE' and route ~ '^/api/api-keys/[0-9]+$')
     or (p_method = 'POST' and route ~ '^/api/einvoice/[0-9]+/(refresh|cancel)$')
     or (p_method = 'POST' and route ~ '^/api/[[:alnum:]_-]+-preview(/[[:alnum:]_-]+)?$')
     or route ~ '^/api/(cloud|ai|lock)(/|$)'
     or (p_method = 'POST' and route = '/api/import' and p_path ~ '([?&])dry_run=1(&|$)') then
    raise exception 'device-local operations cannot be appended to the shared workspace log' using errcode = '22023';
  end if;

  -- Delegation preserves company membership, workspace-role authorization, per-company sequence locking,
  -- actor attribution, and storage. The wrapper also validates all uploaded parts in the same transaction.
  r := public.beyond_append_op(
    p_company, p_seq, p_version, p_app_user, p_device, p_method, p_path,
    p_body, p_content_type, p_clock
  );
  perform public.beyond_take_parts(p_company, p_body, 'op');
  return r;
end $$;


create or replace function public.put_snapshot(
  p_company uuid, p_seq bigint, p_version text, p_data text
) returns bigint
language plpgsql security definer set search_path = public as $$
declare
  r bigint;
begin
  if p_version is null or p_version !~ '^[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}$' then
    raise exception 'invalid workspace client version' using errcode = '22023';
  end if;
  if string_to_array(p_version, '.')::numeric[] > array[1, 3, 0]::numeric[] then
    raise exception 'workspace client version % is newer than the supported version 1.3.0', p_version
      using errcode = '22023';
  end if;

  -- Preserve the original authorization, sequence checks, and per-company snapshot storage.
  r := public.beyond_put_snapshot(p_company, p_seq, p_version, p_data);
  perform public.beyond_take_parts(p_company, p_data, 'snap');

  -- Match the 2026-11-14 wrapper: discard pieces for snapshots no longer retained.
  delete from op_parts p
  where p.company_id = p_company and p.kind = 'snap'
    and not exists (
      select 1 from snapshots s
      where s.company_id = p_company
        and left(s.data, 64) like 'parts:' || p.part_id || ':%'
    );
  return r;
end $$;
