# =============================================================================
# Cache — ElastiCache Redis for response caching
# =============================================================================

# ------------------------------------------------------------------
# Parameter Group
# ------------------------------------------------------------------

resource "aws_elasticache_parameter_group" "redis" {
  count = var.enable_cache ? 1 : 0

  name   = "${var.project_name}-${var.environment}-redis7"
  family = "redis7"

  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-redis7"
  }
}

# ------------------------------------------------------------------
# Subnet Group
# ------------------------------------------------------------------

resource "aws_elasticache_subnet_group" "redis" {
  count = var.enable_cache ? 1 : 0

  name       = "${var.project_name}-${var.environment}-redis"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.project_name}-${var.environment}-redis"
  }
}

# ------------------------------------------------------------------
# Security Group
# ------------------------------------------------------------------

resource "aws_security_group" "redis" {
  count = var.enable_cache ? 1 : 0

  name        = "${var.project_name}-${var.environment}-redis"
  description = "Security group for ElastiCache Redis"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-${var.environment}-redis"
  }
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_ecs" {
  count = var.enable_cache ? 1 : 0

  security_group_id            = aws_security_group.redis[0].id
  description                  = "Redis from ECS tasks"
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
  referenced_security_group_id = var.ecs_security_group_id
}

# ------------------------------------------------------------------
# Replication Group (Redis)
# ------------------------------------------------------------------

resource "aws_elasticache_replication_group" "redis" {
  count = var.enable_cache ? 1 : 0

  replication_group_id = "${var.project_name}-${var.environment}"
  description          = "Redis cache for ${var.project_name} response caching"

  node_type            = var.cache_node_type
  num_cache_clusters   = var.cache_num_nodes
  engine_version       = var.cache_engine_version
  port                 = 6379
  parameter_group_name = aws_elasticache_parameter_group.redis[0].name
  subnet_group_name    = aws_elasticache_subnet_group.redis[0].name
  security_group_ids   = [aws_security_group.redis[0].id]

  # Encryption
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  # Single-node: no automatic failover
  automatic_failover_enabled = var.cache_num_nodes > 1

  # Maintenance & snapshots
  maintenance_window       = "sun:05:00-sun:06:00"
  snapshot_retention_limit = 1
  snapshot_window          = "03:00-04:00"

  # Apply changes immediately in dev; use maintenance window in prod
  apply_immediately = true

  tags = {
    Name = "${var.project_name}-${var.environment}-redis"
  }
}
