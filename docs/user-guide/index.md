# User Guide

This guide is for **developers using the AI Gateway** to route LLM requests from AI coding agents and applications.

---

## Who This Is For

You are a developer who wants to:

- Route AI agent requests through a centralized gateway instead of hitting provider APIs directly
- Use any of the 6 supported AI coding agents (Claude Code, OpenCode, Goose, Continue.dev, LangChain, Codex CLI)
- Access multiple model providers (Anthropic, OpenAI, Google, Azure OpenAI) with a single set of credentials
- Avoid managing individual provider API keys on your local machine

---

## What You Can Do

| Capability | Description |
|---|---|
| **Route to any provider** | Switch between Anthropic, OpenAI, Google, and Azure OpenAI by changing a single header |
| **Use either API format** | Send requests in OpenAI Chat Completions format (`/v1/chat/completions`) or Anthropic Messages format (`/v1/messages`) |
| **Authenticate once** | Use a single Cognito M2M token instead of per-provider API keys |
| **Use any compatible agent** | Configure Claude Code, OpenCode, Goose, Continue.dev, LangChain, or Codex CLI |

---

## Sections

<div class="grid cards" markdown>

-   **Agent Setup**

    ---

    Step-by-step configuration for each supported AI coding agent.

    [:octicons-arrow-right-24: Agent setup](agent-setup.md)

-   **API Reference**

    ---

    Endpoints, headers, request/response formats, and rate limits.

    [:octicons-arrow-right-24: API reference](api-reference.md)

-   **Troubleshooting**

    ---

    Solutions for common errors: 401s, 403s, missing headers, and more.

    [:octicons-arrow-right-24: Troubleshooting](troubleshooting.md)

</div>
