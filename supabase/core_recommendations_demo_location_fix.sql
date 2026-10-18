-- Repair only this synthetic batch; manual locations omit optional source.
-- Never falsely label generated sample coordinates as OpenStreetMap results.
update public.core_posts
set payload=payload#-'{location,source}',
 original_payload=original_payload#-'{location,source}',version=version+1
where demo_batch='showcase_20261019_recommendations_v2'
 and payload->'location'->>'source'='manual';
