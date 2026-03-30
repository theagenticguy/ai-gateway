# Shared inputs across all environments
locals {
  project_name = "ai-gateway"

  # Read Portkey version from the single source of truth (versions.env)
  _versions_raw    = file("${get_repo_root()}/versions.env")
  _portkey_version = trimspace(regex("PORTKEY_VERSION=(.+)", local._versions_raw)[0])
  portkey_image    = "portkeyai/gateway:${local._portkey_version}"
}
