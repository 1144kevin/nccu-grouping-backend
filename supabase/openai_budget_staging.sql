-- Staging preparation only. Apply to an isolated database after security review.
-- A dedicated backend database role must be granted EXECUTE separately.
create schema if not exists ai_budget_private;
revoke all on schema ai_budget_private from public;

create table ai_budget_private.reservations (
  id bigint generated always as identity primary key,
  reserved_at timestamptz not null default clock_timestamp(),
  utc_day date not null,
  kind text not null check (kind in ('text', 'image', 'embedding', 'speech')),
  units bigint not null check (units > 0)
);
revoke all on ai_budget_private.reservations from public;

create function ai_budget_private.reserve(p_kind text, p_units bigint)
returns boolean language plpgsql security definer set search_path = '' as $$
declare
  v_day date := (clock_timestamp() at time zone 'UTC')::date;
  v_total bigint;
  v_daily bigint;
begin
  if p_kind not in ('text', 'image', 'embedding', 'speech') or p_units is null
     or p_units <= 0 or p_units > 10000000 then
    raise exception 'invalid reservation';
  end if;
  -- Serialize all workers using the same transaction lock before calculating totals.
  perform pg_advisory_xact_lock(hashtextextended('openai-budget-v1', 0));
  select coalesce(sum(units), 0), coalesce(sum(units) filter (where utc_day = v_day), 0)
    into v_total, v_daily from ai_budget_private.reservations;
  if v_total + p_units > 50000000 or v_daily + p_units > 10000000 then
    return false;
  end if;
  insert into ai_budget_private.reservations (utc_day, kind, units)
    values (v_day, p_kind, p_units);
  return true;
end;
$$;
revoke all on function ai_budget_private.reserve(text, bigint) from public;
