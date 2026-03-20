terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.22"
    }
  }
}

# =============================================================================
# Compute — ECS, ECR, IAM, Secrets Manager
# =============================================================================

# ------------------------------------------------------------------
# KMS key for ECR encryption
# ------------------------------------------------------------------

resource "aws_kms_key" "ecr" {
  description             = "KMS key for AI Gateway ECR encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccount"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${var.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "ai-gateway-ecr"
  }
}

resource "aws_kms_alias" "ecr" {
  name          = "alias/ai-gateway-ecr"
  target_key_id = aws_kms_key.ecr.key_id
}

resource "aws_ecr_repository" "gateway" {
  name                 = "${var.project_name}-gateway"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ecr.arn
  }
}

resource "aws_ecr_lifecycle_policy" "gateway" {
  repository = aws_ecr_repository.gateway.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

# ------------------------------------------------------------------
# KMS key for Secrets Manager encryption
# ------------------------------------------------------------------

resource "aws_kms_key" "secrets" {
  description             = "KMS key for AI Gateway secrets encryption"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootAccount"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${var.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "ai-gateway-secrets"
  }
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/ai-gateway-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

locals {
  secrets = {
    openai    = "ai-gateway/openai-api-key"
    anthropic = "ai-gateway/anthropic-api-key"
    google    = "ai-gateway/google-api-key"
    azure     = "ai-gateway/azure-api-key"
  }
}

resource "aws_secretsmanager_secret" "secrets" {
  #checkov:skip=CKV2_AWS_57:External provider API keys cannot be auto-rotated by Secrets Manager
  for_each = local.secrets

  name       = each.value
  kms_key_id = aws_kms_key.secrets.arn
}

resource "aws_secretsmanager_secret_version" "secrets" {
  for_each = local.secrets

  secret_id     = aws_secretsmanager_secret.secrets[each.key].id
  secret_string = "REPLACE_ME"
}

# ------------------------------------------------------------------
# ECS Task Execution Role — used by ECS agent to pull images & secrets
# ------------------------------------------------------------------

resource "aws_iam_role" "ecs_task_execution" {
  name = "${var.project_name}-${var.environment}-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name = "secrets-access"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.account_id}:secret:ai-gateway/*"
        ]
      },
      {
        Effect = "Allow"
        Action = ["ssm:GetParameters"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}:${var.account_id}:parameter/ai-gateway/*"
        ]
      }
    ]
  })
}

# ------------------------------------------------------------------
# ECS Task Role — runtime permissions for gateway + ADOT
# ------------------------------------------------------------------

resource "aws_iam_role" "ecs_task" {
  name = "${var.project_name}-${var.environment}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task_bedrock" {
  #checkov:skip=CKV_AWS_290:Bedrock InvokeModel does not support resource-level permissions
  #checkov:skip=CKV_AWS_355:Bedrock InvokeModel does not support resource-level permissions
  name = "bedrock-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ApplyGuardrail",
        "bedrock:GetGuardrail"
      ]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "ecs_task_observability" {
  name = "observability"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:CreateLogGroup"
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.project_name}/*",
          "arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.project_name}/*:*"
        ]
      }
    ]
  })
}

# ------------------------------------------------------------------
# ECS Cluster + Service
# ------------------------------------------------------------------

locals {
  # Proportionally allocate CPU/memory between gateway and otel sidecar
  gateway_cpu    = var.gateway_cpu - 256
  gateway_memory = var.gateway_memory - 256
}

module "ecs_cluster" {
  #checkov:skip=CKV_TF_1:Registry modules pinned by version; commit hash not applicable
  source  = "terraform-aws-modules/ecs/aws//modules/cluster"
  version = "7.5.0"

  name = "${var.project_name}-${var.environment}"

  setting = [
    {
      name  = "containerInsights"
      value = "enhanced"
    }
  ]

  # v7 requires explicit cluster_capacity_providers
  cluster_capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy = {
    FARGATE = {
      weight = 1
      base   = 1
    }
    FARGATE_SPOT = {
      weight = 0
    }
  }
}

module "ecs_service" {
  #checkov:skip=CKV_TF_1:Registry modules pinned by version; commit hash not applicable
  source  = "terraform-aws-modules/ecs/aws//modules/service"
  version = "7.5.0"

  name        = "${var.project_name}-gateway"
  cluster_arn = module.ecs_cluster.arn

  cpu    = var.gateway_cpu
  memory = var.gateway_memory

  desired_count = var.gateway_desired_count

  # Circuit breaker with rollback
  deployment_circuit_breaker = {
    enable   = true
    rollback = true
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # Container definitions
  container_definitions = {
    gateway = {
      essential = true
      image     = var.portkey_image
      cpu       = local.gateway_cpu
      memory    = local.gateway_memory

      port_mappings = [{
        containerPort = 8787
        protocol      = "tcp"
      }]

      environment = concat(
        [
          { name = "NODE_ENV", value = "production" },
          { name = "PORT", value = "8787" },
        ],
        [for name, config in var.portkey_routing_configs : {
          name  = "PORTKEY_DEFAULT_CONFIG_${upper(name)}"
          value = config
        }],
        var.cache_enabled ? [
          { name = "CACHE_STORE", value = "redis" },
          { name = "REDIS_URL", value = var.redis_url },
        ] : []
      )

      secrets = [
        { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.secrets["openai"].arn },
        { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.secrets["anthropic"].arn },
        { name = "GOOGLE_API_KEY", valueFrom = aws_secretsmanager_secret.secrets["google"].arn },
        { name = "AZURE_API_KEY", valueFrom = aws_secretsmanager_secret.secrets["azure"].arn },
      ]

      health_check = {
        command     = ["CMD-SHELL", "wget -q --spider http://localhost:8787/ || exit 1"]
        interval    = 15
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      log_configuration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.gateway_log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "gateway"
        }
      }
    }

    otel-collector = {
      essential = true
      image     = "public.ecr.aws/aws-observability/aws-otel-collector:latest"
      cpu       = 256
      memory    = 256

      environment = [
        { name = "AOT_CONFIG_CONTENT", value = var.otel_config_content },
      ]

      log_configuration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = var.otel_log_group_name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "otel"
        }
      }
    }
  }

  # Network
  subnet_ids = var.private_subnets

  security_group_ingress_rules = {
    alb = {
      from_port                = 8787
      to_port                  = 8787
      ip_protocol              = "tcp"
      source_security_group_id = var.alb_security_group_id
    }
  }

  security_group_egress_rules = {
    all = {
      ip_protocol = "-1"
      cidr_ipv4   = "0.0.0.0/0"
    }
  }

  # IAM — use pre-created roles
  task_exec_iam_role_arn    = aws_iam_role.ecs_task_execution.arn
  tasks_iam_role_arn        = aws_iam_role.ecs_task.arn
  create_task_exec_iam_role = false
  create_tasks_iam_role     = false

  # Load balancer
  load_balancer = {
    service = {
      target_group_arn = var.alb_target_group_gateway_arn
      container_name   = "gateway"
      container_port   = 8787
    }
  }

  # Autoscaling
  autoscaling_min_capacity = var.autoscaling_min_capacity
  autoscaling_max_capacity = var.autoscaling_max_capacity

  autoscaling_policies = {
    cpu = {
      policy_type = "TargetTrackingScaling"
      target_tracking_scaling_policy_configuration = {
        predefined_metric_specification = {
          predefined_metric_type = "ECSServiceAverageCPUUtilization"
        }
        target_value       = 70
        scale_in_cooldown  = 300
        scale_out_cooldown = 60
      }
    }
    requests = {
      policy_type = "TargetTrackingScaling"
      target_tracking_scaling_policy_configuration = {
        predefined_metric_specification = {
          predefined_metric_type = "ALBRequestCountPerTarget"
          resource_label         = "${var.alb_arn_suffix}/${var.alb_target_group_gateway_arn_suffix}"
        }
        target_value       = 500
        scale_in_cooldown  = 300
        scale_out_cooldown = 60
      }
    }
  }
}
