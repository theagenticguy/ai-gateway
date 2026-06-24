import { defineConfig } from "astro/config";
import path from "node:path";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import starlight from "@astrojs/starlight";
import sitemap from "@astrojs/sitemap";
import mermaid from "astro-mermaid";
import starlightPageActions from "starlight-page-actions";
import starlightLlmsTxt from "starlight-llms-txt";
import remarkGfm from "remark-gfm";

const SITE_BASE = "/ai-gateway";
const DOCS_ROOT = "src/content/docs";

/**
 * Integration: fix the home page's "copy / view as markdown" action targets.
 *
 * starlight-page-actions derives the markdown path as
 * `Astro.url.pathname.replace(/\/$/, "")` and appends `.md`. For every normal
 * page that yields a real file (`/ai-gateway/user-guide/` -> `/ai-gateway/user-guide.md`),
 * but for the SITE ROOT it collapses `/ai-gateway/` -> `/ai-gateway` -> `/ai-gateway.md`,
 * which sits OUTSIDE the base path and cannot be served by a project Pages site
 * (404). The actual home markdown lives at `/ai-gateway/index.md`.
 *
 * Two consumers on the home page need fixing, both rooted at that same path:
 *   1. the "View in Markdown" dropdown link  -> `<a href="/ai-gateway.md">`
 *   2. the "Copy Markdown" button            -> `<button data-path="/ai-gateway">`,
 *      whose client JS does `fetch(`${dataset.path}.md`)`.
 * Rewrite both in the built home page so each resolves to `/ai-gateway/index.md`.
 * Self-contained; no node_modules patch.
 */
function fixHomeMarkdownActionUrl() {
  return {
    name: "fix-home-markdown-action-url",
    hooks: {
      "astro:build:done": async ({ dir, logger }) => {
        const home = fileURLToPath(new URL("./index.html", dir));
        const replacements = [
          // dropdown link href
          [`href="${SITE_BASE}.md"`, `href="${SITE_BASE}/index.md"`],
          // copy-button data-path (JS appends ".md" -> /ai-gateway/index.md)
          [`data-path="${SITE_BASE}"`, `data-path="${SITE_BASE}/index"`],
        ];
        try {
          let html = await readFile(home, "utf-8");
          let changed = false;
          for (const [bad, good] of replacements) {
            if (html.includes(bad)) {
              html = html.split(bad).join(good);
              changed = true;
              logger.info(`Rewrote home markdown action: ${bad} -> ${good}`);
            }
          }
          if (changed) await writeFile(home, html);
        } catch (err) {
          logger.warn(`Could not post-process home page: ${err}`);
        }
      },
    },
  };
}

export default defineConfig({
  site: "https://theagenticguy.github.io",
  base: "/ai-gateway",

  integrations: [
    mermaid(),
    starlight({
      title: "AI Gateway",
      description:
        "Lightweight LLM inference gateway on AWS — route any AI agent to any model provider through a single endpoint.",

      favicon: "/favicon.svg",

      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/theagenticguy/ai-gateway",
        },
      ],

      editLink: {
        baseUrl:
          "https://github.com/theagenticguy/ai-gateway/edit/main/docs/",
      },

      lastUpdated: true,

      tableOfContents: {
        minHeadingLevel: 2,
        maxHeadingLevel: 4,
      },

      customCss: ["./src/styles/custom.css"],

      sidebar: [
        { label: "Home", slug: "index" },
        {
          label: "Getting Started",
          items: [{ autogenerate: { directory: "getting-started" } }],
        },
        {
          label: "User Guide",
          items: [{ autogenerate: { directory: "user-guide" } }],
        },
        {
          label: "Admin Guide",
          items: [{ autogenerate: { directory: "admin-guide" } }],
        },
        {
          label: "Developer Guide",
          items: [{ autogenerate: { directory: "developer-guide" } }],
        },
        {
          label: "Reference",
          collapsed: true,
          items: [{ autogenerate: { directory: "reference" } }],
        },
        {
          label: "ADRs",
          collapsed: true,
          items: [{ autogenerate: { directory: "adrs" } }],
        },
      ],

      plugins: [
        starlightPageActions({
          actions: {
            chatgpt: true,
            claude: true,
            markdown: true,
          },
        }),
        starlightLlmsTxt({
          projectName: "AI Gateway",
          description:
            "Lightweight LLM inference gateway on AWS — Portkey OSS on ECS Fargate with Cognito M2M auth, multi-provider routing, and dual API format support.",
          promote: ["index*", "getting-started/*"],
          exclude: ["reference/devtools-research", "reference/infra-stack-research", "adrs/*"],
        }),
      ],
    }),
    sitemap(),
    fixHomeMarkdownActionUrl(),
  ],

  markdown: {
    // remark-gfm is added explicitly: as of Astro 6.4 + @astrojs/mdx 5,
    // supplying a custom remarkPlugins array no longer auto-injects the
    // default GFM plugin into the MDX processor, so tables (and other GFM
    // syntax) silently stopped rendering in .mdx pages. Listing it here
    // restores GFM for both .md and .mdx.
    remarkPlugins: [remarkGfm, remarkStripMdLinks],
  },
});

/**
 * Remark plugin: rewrites relative .md/.mdx links to site-absolute,
 * trailing-slash Starlight URLs.
 *
 * Why site-absolute and not just `.md` -> `/`: Starlight serves every page as
 * a trailing-slash directory (`/admin-guide/deployment/`). A *relative* target
 * like `environments/` then resolves against the current directory in the
 * browser -> `/admin-guide/deployment/environments/` (a 404). Relative links
 * only happened to work from index pages. Resolving each link against the
 * page's own route and emitting an absolute `/ai-gateway/...` URL fixes leaf
 * pages and is position-independent. Raw `.md` links still work on GitHub
 * because this rewrite only runs at build time.
 */
function remarkStripMdLinks() {
  return (tree, file) => {
    // Directory of the source file relative to src/content/docs. Relative
    // markdown links resolve against the *source file's directory*, not the
    // rendered route — e.g. a link from "getting-started/prerequisites.mdx" to
    // "authentication.md" targets the sibling "getting-started/authentication",
    // not "getting-started/prerequisites/authentication".
    const abs = (file?.path ?? "").replace(/\\/g, "/");
    const idx = abs.indexOf(`${DOCS_ROOT}/`);
    let pageDir = "";
    if (idx !== -1) {
      const relFromDocs = abs.slice(idx + DOCS_ROOT.length + 1);
      const dir = path.posix.dirname(relFromDocs);
      pageDir = dir === "." ? "" : dir;
    }
    visitLinks(tree, pageDir);
  };
}

function visitLinks(node, pageDir) {
  if (node.type === "link" && node.url) {
    // Only process relative links (not http://, mailto:, anchors, etc.)
    if (!/^[a-z]+:/i.test(node.url) && !node.url.startsWith("/") && !node.url.startsWith("#")) {
      const [rawPath, fragment] = node.url.split("#");
      if (rawPath.endsWith(".md") || rawPath.endsWith(".mdx")) {
        const stripped = rawPath.replace(/\.mdx?$/, "");
        // Resolve the link target against the page's own source directory, then
        // make it site-absolute with the configured base and a trailing slash.
        let resolved = path.posix.normalize(
          path.posix.join("/", pageDir, stripped),
        );
        // An index page renders at its directory route, so a link to index.md
        // maps to the containing dir (e.g. "index" -> "/", "x/index" -> "/x").
        resolved = resolved.replace(/(^|\/)index$/, "$1");
        const url = `${SITE_BASE}${resolved}/`.replace(/\/{2,}/g, "/");
        node.url = fragment ? `${url}#${fragment}` : url;
      }
    }
  }
  if (node.children) {
    for (const child of node.children) {
      visitLinks(child, pageDir);
    }
  }
}
