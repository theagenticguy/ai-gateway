-- audit_denials
-- Purpose: Every DENIED control-plane / authz decision over a period — who was
--          blocked, on what resource, with what HTTP status. ADR-016 records
--          allow AND deny; this surfaces the deny tail for security review.
-- Params:  <BUCKET>  the S3 Tables audit table-bucket name (Terraform interpolated)
--          :start    inclusive ISO-8601 lower bound
--          :end      inclusive ISO-8601 upper bound
-- Notes:   decision IN ('allow','deny'); this filters decision = 'deny'.
--          `ts` is an ISO-8601 STRING → from_iso8601_timestamp(ts) for the range.
SELECT ts,
       actor,
       team,
       action,
       resource,
       status,
       source_ip,
       correlation_id,
       detail
FROM "s3tablescatalog/<BUCKET>"."control_plane"."audit_events"
WHERE decision = 'deny'
  AND from_iso8601_timestamp(ts)
      BETWEEN from_iso8601_timestamp(:start) AND from_iso8601_timestamp(:end)
ORDER BY from_iso8601_timestamp(ts) DESC;
