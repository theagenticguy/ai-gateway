terraform {
  required_version = "~> 1.14"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
  }
}

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
  # checkov:skip=CKV2_AWS_5:Decommissioned per ADR-017 (supersedes ADR-012). The
  # response cache is no longer instantiated (var.enable_cache forced false, module
  # not called from main.tf); this SG attaches to the replication group only when
  # the module is live. Code retained on disk for history per the ADR.
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

#checkov:skip=CKV_AWS_2:False positive — this is ElastiCache Redis, not an ALB listener
resource "aws_elasticache_replication_group" "redis" {
  #checkov:skip=CKV2_AWS_50:Single-node dev cluster
  #checkov:skip=CKV_AWS_191:Decommissioned per ADR-017 — response cache no longer instantiated (var.enable_cache forced false); transit+at-rest encryption already on, CMK not warranted for dead code retained for history
  #checkov:skip=CKV_AWS_31:Decommissioned per ADR-017 — transit_encryption_enabled=true is set; auth-token finding is moot since the module is no longer called from main.tf
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
