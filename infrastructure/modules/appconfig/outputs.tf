output "application_id" {
  description = "ID of the AppConfig application"
  value       = var.enable_appconfig ? aws_appconfig_application.this[0].id : null
}

output "environment_id" {
  description = "ID of the AppConfig environment"
  value       = var.enable_appconfig ? aws_appconfig_environment.this[0].environment_id : null
}

output "configuration_profile_id" {
  description = "ID of the AppConfig configuration profile"
  value       = var.enable_appconfig ? aws_appconfig_configuration_profile.scanner[0].configuration_profile_id : null
}

output "appconfig_resource_path" {
  description = "Full prefetch path for the Lambda AppConfig extension"
  value = var.enable_appconfig ? join("", [
    "/applications/", aws_appconfig_application.this[0].id,
    "/environments/", aws_appconfig_environment.this[0].environment_id,
    "/configurations/", aws_appconfig_configuration_profile.scanner[0].configuration_profile_id,
  ]) : null
}
