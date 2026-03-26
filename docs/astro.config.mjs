import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import sitemap from "@astrojs/sitemap";
import starlightPageActions from "starlight-page-actions";
import starlightLlmsTxt from "starlight-llms-txt";

export default defineConfig({
  site: "https://theagenticguy.github.io",
  base: "/ai-gateway",

  integrations: [
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

      head: [
        {
          tag: "script",
          attrs: { type: "module", defer: true },
          content: `
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
            mermaid.initialize({ startOnLoad: false, theme: 'dark' });
            function renderMermaid() {
              const els = document.querySelectorAll('pre.mermaid:not([data-processed])');
              els.forEach(el => el.setAttribute('data-processed', 'true'));
              if (els.length) mermaid.run({ nodes: els });
            }
            // Render immediately — by the time this module loads, DOM is ready
            renderMermaid();
            // Also handle Starlight view transitions (client-side navigation)
            document.addEventListener('astro:page-load', renderMermaid);
          `,
        },
      ],

      sidebar: [
        { label: "Home", slug: "index" },
        {
          label: "Getting Started",
          autogenerate: { directory: "getting-started" },
        },
        {
          label: "User Guide",
          autogenerate: { directory: "user-guide" },
        },
        {
          label: "Admin Guide",
          autogenerate: { directory: "admin-guide" },
        },
        {
          label: "Developer Guide",
          autogenerate: { directory: "developer-guide" },
        },
        {
          label: "Reference",
          collapsed: true,
          autogenerate: { directory: "reference" },
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
          exclude: ["reference/devtools-research", "reference/infra-stack-research"],
        }),
      ],
    }),
    sitemap(),
  ],

  markdown: {
    remarkPlugins: [remarkMermaid, remarkStripMdLinks],
  },
});

/**
 * Remark plugin: converts ```mermaid code blocks to raw <pre class="mermaid">
 * elements BEFORE Expressive Code processes them, so Mermaid can render
 * them client-side.
 */
function remarkMermaid() {
  return (tree) => {
    walkTree(tree);
  };
}

function walkTree(node) {
  if (node.type === "code" && node.lang === "mermaid") {
    // Convert to raw HTML so Expressive Code skips it
    node.type = "html";
    node.value = `<pre class="mermaid">\n${escapeHtml(node.value)}\n</pre>`;
    delete node.lang;
    delete node.meta;
  }
  if (node.children) {
    for (const child of node.children) {
      walkTree(child);
    }
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/**
 * Remark plugin: rewrites relative .md/.mdx links to trailing-slash URLs.
 * Keeps raw markdown links working on GitHub while producing correct URLs
 * for the built Starlight site.
 */
function remarkStripMdLinks() {
  return (tree) => {
    visitLinks(tree);
  };
}

function visitLinks(node) {
  if (node.type === "link" && node.url) {
    // Only process relative links (not http://, mailto:, etc.)
    if (!/^[a-z]+:/i.test(node.url)) {
      const [path, fragment] = node.url.split("#");
      if (path.endsWith(".md") || path.endsWith(".mdx")) {
        const stripped = path.replace(/\.mdx?$/, "/");
        node.url = fragment ? `${stripped}#${fragment}` : stripped;
      }
    }
  }
  if (node.children) {
    for (const child of node.children) {
      visitLinks(child);
    }
  }
}
