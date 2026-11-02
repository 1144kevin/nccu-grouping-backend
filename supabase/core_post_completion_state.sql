-- Expose the existing durable completion result through every post representation.
-- No state transitions, permissions or stored post/chat data are changed.
do $migration$
declare definition text;
begin
  definition := pg_get_functiondef('core_private.post_view(uuid,uuid)'::regprocedure);
  if strpos(definition, '''completed'',core_private.handoff_complete(post.id)') > 0 then return; end if;
  if strpos(definition, '''created_at'',post.created_at') = 0 then
    raise exception 'Unexpected post_view definition; inspect before applying';
  end if;
  definition := replace(definition, '''created_at'',post.created_at',
    '''completed'',core_private.handoff_complete(post.id),''created_at'',post.created_at');
  execute definition;
end;
$migration$;
