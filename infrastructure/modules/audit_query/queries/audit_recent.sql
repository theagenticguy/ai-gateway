-- audit_recent
-- Purpose: The last-24h tail of the control-plane audit trail — a quick "what
--          just happened" view across all teams. Uses Iceberg relative
--          time-travel-free filtering on the ISO-8601 ts string.
-- Params:  <BUCKET>  the S3 Tables audit table-bucket name (Terraform interpolated)
-- Notes:   `ts` is an ISO-8601 STRING → from_iso8601_timestamp(ts) is compared
--          against (current_timestamp - interval '24' hour). All lowercase,
--          catalog fully qualified.
SELECT ts,
       decision,
       status,
       actor,
       team,
       action,
       resource,
       source_ip,
       correlation_id
FROM "s3tablescatalog/<BUCKET>"."control_plane"."audit_events"
WHERE from_iso8601_timestamp(ts) >= (current_timestamp - interval '24' hour)
ORDER BY from_iso8601_timestamp(ts) DESC
LIMIT 500;
