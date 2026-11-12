# Hotel-ai

1. At session start, read `docs/roadmap.md` for the scope and acceptance checks and `docs/handoff.md` for verified progress and the next action.
2. Branch from `main` into `develop/<milestone>`. Give each independent task an implementation owner and have another subagent review the resulting diff and verification evidence. Fix findings and recheck the affected paths before merging.
3. Run the milestone checks and record the results, decisions, and next action in the handoff. Open a pull request to `main` for the milestone, wait for configured checks, merge, and verify the merge. Update the roadmap in that PR.

For a failed GitHub Actions check, use the repository's `gh-fix-ci` skill. For a task involving OpenAI or Codex configuration, consult the OpenAI Docs MCP when available. Keep secrets and guest data in local configuration or test fixtures with fictional values.
