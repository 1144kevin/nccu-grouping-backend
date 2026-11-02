-- Recheck the attested Storage object while holding its asset lock at attachment.
do $upgrade$
declare definition text;
begin
 select pg_get_functiondef('core_private.dispatch(text,jsonb)'::regprocedure) into definition;
 if position('core_private.personal_media(' in definition)=0 then
  definition:=replace(definition,
    'perform 1 from public.core_personal_images where id=(args->>''avatar_id'')::uuid for update;',
    'perform 1 from public.core_personal_images where id=(args->>''avatar_id'')::uuid for update;
     if args->>''avatar_id'' is not null then
       perform core_private.personal_media(''read'',jsonb_build_object(''image_id'',args->>''avatar_id''));
     end if;');
  if position('core_private.personal_media(' in definition)=0 then raise exception 'avatar guard anchor missing'; end if;
  execute definition;
 end if;
 select pg_get_functiondef('core_private.engagement(text,jsonb)'::regprocedure) into definition;
 if position('core_private.personal_media(' in definition)=0 then
  definition:=replace(definition,
    'if not found then raise exception using errcode=''PT422'',message=''report_image_required''; end if;',
    'if not found then raise exception using errcode=''PT422'',message=''report_image_required''; end if;
     perform core_private.personal_media(''read'',jsonb_build_object(''image_id'',value->>''evidence_image_id''));');
  if position('core_private.personal_media(' in definition)=0 then raise exception 'evidence guard anchor missing'; end if;
  execute definition;
 end if;
end $upgrade$;
