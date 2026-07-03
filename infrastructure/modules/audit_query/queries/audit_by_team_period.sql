-- audit_by_team_period
-- Purpose: Governed control-plane audit records for one team over a time
--          window. This is the canonical query behind the GET /audit endpoint.
-- Params:  <BUCKET>  the S3 Tables audit table-bucket name (Terraform interpolates
--                     it into the aws_athena_named_query; the .sql keeps a placeholder)
--          :team     the team whose records to return
--          :start    inclusive ISO-8601 lower bound (e.g. 2026-06-01T00:00:00+00:00)
--          :end      inclusive ISO-8601 upper bound
--          :max_rows row cap
-- Notes:   `ts` is an ISO-8601 STRING column, so from_iso8601_timestamp(ts) is
--          used for range comparison. All names lowercase; catalog fully qualified.
SELECT action,
       actor,
       resource,
       decision,
       status,
       team,
       source_ip,
       correlation_id,
       detail,
       ts
FROM "s3tablescatalog/<BUCKET>"."control_plane"."audit_events"
WHERE team = :team
  AND from_iso8601_timestamp(ts)
      BETWEEN from_iso8601_timestamp(:start) AND from_iso8601_timestamp(:end)
ORDER BY from_iso8601_timestamp(ts) DESC
LIMIT :max_rows;
