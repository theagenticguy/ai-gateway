"""Thin admin CLI for the AI Gateway control plane.

Standalone client: it wraps the control-plane admin API (``/teams``,
``/budgets``, ``/routing``, ``/pricing``) behind Cognito M2M
``client_credentials`` auth. It forwards JSON bodies and prints responses; it
does NOT import from the gateway ``src/`` runtime or reimplement its models.
"""

from __future__ import annotations

__version__ = "0.1.0"
