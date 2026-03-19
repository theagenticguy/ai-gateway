module "alb" {
  #checkov:skip=CKV_TF_1:Registry modules pinned by version; commit hash not applicable
  source  = "terraform-aws-modules/alb/aws"
  version = "10.0.0"

  name               = "${var.project_name}-${var.environment}"
  load_balancer_type = "application"
  vpc_id             = module.vpc.vpc_id
  subnets            = module.vpc.public_subnets

  security_group_ingress_rules = {
    all_https = {
      from_port   = 443
      to_port     = 443
      ip_protocol = "tcp"
      cidr_ipv4   = "0.0.0.0/0"
    }
    all_http = {
      from_port   = 80
      to_port     = 80
      ip_protocol = "tcp"
      cidr_ipv4   = "0.0.0.0/0"
    }
  }

  security_group_egress_rules = {
    all = {
      ip_protocol = "-1"
      cidr_ipv4   = module.vpc.vpc_cidr_block
    }
  }

  listeners = merge(
    # HTTP listener — always created
    {
      http = {
        port     = 80
        protocol = "HTTP"

        # Redirect to HTTPS when a certificate is available, otherwise forward to gateway
        forward = var.certificate_arn == "" ? {
          target_group_key = "gateway"
        } : null

        redirect = var.certificate_arn != "" ? {
          port        = "443"
          protocol    = "HTTPS"
          status_code = "HTTP_301"
        } : null
      }
    },
    # HTTPS listener — only when a certificate ARN is provided
    var.certificate_arn != "" && !var.enable_jwt_auth ? {
      https = {
        port            = 443
        protocol        = "HTTPS"
        ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-2021-06"
        certificate_arn = var.certificate_arn

        forward = {
          target_group_key = "gateway"
        }
      }
    } : {}
  )

  target_groups = {
    gateway = {
      backend_protocol = "HTTP"
      backend_port     = 8787
      target_type      = "ip"

      health_check = {
        enabled             = true
        path                = "/"
        port                = "8787"
        protocol            = "HTTP"
        healthy_threshold   = 2
        unhealthy_threshold = 3
        interval            = 15
        timeout             = 5
        matcher             = "200"
      }

      # ECS manages target registration
      create_attachment = false

      deregistration_delay = 30

      stickiness = {
        enabled = false
        type    = "lb_cookie"
      }
    }
  }
}
