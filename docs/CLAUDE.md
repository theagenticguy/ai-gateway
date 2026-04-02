# CLAUDE.md

You are working in a Starlight documentation site (`docs/` directory).

## Goals
- Keep the site static-first.
- Prefer Markdown over MDX unless components are required.
- Preserve docs readability for both humans and coding agents.
- Keep copyable commands and code blocks intact.
- Do not introduce unnecessary client-side JavaScript.

## Architecture
- Astro + Starlight
- AI-friendly docs via starlight-page-actions and starlight-llms-txt
- Search defaults to Pagefind
- Mermaid diagrams via `astro-mermaid` integration (must come before starlight in integrations)

## Content conventions
- Use clear H2/H3 headings
- Put prerequisites before steps
- Put exact commands in fenced code blocks
- Use frontmatter for title, description, and sidebar ordering
- Prefer file-based organization over hardcoded navigation where possible
- Use Starlight aside syntax (`:::note`, `:::tip`, `:::caution`, `:::danger`) not MkDocs admonitions
- Use `<Tabs>` / `<TabItem>` components in MDX files for tabbed content

## Commands
- `pnpm dev` — local dev server
- `pnpm build` — production build
- `pnpm preview` — preview built site

## Guardrails
- Do not switch to a different docs framework unless explicitly asked
- Do not introduce SSR unless required
- Do not add React or other UI frameworks unless there is a concrete need
- Keep docs pages mostly Markdown-native for high-quality copy-as-markdown
