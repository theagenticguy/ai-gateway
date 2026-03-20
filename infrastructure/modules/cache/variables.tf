variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
}

variable "environment" {
  description = "Deployment environment (dev or prod)"
  type        = string
}

variable "enable_cache" {
  description = "Whether to create the ElastiCache Redis cluster"
  type        = bool
  default     = true
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for the ElastiCache subnet group"
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID for the Redis security group"
  type        = string
}

variable "ecs_security_group_id" {
  description = "Security group ID of the ECS service (to allow ingress from ECS to Redis)"
  type        = string
}

variable "cache_node_type" {
  description = "ElastiCache node instance type"
  type        = string
  default     = "cache.t4g.micro"
}

variable "cache_engine_version" {
  description = "Redis engine version"
  type        = string
  default     = "7.1"
}

variable "cache_num_nodes" {
  description = "Number of cache nodes in the replication group"
  type        = number
  default     = 1
}
