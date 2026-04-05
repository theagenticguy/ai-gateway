---
title: Upgrading
description: How to upgrade Portkey versions, Terraform providers, and enable new features.
sidebar:
  order: 10
---

This guide covers the three main upgrade paths for the AI Gateway: upgrading the Portkey gateway version, upgrading Terraform providers, and enabling new features.

## Upgrading Portkey Version

The gateway pins a specific version of the [Portkey-AI/gateway](https://github.com/Portkey-AI/gateway) in `versions.env` at the repository root. The Dockerfile downloads, verifies, and builds from this pinned source.

### Automated Detection

A daily GitHub Actions workflow ([`portkey-release-scanner.yml`](https://github.com/theagenticguy/ai-gateway/blob/main/.github/workflows/portkey-release-scanner.yml)) checks for new upstream Portkey releases at 07:00 UTC. When a new version is found, the workflow:

1. Downloads the new source tarball and computes its SHA256 hash.
2. Builds the custom hardened container image with the new version.
3. Runs Trivy and Grype security scans against the built image.
4. Opens a pull request to update `versions.env` with the new version and hash.

:::tip
You can trigger the scanner manually from the Actions tab via `workflow_dispatch` if you want to check for updates outside the daily schedule.
:::

### Manual Upgrade Steps

If you prefer to upgrade manually or need to pin a specific version:

**1. Update `versions.env`**

```bash
# versions.env — two values, both required
PORTKEY_VERSION=1.16.0
PORTKEY_TARBALL_SHA256=<sha256-of-the-v1.16.0-tarball>
```

**2. Compute the SHA256 hash**

```bash
wget -qO /tmp/portkey.tar.gz \
  "https://github.com/Portkey-AI/gateway/archive/refs/tags/v1.16.0.tar.gz"
sha256sum /tmp/portkey.tar.gz
```

Copy the hash into `PORTKEY_TARBALL_SHA256` in `versions.env`.

**3. Test the build locally**

```bash
docker build \
  --build-arg PORTKEY_VERSION=1.16.0 \
  --build-arg PORTKEY_TARBALL_SHA256=<hash> \
  -t ai-gateway:test .
```

The Dockerfile performs SHA256 verification during the build. If the hash does not match, the build fails immediately.

**4. Push a version tag to trigger the release**

```bash
# Bump the project version (updates pyproject.toml, generates CHANGELOG.md, commits, tags)
mise run release:bump-patch   # or bump-minor / bump-major

# Push the tag to trigger the release workflow
git push origin main --tags
```

The `v*` tag triggers `.github/workflows/release.yml`, which:

- Builds and pushes the container image to GHCR (always) and ECR (if configured).
- Signs the image with cosign (keyless Sigstore).
- Generates CycloneDX and SPDX SBOMs.
- Creates a GitHub Release with an auto-generated changelog.

:::caution
Always review the [Portkey release notes](https://github.com/Portkey-AI/gateway/releases) for breaking changes before upgrading. The gateway is a core dependency and breaking changes in Portkey may require updates to routing configurations or environment variables.
:::

### How the Build Uses the Pinned Version

The Dockerfile is a multi-stage build:

| Stage | What Happens |
|---|---|
| **source** | Downloads the Portkey tarball for the pinned version, verifies SHA256, extracts source |
| **build** | Installs dependencies (`npm ci`), builds from source (`npm run build`), prunes dev deps |
| **runtime** | Copies only the build output and production `node_modules` into a hardened Alpine image (non-root user, tini PID 1, no npm) |

The `versions.env` file is loaded by both the release workflow and the scanner workflow via `cat versions.env >> "$GITHUB_ENV"`, making `PORTKEY_VERSION` and `PORTKEY_TARBALL_SHA256` available as environment variables during the build.

---

## Upgrading Terraform Providers

Terraform provider versions are pinned in `infrastructure/versions.tf`:

```hcl
terraform {
  required_version = "~> 1.14"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.22"
    }
  }
}
```

### Steps

**1. Update the version constraint**

Edit `infrastructure/versions.tf` and adjust the version constraint. The `~>` operator allows patch-level upgrades within the specified minor version (e.g., `~> 6.22` allows `6.22.x` through `6.99.x`).

```hcl
# Example: allow up to 6.x
version = "~> 6.39"
```

**2. Run `terraform init -upgrade`**

```bash
cd infrastructure
terraform init -upgrade
```

This downloads the latest provider version that satisfies the constraint and updates `.terraform.lock.hcl`.

**3. Run `terraform plan`**

```bash
terraform plan -var-file=envs/dev.tfvars
```

Review the plan carefully. Provider upgrades can introduce new resource behaviors, deprecated arguments, or changed defaults.

**4. Commit the lock file**

```bash
git add versions.tf .terraform.lock.hcl
git commit -m "deps(terraform): bump hashicorp/aws to ~> 6.39"
```

:::note
Dependabot is configured to open PRs for Terraform provider updates in the `terraform-minor-patch` group. Review these PRs and merge after verifying the plan is clean.
:::

---

## Enabling Features

All optional features are controlled by Terraform boolean variables and can be enabled independently. See [Feature Toggles](/ai-gateway/admin-guide/features/) for the full list.

### How to Enable a Feature

**1. Add the toggle variables to your `.tfvars` file**

```hcl
# Platform features
enable_multi_client     = true
enable_cost_attribution = true

# Metering & governance
enable_admin_api = true
enable_audit_log = true

# Identity & SSO
enable_user_auth = true
```

**2. Provide any required configuration**

Some features require additional configuration variables beyond the toggle. For example, SSO requires `identity_providers` and `group_mapping`. Refer to the [Feature Toggles](/ai-gateway/admin-guide/features/) page for the full variable list.

**3. Apply**

```bash
terraform plan -var-file=envs/dev.tfvars
terraform apply -var-file=envs/dev.tfvars
```

:::tip
Features are independent. You can enable SSO without multi-client, or enable the response cache without cost attribution. The only dependency is that rate limiting and group mapping both benefit from multi-client being enabled, but they function without it.
:::

### Disabling a Feature

Setting a toggle to `false` and running `terraform apply` destroys only the resources created by that feature. Base infrastructure and other features are unaffected.

```hcl
# Disable guardrails while keeping everything else
enable_guardrails = false
```

:::caution
Disabling `enable_audit_log` will destroy the Kinesis Firehose, S3 bucket, and Glue catalog. Ensure you have exported or backed up any audit data before disabling.
:::

---

## Rollback Procedures

### Rolling Back a Portkey Version

If a new Portkey version introduces issues, revert `versions.env` to the previous values and push a new release tag:

```bash
# Revert versions.env to the previous known-good version
git checkout HEAD~1 -- versions.env

# Commit and tag a new release
git add versions.env
git commit -m "fix: revert Portkey to v1.15.2"
mise run release:bump-patch
git push origin main --tags
```

The release workflow will build and deploy the container image with the reverted Portkey version.

:::note
Container images are tagged with both the version tag and `latest`. Previous version images remain available in GHCR and ECR. You can also point your ECS task definition directly at a previous image tag (e.g., `ghcr.io/theagenticguy/ai-gateway:v1.2.0`) for an immediate rollback without rebuilding.
:::

### Rolling Back a Terraform Change

For infrastructure changes, use Terraform's standard rollback approach:

**Option 1: Revert the code and re-apply**

```bash
git revert <commit-sha>
terraform apply -var-file=envs/dev.tfvars
```

**Option 2: Re-apply with the previous variable values**

If you only changed `.tfvars` values, revert those values and re-apply. Terraform will converge the infrastructure to match the previous state.

### Rolling Back a Feature

To roll back a feature, set its toggle to `false` and apply:

```hcl
# Roll back SSO
enable_user_auth = false
```

```bash
terraform apply -var-file=envs/dev.tfvars
```

This destroys only the resources created by that feature. M2M authentication and other features continue to function.

:::caution
Rolling back `enable_user_auth = false` will remove the Cognito identity providers and the user SSO app client. Any active user sessions will be invalidated. M2M clients are unaffected.
:::
