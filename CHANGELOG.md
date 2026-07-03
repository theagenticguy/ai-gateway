## Unreleased

### Feat

- inline Bedrock guardrail replaces the content-scanner hook path (#54)
- C-Series refinements (C.1–C.5) + ADR-014 (#52)
- AI Gateway v2 — merge all feature branches (#48)
- add git-cliff automated changelog generation
- add semver versioning policy and release bump tasks
- **security**: add property-based fuzz tests for cost attribution
- **infra**: add ElastiCache Redis response cache layer
- **security**: add Bedrock Guardrails module for content safety filtering
- **observability**: add cost attribution pipeline with Lambda + CloudWatch metrics
- **routing**: add provider fallback and load-balance configurations
- **auth**: add multi-client onboarding module for per-team Cognito credentials

### Fix

- **ci**: strip comments from versions.env and prefix TFLint version
- resolve all version lifecycle risks from issue #55
- **docs**: mermaid rendering, edit links, .md link resolution, stale content (#51)
- restore scorecard-action SHA and fix gitleaks shallow clone
- correct codeql-action SHA pins, add pre-flight lint to bot-push (#32)
- correct scorecard-action SHA pin and add CONTRIBUTING.md
- address Copilot review — guard non-dict usage and non-string provider
- add default region to cloudwatch client for CI test env
- **ci**: skip CKV2_AWS_50 Multi-AZ failover check (single-node dev cache)
- **ci**: skip Checkov false positive on ElastiCache and soften Trivy
- **ci**: restore SARIF uploads for public repo, fix Checkov and Semgrep
- **ci**: remove SARIF uploads and disable CodeQL/Scorecard for private repo
- **ci**: wire cache_node_type and remove unused default_routing_strategy
- **ci**: add version constraints and required_version to all child modules
- **ci**: use dedicated PAT for OpenSSF Scorecard
- **ci**: add required_providers to all child modules and ignore unfixed CVEs
- **ci**: add boto3 dep for pyright and regenerate terraform-docs
- resolve ruff lint errors and terraform fmt in B.3 cost attribution

### Refactor

- adopt Pydantic v2 models for cost attribution validation
- extract 4 local modules + terraform-docs + structural cleanup
