variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "gateway_log_group_name" {
  type = string
}

variable "gateway_log_group_arn" {
  type = string
}

variable "enable_cost_attribution" {
  type    = bool
  default = true
}

variable "account_id" {
  type = string
}
