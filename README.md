# Hotel-ai

Hotel-ai is a bilingual hotel reception agent for making room reservations in English and Polish. The alpha uses a Django booking engine and a browser interface for voice and chat.

## Development environment

Use Git, authenticated GitHub CLI, Docker with Compose, and Node.js 20 or newer for browser automation. Python 3.13 is useful for host-side checks. Clone the private repository with `gh repo clone michalfrackowiak/hotel-ai`.

- [`AGENTS.md`](AGENTS.md) describes the branch, review, and merge workflow.
- [`docs/roadmap.md`](docs/roadmap.md) tracks milestone acceptance and completion. [`docs/handoff.md`](docs/handoff.md) records the latest verified state and next action across sessions.
- [`.agents/skills/`](.agents/skills/) contains the installed project skills. Start a new Codex session if they do not appear in the skill selector.
- [`.codex/config.toml`](.codex/config.toml) configures Playwright MCP for isolated browser checks and OpenAI Docs MCP for provider and Codex references. In a trusted Codex project, use `codex mcp list` to inspect the configured servers. Node downloads the pinned Playwright MCP package when needed. Its first browser launch may also need a browser download.
- [`docs/tooling-research.md`](docs/tooling-research.md) records the evaluated tooling and the rationale for using a repository handoff as shared memory.

`main` holds merged milestones. Use `develop/<milestone>` branches and pull requests for development. The first application milestone adds Docker startup instructions and the booking API reference.

### Skill sources

- `code-review-and-quality`, `context-engineering`, and `incremental-implementation` come from [Agent Skills](https://github.com/addyosmani/agent-skills/tree/301b02b12bff98e4688e6984af6556802322bbde/skills). The MIT license and the referenced checklists are included. The handoff and completion guidance follows this project's authorized review and merge workflow.
- `gh-fix-ci` comes from [OpenAI's curated skills](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/gh-fix-ci) and includes its upstream license. Its workflow uses the project's existing authorization to fix and recheck failing milestone checks.
