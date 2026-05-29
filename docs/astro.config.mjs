import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import sitemap from "@astrojs/sitemap";
import mermaid from "astro-mermaid";
import starlightPageActions from "starlight-page-actions";
import starlightLlmsTxt from "starlight-llms-txt";

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
  ],

  markdown: {
    remarkPlugins: [remarkStripMdLinks],
  },
});

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
