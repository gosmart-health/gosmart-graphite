# GRAPHITE Documentation (Zensical)

This directory contains the **user-facing product documentation site** for
Gosmart.Health GRAPHITE, built with [Zensical](https://zensical.org/).

The built output is static HTML that the GoSmart web server pulls in and serves
under a product-specific slug via its reverse proxy.

## Prerequisites

Docs tooling lives in the `docs` dependency group (kept out of the runtime
dependencies):

```bash
uv sync --group docs
```

## Build & Preview

Run all commands from this directory (`docs/zensical/`):

```bash
# Live-reload preview at http://127.0.0.1:8000
uv run --group docs zensical serve

# Production build into ./site/ (fails on broken internal links)
uv run --group docs zensical build --strict --clean
```

## Deployment (Reverse Proxy)

- `zensical build` emits the static site into `docs/zensical/site/` (gitignored)
  and stores incremental build cache in `docs/zensical/.cache/` (gitignored).
- All in-site links are relative, so the site can be served from **any base
  path** without rebuilding.
- Set `site_url` in [`zensical.toml`](./zensical.toml) to the final public URL
  (scheme + host + product slug). This value only affects canonical links, the
  generated `sitemap.xml`, and social preview metadata — never wire your
  reverse proxy to it.

## Structure

```
docs/zensical/
├── zensical.toml           # Site configuration (nav, theme, extensions)
├── preview.sh              # Convenience script for `zensical serve`
├── README.md               # This file
└── docs/                   # Markdown source (the "docs_dir")
    ├── index.md            # Introduction
    ├── getting-started/    # Installation, Docker quickstart, first steps, Cypher guide, testing ecosystem
    ├── configuration/      # Environment variables, administration, DICOM/FHIR/LLM configs, conformance statement
    ├── mcp/                # MCP setup, tool catalog, utterance examples
    └── about/              # License, release notes
```

Internal engineering documents (`docs/design/` and `docs/design-notes-informal/`)
are **not** part of this site; they stay in-repo for contributors.

## Future: API Reference

This scaffold intentionally excludes auto-generated API reference pages. As
Zensical's plugin/module ecosystem matures (or via a static Markdown generator
targeting `docs/api-reference/`), API reference documentation from
`src/gosmart_graphite/` can be added as an `api-reference/` section in
`zensical.toml`.
