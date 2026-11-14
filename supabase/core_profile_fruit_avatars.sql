begin;
-- Add fruit avatars while preserving all existing avatar identifiers.
alter table public.core_profiles drop constraint if exists core_profiles_badge_check;
alter table public.core_profiles add constraint core_profiles_badge_check
  check (badge in ('🍜','🥐','🍕','🧋','🍱','🍍', 'eagle-01', 'eagle-02', 'eagle-03', 'eagle-04', 'eagle-05', 'eagle-06', 'eagle-07', 'eagle-08', 'eagle-09', 'eagle-10', 'eagle-11', 'eagle-12', 'eagle-13', 'eagle-14', 'eagle-15', 'eagle-16', 'fruit-orange', 'fruit-peach', 'fruit-lemon', 'fruit-pineapple', 'fruit-avocado', 'fruit-banana', 'fruit-grape', 'fruit-strawberry', 'fruit-blueberry', 'fruit-apple'));

commit;
