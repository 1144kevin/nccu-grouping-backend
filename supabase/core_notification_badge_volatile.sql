-- account_guard takes a row lock to validate the session. PostgREST executes
-- STABLE functions in a read-only transaction, where that lock is forbidden.
-- VOLATILE lets the same authenticated check run in a read-write transaction;
-- the badge function itself still only counts existing notices.
alter function public.core_notification_badge() volatile;
