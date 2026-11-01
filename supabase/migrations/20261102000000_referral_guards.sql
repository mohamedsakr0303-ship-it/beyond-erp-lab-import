-- Referral program and upgrades: closing the ways they could be misused.
--
--   * A paying customer is anyone who completed an order, or who administers a company with a paid payment (also one
--     recorded by the platform team): neither can claim a link or count as a first subscription.
--   * Nobody earns from their own company: a link does not count between people who share a company, the invited
--     person's discount does not apply to a company of the person who shared, and a payment for such a company earns
--     no reward. Two people cannot claim each other's links.
--   * The invited person's discount and every reward are held by an order that is open, sent or rejected (a rejected
--     order can be sent again), so neither is used twice; orders are placed one at a time per person; and approving
--     checks again that the discount is still unused, otherwise it stops.
--   * A reward is earned only by the invited person's first completed payment.
--   * An upgrade cannot lower the plan or the extra users, and approving it keeps what the company bought meanwhile.

-- ================================================================== who is already a paying customer
create or replace function public.beyond_has_paid(p_email text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from billing_orders where lower(email) = lower(p_email) and status = 'approved' and amount > 0)
      or exists (select 1 from members m join billing_payments p on p.company_id = m.company_id
                  where lower(m.email) = lower(p_email) and m.is_admin and m.active and p.status = 'paid' and p.amount > 0)
$$;

-- do these two people share a company (as members or as the one who created it)?
create or replace function public.beyond_ref_tied(p_a text, p_b text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from companies c
                  where (lower(coalesce(c.created_by, '')) = lower(p_a) or exists (select 1 from members m where m.company_id = c.id and lower(m.email) = lower(p_a)))
                    and (lower(coalesce(c.created_by, '')) = lower(p_b) or exists (select 1 from members m where m.company_id = c.id and lower(m.email) = lower(p_b))))
$$;

-- is this person in (or the creator of) any company of these order lines?
create or replace function public.beyond_ref_in_lines(p_who text, p_lines jsonb) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from jsonb_array_elements(coalesce(p_lines, '[]'::jsonb)) v join companies c on c.id = (v->>'company_id')::uuid
                  where lower(coalesce(c.created_by, '')) = lower(p_who)
                     or exists (select 1 from members m where m.company_id = c.id and lower(m.email) = lower(p_who)))
$$;

-- ================================================================== claiming a link
create or replace function public.referral_claim(p_code text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare me text := public.beyond_caller(); cfg jsonb; who text; c text := upper(trim(coalesce(p_code, '')));
begin
  if me = '' then raise exception 'sign in first' using errcode = '42501'; end if;
  select referral into cfg from billing_config where id = 1;
  if not coalesce((cfg->>'on')::boolean, true) then return jsonb_build_object('ok', false, 'reason', 'off'); end if;
  select email into who from referral_codes where code = c;
  if who is null then return jsonb_build_object('ok', false, 'reason', 'unknown'); end if;
  if who = me then return jsonb_build_object('ok', false, 'reason', 'own'); end if;
  if exists (select 1 from referrals where referred = me) then return jsonb_build_object('ok', false, 'reason', 'already'); end if;
  if public.beyond_has_paid(me) then return jsonb_build_object('ok', false, 'reason', 'customer'); end if;
  if public.beyond_ref_tied(me, who) then return jsonb_build_object('ok', false, 'reason', 'team'); end if;
  if exists (select 1 from referrals where referrer = me and referred = who) then return jsonb_build_object('ok', false, 'reason', 'mutual'); end if;
  insert into referrals(referrer, referred, code) values (who, me, c) on conflict (referred) do nothing;
  return jsonb_build_object('ok', true, 'friend_pct', (cfg->>'friend_pct')::numeric);
end $$;

-- ================================================================== the discount a payment gets from the programme
-- p_lines: the companies paid for (null: only to show what is waiting). Held: by the person's open, sent or rejected orders.
create or replace function public.beyond_ref_discount(p_email text, p_kind text, p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg jsonb; e text := lower(coalesce(p_email, '')); best jsonb; rid bigint; pct numeric; first_time boolean; rf referrals;
begin
  select referral into cfg from billing_config where id = 1;
  if cfg is null or not coalesce((cfg->>'on')::boolean, true) then return null; end if;
  first_time := not public.beyond_has_paid(e);
  select * into rf from referrals where referred = e and status = 'joined';
  if first_time and rf.id is not null
     and not (p_lines is not null and public.beyond_ref_in_lines(rf.referrer, p_lines))
     and not exists (select 1 from billing_orders o where lower(o.email) = e and o.status in ('open', 'submitted', 'rejected')
                      and o.discount->>'source' = 'friend') then
    best := jsonb_build_object('source', 'friend', 'pct', coalesce((cfg->>'friend_pct')::numeric, 0));
  end if;
  pct := case when p_kind = 'upgrade' then (cfg->>'upgrade_pct')::numeric when first_time then (cfg->>'first_pct')::numeric end;
  if coalesce(pct, 0) > 0 then
    select id into rid from referral_rewards r where r.email = e and r.status = 'available'
       and not exists (select 1 from billing_orders o where o.status in ('open', 'submitted', 'rejected') and (o.discount->>'reward_id')::bigint = r.id)
     order by created_at limit 1;
    if rid is not null and pct > coalesce((best->>'pct')::numeric, 0) then
      best := jsonb_build_object('source', 'reward', 'pct', pct, 'reward_id', rid);
    end if;
  end if;
  return best;
end $$;

create or replace function public.beyond_ref_discount(p_email text, p_kind text) returns jsonb
language sql stable security definer set search_path = public as $$
  select public.beyond_ref_discount(p_email, p_kind, null)
$$;

create or replace function public.billing_order_quote(p_plan text, p_months int, p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare q jsonb; ref jsonb; lines jsonb;
begin
  perform public.beyond_require_admin_all(p_lines);
  q := public.beyond_order_price(p_plan, p_months, p_lines);
  ref := public.beyond_ref_discount(public.beyond_caller(), 'subscribe', q->'lines');
  if ref is null then return q; end if;
  lines := public.beyond_apply_ref(q->'lines', ref);
  if not exists (select 1 from jsonb_array_elements(lines) v where v ? 'discount_source') then return q; end if;
  return q || jsonb_build_object('lines', lines, 'amount', (select coalesce(sum((v->>'amount')::numeric), 0) from jsonb_array_elements(lines) v),
                                 'referral', ref);
end $$;

create or replace function public.billing_upgrade_quote(p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare q jsonb; ref jsonb; lines jsonb;
begin
  perform public.beyond_require_admin_all(p_lines);
  q := public.beyond_upgrade_price(p_lines);
  ref := public.beyond_ref_discount(public.beyond_caller(), 'upgrade', q->'lines');
  if ref is null then return q; end if;
  lines := public.beyond_apply_ref(q->'lines', ref);
  if not exists (select 1 from jsonb_array_elements(lines) v where v ? 'discount_source') then return q; end if;
  return q || jsonb_build_object('lines', lines, 'amount', (select coalesce(sum((v->>'amount')::numeric), 0) from jsonb_array_elements(lines) v),
                                 'referral', ref);
end $$;

-- one order at a time per person; the open or rejected order it replaces lets go of its discount first
create or replace function public.billing_order_open(p_plan text, p_months int, p_lines jsonb, p_method bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare q jsonb;
begin
  perform pg_advisory_xact_lock(hashtext('beyond-order:' || public.beyond_caller()));
  update billing_orders set status = 'cancelled', updated_at = now() where lower(email) = public.beyond_caller() and status in ('open', 'rejected');
  q := public.billing_order_quote(p_plan, p_months, p_lines);
  return public.beyond_order_insert('subscribe', p_plan, (q->>'months')::int, q, p_method);
end $$;

create or replace function public.billing_upgrade_open(p_lines jsonb, p_method bigint) returns jsonb
language plpgsql security definer set search_path = public as $$
declare q jsonb;
begin
  perform pg_advisory_xact_lock(hashtext('beyond-order:' || public.beyond_caller()));
  update billing_orders set status = 'cancelled', updated_at = now() where lower(email) = public.beyond_caller() and status in ('open', 'rejected');
  q := public.billing_upgrade_quote(p_lines);
  return public.beyond_order_insert('upgrade', coalesce(q->'lines'->0->>'plan', ''), 1, q, p_method);
end $$;

-- a sent or rejected order is held too: sending it again re-checks that its discount is still free
create or replace function public.billing_order_submit(p_order bigint, p_proof jsonb, p_ref text, p_amount numeric, p_note text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare o billing_orders; needs boolean;
begin
  select * into o from billing_orders where id = p_order for update;
  if o.id is null or lower(o.email) <> public.beyond_caller() then raise exception 'order not found' using errcode = '42501'; end if;
  if o.status not in ('open', 'rejected') then raise exception 'this order was already sent'; end if;
  perform public.beyond_ref_check(o);
  select coalesce(needs_proof, true) into needs from billing_methods where id = o.method_id;
  if jsonb_typeof(coalesce(p_proof, '[]')) <> 'array' or jsonb_array_length(coalesce(p_proof, '[]')) > 5 then
    raise exception 'up to 5 pictures';
  end if;
  if coalesce(needs, true) and jsonb_array_length(coalesce(p_proof, '[]')) = 0 then
    raise exception 'add a picture of the transfer';
  end if;
  if exists (select 1 from jsonb_array_elements(coalesce(p_proof, '[]')) f
             where coalesce(f->>'path', '') not like public.beyond_caller() || '/%') then
    raise exception 'pictures must be uploaded by you';
  end if;
  update billing_orders set status = 'submitted', proof = coalesce(p_proof, '[]'), proof_ref = left(nullif(trim(coalesce(p_ref, '')), ''), 120),
         proof_amount = p_amount, customer_note = left(nullif(trim(coalesce(p_note, '')), ''), 2000), submitted_at = now(),
         review_note = null, updated_at = now()
   where id = p_order;
  return public.beyond_order_row(p_order, false);
end $$;

-- the order's programme discount is still its own to use (raises otherwise)
create or replace function public.beyond_ref_check(o billing_orders) returns void
language plpgsql stable security definer set search_path = public as $$
begin
  if o.discount->>'source' = 'reward' and not exists (
       select 1 from referral_rewards where id = (o.discount->>'reward_id')::bigint and status = 'available' and email = lower(o.email)) then
    raise exception 'the referral reward on order % was already used or cancelled — cancel it and place a new order', o.ref using errcode = '22023';
  end if;
  if o.discount->>'source' = 'friend' and (
       public.beyond_has_paid(o.email)
       or not exists (select 1 from referrals where referred = lower(o.email) and status = 'joined')
       or exists (select 1 from billing_orders x where x.id <> o.id and lower(x.email) = lower(o.email)
                   and x.status in ('submitted', 'approved') and x.discount->>'source' = 'friend')) then
    raise exception 'the invitation discount on order % was already used or cancelled — cancel it and place a new order', o.ref using errcode = '22023';
  end if;
end $$;

-- ================================================================== upgrade price: never lower than what the company has
create or replace function public.beyond_upgrade_price(p_lines jsonb) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare cfg billing_config; x jsonb; cid uuid; b company_billing; st jsonb; nm text; plan jsonb; cur numeric; nxt numeric; days numeric;
        yearly boolean; factor numeric; pre numeric; disc numeric; amt numeric; total numeric := 0; out jsonb := '[]'::jsonb;
        mods jsonb; mod text; cur_mods jsonb; members_n int; users_cap int; extra int;
begin
  select * into cfg from billing_config where id = 1;
  if jsonb_typeof(p_lines) <> 'array' or jsonb_array_length(p_lines) = 0 then raise exception 'choose at least one company'; end if;
  for x in select v from jsonb_array_elements(p_lines) v loop
    cid := (x->>'company_id')::uuid;
    select name into nm from companies where id = cid;
    if nm is null then raise exception 'company not found'; end if;
    st := public.beyond_billing_state(cid);
    select * into b from company_billing where company_id = cid;
    if st->>'billing_status' <> 'active' or b.paid_until is null or b.paid_until <= now() then
      raise exception '"%" is not in a paid period — subscribe it instead', nm using errcode = '22023';
    end if;
    plan := public.beyond_plan(coalesce(nullif(x->>'plan', ''), b.plan));
    if plan is null then raise exception 'unknown plan'; end if;
    if coalesce((plan->>'contact')::boolean, false) then
      raise exception 'contact_us: the % plan is arranged with us directly', plan->>'name' using errcode = 'P0402';
    end if;
    if coalesce((plan->>'price')::numeric, 0) < coalesce((public.beyond_plan(b.plan)->>'price')::numeric, 0) then
      raise exception '"%": an upgrade keeps the current plan or a bigger one', nm using errcode = '22023';
    end if;
    extra := coalesce((x->>'extra_users')::int, b.extra_users, 0);
    if extra < coalesce(b.extra_users, 0) then
      raise exception '"%": an upgrade keeps its % extra users or adds more', nm, b.extra_users using errcode = '22023';
    end if;
    -- modules already paid for stay; new ones are added (a module the new plan includes needs no add-on)
    cur_mods := case when jsonb_typeof(b.addons->'modules') = 'array' then b.addons->'modules' else '[]'::jsonb end;
    mods := '[]'::jsonb;
    for mod in select distinct m from (select jsonb_array_elements_text(cur_mods) m
                                       union select jsonb_array_elements_text(case when jsonb_typeof(x->'modules') = 'array' then x->'modules' else '[]'::jsonb end)) q loop
      if coalesce(plan->>'modules', 'all') = 'all' or (plan->'modules') ? mod or coalesce(cfg.core_modules, '[]'::jsonb) ? mod then continue; end if;
      if not (cfg.module_prices ? mod) then raise exception 'the module "%" cannot be added on its own — choose a plan that includes it', mod; end if;
      mods := mods || to_jsonb(mod);
    end loop;
    select count(*) into members_n from members where company_id = cid and active and not pending;
    users_cap := case when jsonb_typeof(b.overrides->'users') = 'number' then (b.overrides->>'users')::int end;
    if users_cap is null and members_n > coalesce((plan->>'users')::int, 0) + extra then
      raise exception '"%" has % active people — add extra users for it', nm, members_n using errcode = '22023';
    end if;
    cur := public.beyond_monthly(b.plan, b.extra_users, b.addons);
    nxt := public.beyond_monthly(plan->>'code', extra,
             jsonb_build_object('modules', mods,
               'ai', (coalesce((x->>'ai')::boolean, false) or coalesce((b.addons->>'ai')::boolean, false)) and not coalesce((plan->>'ai')::boolean, false),
               'support', (coalesce((x->>'support')::boolean, false) or coalesce((b.addons->>'support')::boolean, false)) and coalesce((plan->>'support')::boolean, true) = false));
    if nxt <= cur then raise exception '"%": choose something more than it has now', nm using errcode = '22023'; end if;
    days := greatest(1, ceil(extract(epoch from b.paid_until - now()) / 86400));
    yearly := coalesce((select months from billing_payments where company_id = cid and status = 'paid' and kind = 'period'
                        order by paid_at desc nulls last limit 1), 1) = 12;
    factor := case when yearly then cfg.yearly_months::numeric / 12 else 1 end;
    pre := round((nxt - cur) * factor * days / 30, 2);
    disc := coalesce(b.discount_pct, 0);
    amt := round(pre * (100 - disc) / 100, 2);
    out := out || jsonb_build_object('company_id', cid, 'name', nm, 'plan', plan->>'code', 'plan_name', plan->>'name',
      'from_plan', b.plan, 'extra_users', extra, 'modules', mods,
      'ai', (coalesce((x->>'ai')::boolean, false) or coalesce((b.addons->>'ai')::boolean, false)) and not coalesce((plan->>'ai')::boolean, false),
      'support', (coalesce((x->>'support')::boolean, false) or coalesce((b.addons->>'support')::boolean, false)) and coalesce((plan->>'support')::boolean, true) = false,
      'monthly_now', cur, 'monthly_new', nxt, 'days', days, 'until', b.paid_until, 'yearly', yearly,
      'pre', pre, 'base', pre, 'pct', 100, 'discount', disc, 'amount', amt);
    total := total + amt;
  end loop;
  return jsonb_build_object('kind', 'upgrade', 'lines', out, 'amount', total, 'currency', cfg.currency);
end $$;

-- ================================================================== approving
create or replace function public.beyond_order_approve(p_order bigint, p_note text, p_txn text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare o billing_orders; x jsonb; i int := 0; pid bigint; rf referrals; b company_billing; np jsonb; was_paid boolean;
        mods jsonb; extra int; ai boolean; sup boolean;
begin
  select * into o from billing_orders where id = p_order for update;
  if o.id is null then raise exception 'order not found'; end if;
  if o.status = 'approved' then return public.beyond_order_row(p_order, true); end if;
  if o.status = 'cancelled' then raise exception 'this order was cancelled'; end if;
  perform pg_advisory_xact_lock(hashtext('beyond-order:' || lower(o.email)));
  perform public.beyond_ref_check(o);
  was_paid := public.beyond_has_paid(o.email);
  for x in select v from jsonb_array_elements(o.lines) v loop
    i := i + 1;
    if o.kind = 'upgrade' then
      -- the company has the new plan and add-ons from now, plus anything it bought meanwhile; its paid period stays
      select * into b from company_billing where company_id = (x->>'company_id')::uuid for update;
      np := public.beyond_plan(coalesce(x->>'plan', b.plan));
      select coalesce(jsonb_agg(distinct m), '[]'::jsonb) into mods
        from (select jsonb_array_elements_text(coalesce(x->'modules', '[]'::jsonb)) m
              union select jsonb_array_elements_text(case when jsonb_typeof(b.addons->'modules') = 'array' then b.addons->'modules' else '[]'::jsonb end)) q
       where not (coalesce(np->>'modules', 'all') = 'all' or coalesce(np->'modules', '[]'::jsonb) ? m);
      extra := greatest(coalesce((x->>'extra_users')::int, 0), coalesce(b.extra_users, 0));
      ai := (coalesce((x->>'ai')::boolean, false) or coalesce((b.addons->>'ai')::boolean, false)) and not coalesce((np->>'ai')::boolean, false);
      sup := (coalesce((x->>'support')::boolean, false) or coalesce((b.addons->>'support')::boolean, false)) and coalesce((np->>'support')::boolean, true) = false;
      update company_billing set plan = coalesce(x->>'plan', company_billing.plan), extra_users = extra,
             addons = jsonb_build_object('modules', mods, 'ai', ai, 'support', sup), updated_at = now()
       where company_id = (x->>'company_id')::uuid;
      insert into billing_payments(company_id, company_name, plan, months, extra_users, amount, currency, provider, reference, provider_txn, note,
                                   created_by, order_id, addons, kind, status, paid_at, period_start, period_end)
      values ((x->>'company_id')::uuid, x->>'name', coalesce(x->>'plan', o.plan), 1, extra, (x->>'amount')::numeric,
              o.currency, case when o.method_kind = 'card' then 'paymob' else 'manual' end, o.ref || '-' || i, p_txn,
              left('Upgrade · ' || coalesce(o.method_name, '') || coalesce(' · ' || o.proof_ref, ''), 300), o.email, o.id,
              jsonb_build_object('modules', mods, 'ai', ai, 'support', sup), 'upgrade', 'paid', now(), now(), b.paid_until);
    else
      insert into billing_payments(company_id, company_name, plan, months, extra_users, amount, currency, provider, reference, provider_txn, note, created_by, order_id, addons)
      values ((x->>'company_id')::uuid, x->>'name', o.plan, o.months, coalesce((x->>'extra_users')::int, 0), (x->>'amount')::numeric, o.currency,
              case when o.method_kind = 'card' then 'paymob' else 'manual' end, o.ref || '-' || i, p_txn,
              left(coalesce(o.method_name, '') || coalesce(' · ' || o.proof_ref, ''), 300), o.email, o.id,
              jsonb_build_object('modules', coalesce(x->'modules', '[]'::jsonb), 'ai', coalesce((x->>'ai')::boolean, false),
                                 'support', coalesce((x->>'support')::boolean, false)))
      returning id into pid;
      perform public.beyond_apply_payment(pid);
    end if;
  end loop;
  update billing_orders set status = 'approved', review_note = nullif(trim(coalesce(p_note, '')), ''), reviewed_by = nullif(public.beyond_caller(), ''),
         reviewed_at = now(), updated_at = now()
   where id = p_order;
  -- the reward it used is spent
  if o.discount->>'source' = 'reward' then
    update referral_rewards set status = 'used', used_at = now(), order_id = o.id, pct = (o.discount->>'pct')::numeric
     where id = (o.discount->>'reward_id')::bigint and status = 'available';
  end if;
  -- the invited person's first completed payment, for companies of their own: the person who shared earns a reward
  if o.amount > 0 and not was_paid then
    select * into rf from referrals where referred = lower(o.email) and status = 'joined' for update;
    if rf.id is not null and not public.beyond_ref_in_lines(rf.referrer, o.lines) then
      update referrals set status = 'paid', paid_at = now(), order_ref = o.ref, seen = false where id = rf.id;
      insert into referral_rewards(email, referral_id) values (rf.referrer, rf.id) on conflict (referral_id) do nothing;
      perform public.beyond_log('referral.paid', rf.referrer, jsonb_build_object('referred', rf.referred, 'order', o.ref));
    end if;
  end if;
  return public.beyond_order_row(p_order, true);
end $$;

-- ================================================================== the console's settings: only what they should be
create or replace function public.console_referral_settings(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
begin
  perform public.beyond_require_staff('settings');
  if jsonb_typeof(p) <> 'object' then raise exception 'send the programme settings'; end if;
  if p ? 'on' and jsonb_typeof(p->'on') <> 'boolean' then raise exception 'on must be true or false'; end if;
  if exists (select 1 from jsonb_each(p) e where e.key in ('friend_pct', 'first_pct', 'upgrade_pct')
             and (jsonb_typeof(e.value) <> 'number' or (e.value #>> '{}')::numeric not between 0 and 100)) then
    raise exception 'percentages must be 0 to 100';
  end if;
  update billing_config set referral = referral || (p - array(select k from jsonb_object_keys(p) k where k not in ('on', 'friend_pct', 'first_pct', 'upgrade_pct'))),
         updated_at = now(), updated_by = public.beyond_caller()
   where id = 1;
  perform public.beyond_log('referral.settings', null, p);
  return (select referral from billing_config where id = 1);
end $$;

-- "modules", not "sections", in the Enterprise plan's lines (only where they are still the original wording)
update public.billing_config
   set plans = (select jsonb_agg(case when p->>'code' = 'enterprise' and jsonb_typeof(p->'features') = 'array' then
                  jsonb_set(p, '{features}', (select jsonb_agg(case f #>> '{}'
                      when 'Users, companies and sections agreed with you' then to_jsonb('Users, companies and modules agreed with you'::text)
                      when 'Every section and the AI assistant' then to_jsonb('Every module and the AI assistant'::text)
                      else f end order by n) from jsonb_array_elements(p->'features') with ordinality t(f, n)))
                  else p end order by k) from jsonb_array_elements(plans) with ordinality s(p, k))
 where id = 1 and jsonb_typeof(plans) = 'array' and jsonb_array_length(plans) > 0;

revoke all on function public.beyond_ref_tied(text, text), public.beyond_ref_in_lines(text, jsonb),
  public.beyond_ref_discount(text, text, jsonb), public.beyond_ref_check(billing_orders) from public, anon, authenticated;
