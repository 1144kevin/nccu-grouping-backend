begin;
-- Existing profiles keep access; only newly created profiles require setup.
alter table public.core_profiles add column profile_setup_required boolean not null default false;
alter table public.core_profiles alter column profile_setup_required set default true;

create function core_private.complete_profile_setup() returns trigger
language plpgsql set search_path='' as $$
begin
 if new.version > old.version then new.profile_setup_required := false; end if;
 return new;
end $$;
revoke all on function core_private.complete_profile_setup() from public,anon,authenticated;
create trigger core_complete_profile_setup before update on public.core_profiles
for each row execute function core_private.complete_profile_setup();

create or replace function core_private.profile_view(actor uuid) returns jsonb
language sql stable set search_path='' as $$
 select jsonb_build_object('id',p.id,'display_name',p.display_name,'bio',p.bio,
 'badge',p.badge,'version',p.version,'created_at',p.created_at,
 'email',u.email,'email_verified',u.email_confirmed_at is not null,'auth_mode','supabase',
 'profile_setup_required',p.profile_setup_required)
 from public.core_profiles p join auth.users u on u.id=p.id where p.id=actor;
$$;
commit;
