locals {
  # Proportionally allocate CPU/memory between gateway and otel sidecar
  gateway_cpu    = var.gateway_cpu - 256
  gateway_memory = var.gateway_memory - 256
}

module "ecs_cluster" {
  source  = "terraform-aws-modules/ecs/aws//modules/cluster"
  version = "6.0.0"

  name = "${var.project_name}-${var.environment}"

  setting = [
    {
      name  = "containerInsights"
      value = "enhanced"
    }
  ]

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
  source  = "terraform-aws-modules/ecs/aws//modules/service"
  version = "6.0.0"

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

      environment = [
        { name = "NODE_ENV", value = "production" },
        { name = "PORT", value = "8787" },
      ]

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
          "awslogs-group"         = aws_cloudwatch_log_group.gateway.name
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
        { name = "AOT_CONFIG_CONTENT", value = file("${path.module}/otel-config.yaml") },
      ]

      log_configuration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.otel.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "otel"
        }
      }
    }
  }

  # Network
  subnet_ids = module.vpc.private_subnets

  security_group_ingress_rules = {
    alb = {
      from_port                = 8787
      to_port                  = 8787
      ip_protocol              = "tcp"
      source_security_group_id = module.alb.security_group_id
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
      target_group_arn = module.alb.target_groups["gateway"].arn
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
          resource_label         = "${module.alb.arn_suffix}/${module.alb.target_groups["gateway"].arn_suffix}"
        }
        target_value       = 500
        scale_in_cooldown  = 300
        scale_out_cooldown = 60
      }
    }
  }
}
