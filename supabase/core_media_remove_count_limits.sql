-- Remove the development-only 20-per-owner / 100-per-project image counts.
-- Keep account/session checks, idempotency, verification, RLS and file limits.
do $migration$
declare
  target regprocedure;
  table_name text;
  definition text;
  updated text;
  pattern text;
begin
  for target,table_name in
    select 'core_private.media_before_gallery(text,jsonb)'::regprocedure,'core_media_assets'
    union all
    select 'core_private.chat_media(text,jsonb)'::regprocedure,'core_chat_images'
  loop
    definition := pg_get_functiondef(target);
    if position('image_storage_limit' in definition)=0 then continue; end if;
    pattern := format(
      '-- A reservation[^\n]*\n[[:space:]]*if \(select count\(\*\) from public\.%s\)>=100 or[[:space:]]*\(select count\(\*\) from public\.%s where owner_id=actor\)>=20 then[[:space:]]*raise exception using errcode=''PT409'',message=''image_storage_limit''; end if;',
      table_name,table_name);
    updated := regexp_replace(definition,pattern,
      '-- Image counts are unrestricted; Storage capacity and byte limits still apply.');
    if updated=definition or position('image_storage_limit' in updated)>0 then
      raise exception 'Unexpected image reservation definition: %',target;
    end if;
    execute updated;
  end loop;
end $migration$;
