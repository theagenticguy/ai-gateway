---
title: Admin API
description: Admin API endpoints for teams, budgets, pricing, routing, and usage — served via API Gateway with Cognito authorization.
sidebar:
  order: 9
---
The admin API runs on a **separate API Gateway REST API** with a Cognito authorizer, decoupled from the inference path (ALB). This two-plane architecture (see [ADR-014](/ai-gateway/adrs/014-two-plane-architecture-split/)) ensures admin endpoints get consistent auth enforcement without duplicating JWT validation in each Lambda handler.

Enable it with:

```hcl
enable_admin_api = true
```

---

## Architecture

| Plane | Transport | Auth | Traffic Pattern |
|---|---|---|---|
| **Inference** | ALB with `validate_token` | ALB-native JWT validation | High-volume, latency-sensitive |
| **Admin** | API Gateway REST API | Cognito Authorizer (`COGNITO_USER_POOLS`) | Low-volume, correctness-sensitive |

The ALB continues handling `/v1/chat/completions` and `/v1/messages`. All admin endpoints listed below are served by API Gateway.

---

## Authentication

All admin endpoints require a JWT with the `admin` scope. Obtain one via:

```bash
curl -X POST "${COGNITO_TOKEN_ENDPOINT}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=${CLIENT_ID}&client_secret=${CLIENT_SECRET}&scope=https://gateway.internal/admin"
```

:::caution
The `admin` scope grants access to all admin endpoints. Only issue admin credentials to platform team members, not to consuming teams.
:::


---

## Endpoints

### Teams

| Method | Path | Description |
|---|---|---|
| `GET` | `/teams` | List registered teams |
| `POST` | `/teams` | Register a new team |
| `GET` | `/teams/{id}` | Get team details |
| `PUT` | `/teams/{id}` | Update team configuration |
| `DELETE` | `/teams/{id}` | Deregister a team |

### Budgets

| Method | Path | Description |
|---|---|---|
| `GET` | `/budgets` | List all budgets |
| `POST` | `/budgets` | Create a budget |
| `GET` | `/budgets/{id}` | Get budget and current usage |
| `PUT` | `/budgets/{id}` | Update a budget |
| `DELETE` | `/budgets/{id}` | Delete a budget |

### Pricing Admin

| Method | Path | Description |
|---|---|---|
| `GET` | `/pricing` | List all pricing entries (DynamoDB overrides + static defaults) |
| `GET` | `/pricing/{provider}/{model}` | Get pricing for a specific model |
| `PUT` | `/pricing/{provider}/{model}` | Create or update a pricing override |
| `DELETE` | `/pricing/{provider}/{model}` | Remove override, revert to static default |

### Routing Config

| Method | Path | Description |
|---|---|---|
| `GET` | `/routing` | List routing configurations |
| `POST` | `/routing` | Create a routing rule |
| `GET` | `/routing/{id}` | Get routing rule details |
| `PUT` | `/routing/{id}` | Update a routing rule |
| `DELETE` | `/routing/{id}` | Delete a routing rule |

### Usage API

| Method | Path | Description |
|---|---|---|
| `GET` | `/usage/{team}` | Current period usage, budget utilization, per-model breakdown |
| `GET` | `/usage/{team}/history` | Monthly usage history |

:::note
The usage API is read-only and can also be accessed with the `invoke` scope. Teams can query their own usage without admin credentials.
:::


---

## Route Map

Each admin endpoint is backed by a dedicated Lambda function:

| Path | Lambda | Purpose |
|---|---|---|
| `/teams` | `team_registration` | Self-service onboarding |
| `/budgets` | `budget_admin` | Budget CRUD |
| `/routing` | `routing_config` | Routing rule management — renders the agentgateway backend config |
| `/pricing` | `pricing_admin` | Dynamic pricing overrides |
| `/usage` | `usage_api` | Real-time usage self-service |

Each path prefix has a `{proxy+}` child resource for sub-paths, with `ANY` methods and `AWS_PROXY` Lambda integrations.
