output "client_ids" {
  description = "Map of team name to Cognito app client ID"
  value = {
    for team, client in aws_cognito_user_pool_client.team :
    team => client.id
  }
}

output "client_secrets" {
  description = "Map of team name to Cognito app client secret"
  sensitive   = true
  value = {
    for team, client in aws_cognito_user_pool_client.team :
    team => client.client_secret
  }
}

output "client_names" {
  description = "Map of team name to Cognito app client name"
  value = {
    for team, client in aws_cognito_user_pool_client.team :
    team => client.name
  }
}
