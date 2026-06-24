"""admin_token — short-lived, team-scoped, audience-bound token exchange.

Backs the ``gateway refresh --audience <claude|codex>`` flow (ADR-016): a
developer authenticates once via org SSO (Cognito Hosted UI, ADR-013) and the
CLI credential helpers (Claude Code ``apiKeyHelper`` / Codex ``auth.command``)
call this endpoint to mint a fresh gateway access token before each near-expiry
window. Built entirely on ``gwcore``.
"""
