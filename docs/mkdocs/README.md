# GRAPHITE Documentation (MkDocs)

This directory contains the **user-facing product documentation site** for
Gosmart.Health GRAPHITE, built with [MkDocs](https://www.mkdocs.org/) and the
[Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) theme.

The built output is static HTML that the GoSmart web server pulls in and serves
under a product-specific slug via its reverse proxy.

## Prerequisites

Docs tooling lives in the `docs` dependency group (kept out of the runtime
dependencies):

```bash
uv sync --group docs
```

## Build & Preview

Run all commands from this directory (`docs/mkdocs/`):

```bash
# Live-reload preview at http://127.0.0.1:8000
uv run --group docs mkdocs serve

# Production build into ./site/ (fails on broken internal links)
uv run --group docs mkdocs build --strict --clean
```

## Deployment (Reverse Proxy)

- `mkdocs build` emits the static site into `docs/mkdocs/site/` (gitignored).
- All in-site links are relative, so the site can be served from **any base
  path** without rebuilding.
- Set `site_url` in [`mkdocs.yml`](./mkdocs.yml) to the final public URL
  (scheme + host + product slug). This value only affects canonical links, the
  generated `sitemap.xml`, and social preview metadata — never wire your
  reverse proxy to it.

## Structure

```
docs/mkdocs/
├── mkdocs.yml              # Site configuration (nav, theme, extensions)
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

This scaffold intentionally excludes auto-generated API reference pages. To add
one later, append `mkdocstrings[python]` to the `docs` dependency group in
`pyproject.toml`, register the `mkdocstrings` plugin in `mkdocs.yml`, and add an
`api-reference/` section under `docs/` that renders docstrings from
`src/gosmart_graphite/`.
