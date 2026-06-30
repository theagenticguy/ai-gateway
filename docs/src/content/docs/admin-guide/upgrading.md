---
title: Upgrading
description: How to upgrade the agentgateway data-plane image, Terraform providers, and enable new features.
sidebar:
  order: 10
---

This guide covers the three main upgrade paths for the AI Gateway: upgrading the [agentgateway](https://github.com/agentgateway/agentgateway) data-plane image, upgrading Terraform providers, and enabling new features.

## Upgrading the agentgateway Data-Plane Image

The data plane is the [agentgateway](https://github.com/agentgateway/agentgateway) proxy (Rust, distroless base), pinned **by image digest** in `versions.env` at the repository root. We do not build the binary from source — agentgateway publishes an official hardened image, so the Dockerfile re-tags the upstream image **by digest** into our ECR. The digest is the immutable supply-chain contract; the tag is informational. See [ADR-017](/ai-gateway/adrs/017-agentgateway-data-plane-spike/) for the decision that replaced the Portkey OSS build.

`versions.env` holds three values:

- `AGENTGATEWAY_REF` — the upstream image tag published by the agentgateway project (e.g. `v0.5.6`). Informational; the digest is the real pin.
- `AGENTGATEWAY_VERSION` — a human-readable label used in image tags, labels, and docs.
- `AGENTGATEWAY_IMAGE_DIGEST` — the `sha256:...` digest of the upstream multi-arch image. This is the immutable pin; the build references `ghcr.io/agentgateway/agentgateway@<digest>`.

:::note[Why pin by digest instead of building from source?]
agentgateway ships a hardened distroless image (`cgr.dev/chainguard/glibc-dynamic` base, `ENTRYPOINT /app/agentgateway`). Re-tagging it by digest is a smaller attack surface and faster than reproducing the Rust toolchain build. CVE scanning moves to image-level scanning of the pinned digest (Trivy/Grype/Inspector on the ECR image), which CI already runs. The old Portkey Dockerfile compiled Node and patched npm CVEs at build time — that apparatus is gone.
:::

### Automated Detection

A scheduled GitHub Actions workflow watches upstream agentgateway releases and, when a newer release is found, opens a pull request that bumps `AGENTGATEWAY_REF` + `AGENTGATEWAY_IMAGE_DIGEST` in `versions.env`. The workflow pulls the new image, re-tags it by digest, and runs the container security scans before the PR is opened.

:::tip
You can trigger the scanner manually from the Actions tab via `workflow_dispatch` if you want to check for updates outside the schedule.
:::

### Manual Upgrade Steps

If you prefer to upgrade manually or need to pin a specific image:

**1. Resolve the digest for the target tag**

```bash
docker buildx imagetools inspect ghcr.io/agentgateway/agentgateway:v0.5.7
```

Copy the `sha256:...` digest from the output.

**2. Update `versions.env`**

Bump `AGENTGATEWAY_REF` and `AGENTGATEWAY_IMAGE_DIGEST` **together** — they must always describe the same image:

```bash
# versions.env
AGENTGATEWAY_REF=v0.5.7
AGENTGATEWAY_VERSION=0.5.7
AGENTGATEWAY_IMAGE_DIGEST=sha256:<digest-from-step-1>
```

**3. Update the `gateway_image` default**

Update the `gateway_image` default in `infrastructure/variables.tf` to match the new tag, so `terraform plan`/`validate` resolve a consistent default.

**4. Test the build locally**

```bash
docker build \
  --build-arg AGENTGATEWAY_REF=v0.5.7 \
  --build-arg AGENTGATEWAY_VERSION=0.5.7 \
  --build-arg AGENTGATEWAY_IMAGE=ghcr.io/agentgateway/agentgateway@sha256:<digest> \
  -t ai-gateway:test .
```

The build re-tags the pinned upstream image; it intentionally adds no layers, preserving the distroless attack surface.

**5. Push a version tag to trigger the release**

```bash
# Bump the project version (updates pyproject.toml, generates CHANGELOG.md, commits, tags)
mise run release:bump-patch   # or bump-minor / bump-major

# Push the tag to trigger the release workflow
git push origin main --tags
```

The `v*` tag triggers `.github/workflows/release.yml`, which:

- Re-tags the pinned upstream image by digest and pushes it to ECR (and GHCR).
- Signs the image with cosign (keyless Sigstore).
- Generates CycloneDX and SPDX SBOMs.
- Creates a GitHub Release with an auto-generated changelog.

:::caution
Always review the [agentgateway release notes](https://github.com/agentgateway/agentgateway/releases) for breaking changes before upgrading. The data plane reads a rendered YAML config (`compute/agentgateway-config.yaml.tftpl`); a config-schema change upstream may require updating that template.
:::

### How the Build Uses the Pinned Image

The Dockerfile is a single stage that re-tags the pinned upstream image:

| Step | What Happens |
|---|---|
| **base** | `FROM ghcr.io/agentgateway/agentgateway@<AGENTGATEWAY_IMAGE_DIGEST>` — the pinned distroless image |
| **labels** | OCI labels stamp `AGENTGATEWAY_VERSION` and the upstream base name |
| **expose** | Port 8787 (the gateway listener); readiness is on 15021, checked by ECS |

No application layers are added: there is no shell, no package manager, and the entrypoint (`/app/agentgateway`) is inherited from the upstream image. The ECS task definition supplies the config via `command: ["-c", "<rendered config>"]`.

The `versions.env` file is loaded by the CI and release workflows via `cat versions.env >> "$GITHUB_ENV"`, making `AGENTGATEWAY_REF`, `AGENTGATEWAY_VERSION`, and `AGENTGATEWAY_IMAGE_DIGEST` available as `--build-arg` values during the build.

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
Features are independent. You can enable SSO without multi-client, or enable guardrails without cost attribution. The only dependency is that rate limiting and group mapping both benefit from multi-client being enabled, but they function without it.
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

### Rolling Back the agentgateway Image

If a new agentgateway image introduces issues, revert `versions.env` (and the `gateway_image` default) to the previous known-good values and push a new release tag:

```bash
# Revert versions.env to the previous known-good digest
git checkout HEAD~1 -- versions.env infrastructure/variables.tf

# Commit and tag a new release
git add versions.env infrastructure/variables.tf
git commit -m "fix: revert agentgateway image to v0.5.5"
mise run release:bump-patch
git push origin main --tags
```

The release workflow will re-tag and deploy the reverted image by digest.

:::note
Images are tagged with both the version tag and `latest` in ECR. Previous images remain available. You can also point your ECS task definition (via the `gateway_image` variable) directly at a previous image URI for an immediate rollback without re-running the release workflow.
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
