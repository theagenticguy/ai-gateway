plugin "aws" {
  enabled = true
  version = "0.48.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
  # Force PGP verification: tflint's default attestation path panics via a
  # sigstore-go nil-pointer bug when GITHUB_TOKEN is set (upstream
  # terraform-linters/tflint#2591, GitHub attestation-bundle change 2026-07-16).
  # Revert to the default once tflint ships the sigstore-go fix.
  signature = "pgp"
}

config {
  call_module_type = "local"
}

rule "terraform_naming_convention" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = true
}

rule "terraform_standard_module_structure" {
  enabled = false
}
