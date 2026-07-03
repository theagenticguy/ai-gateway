-- audit_mutations_by_actor
-- Purpose: Control-plane mutation counts grouped by actor + action over a
--          period. Answers "who changed what, how often" for pricing/team/
--          routing mutations (action values like team.create, routing.update,
--          pricing.delete).
-- Params:  <BUCKET>  the S3 Tables audit table-bucket name (Terraform interpolated)
--          :start    inclusive ISO-8601 lower bound
--          :end      inclusive ISO-8601 upper bound
-- Notes:   Mutations are the allow-decision writes (reads are not audited). We
--          exclude *.access probe actions and pure deny rows to focus on
--          successful mutations. `ts` is ISO-8601 STRING → from_iso8601_timestamp.
SELECT actor,
       action,
       COUNT(*)                              AS mutation_count,
       MIN(from_iso8601_timestamp(ts))       AS first_seen,
       MAX(from_iso8601_timestamp(ts))       AS last_seen
FROM "s3tablescatalog/<BUCKET>"."control_plane"."audit_events"
WHERE decision = 'allow'
  AND action LIKE '%.%'
  AND action NOT LIKE '%.access'
  AND from_iso8601_timestamp(ts)
      BETWEEN from_iso8601_timestamp(:start) AND from_iso8601_timestamp(:end)
GROUP BY actor, action
ORDER BY mutation_count DESC;
