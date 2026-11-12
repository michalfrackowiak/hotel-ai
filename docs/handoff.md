# Session handoff

## Latest milestone

Development environment, PR [#1](https://github.com/michalfrackowiak/hotel-ai/pull/1). Docker Engine, Docker Compose, Python 3.13, Node.js 24, and authenticated GitHub CLI were verified on the development host. Playwright MCP 0.0.85 was fetched and its command reported the pinned version. `codex mcp list` discovered both repository MCP entries.

The roadmap and this handoff provide versioned memory across sessions. Selected Agent Skills provide implementation and review guidance. The project owner authorized opening and merging the reviewed milestone PRs. See [tooling research](tooling-research.md) for the Claude-Mem evaluation and external sources.

## Next action

Branch from `main` for the Dockerized Django booking engine. Start with one physical room of each capacity. Use Europe/Warsaw for hotel dates, PLN for pricing, and checkout as the exclusive end date. The booking database owns alpha availability. Port 8000 is occupied on the development host, so use a configurable local port with 8080 as the default.
