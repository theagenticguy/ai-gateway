# ADR-009: Provider Routing Strategy

## Status: Accepted
## Date: 2026-03-20

## Decision
Use Portkey native routing with fallback and load-balance configs.

## Options: custom proxy (rejected), API GW routing (rejected), Portkey native (accepted).

## Consequences: improved resilience, zero code changes, Portkey config lock-in.
