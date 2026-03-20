output "redis_endpoint" {
  description = "Primary endpoint address of the Redis replication group"
  value       = var.enable_cache ? aws_elasticache_replication_group.redis[0].primary_endpoint_address : ""
}

output "redis_port" {
  description = "Port number of the Redis cluster"
  value       = var.enable_cache ? 6379 : 0
}

output "redis_connection_url" {
  description = "Full Redis connection URL with TLS (rediss://)"
  value       = var.enable_cache ? "rediss://${aws_elasticache_replication_group.redis[0].primary_endpoint_address}:6379" : ""
}

output "redis_security_group_id" {
  description = "Security group ID of the Redis cluster"
  value       = var.enable_cache ? aws_security_group.redis[0].id : ""
}
