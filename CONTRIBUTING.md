# Contributing to ViralCutter

Thanks for helping make the open-source Opus Clip alternative better! 🚀

## AI coding agents

The repository provides native instruction entry points for common coding agents:

- OpenAI Codex and agents that support `AGENTS.md`: [`AGENTS.md`](AGENTS.md)
- Claude Code: [`CLAUDE.md`](CLAUDE.md)
- Gemini CLI: [`GEMINI.md`](GEMINI.md)
- GitHub Copilot: [`.github/copilot-instructions.md`](.github/copilot-instructions.md)
- Cursor: [`.cursor/rules/cat-project.mdc`](.cursor/rules/cat-project.mdc)

`AGENTS.md` is the single source of truth; tool-specific files only point agents to it. These files provide guidance, not GitHub credentials or write access. Agents must use a branch and pull request, and must not bypass repository protections. The current `main` branch policy requires an approval from someone other than the latest pusher.

## Before you start

- Read [`AGENTS.md`](AGENTS.md) for the current project rules.
- [`docs/DEVELOPER_HANDOVER.md`](docs/DEVELOPER_HANDOVER.md) and
  [`docs/REMAINING_AFTER_V6_9.md`](docs/REMAINING_AFTER_V6_9.md) are historical
  snapshots, not current handover or roadmap documents. Verify their claims
  against the current code, tests, `app_version.py`, and `changelog.md`.

## Hard rules (the CI will enforce these)

1. **i18n parity** — every new English UI string must be added to all 4
   locales (`i18n/locale/en_US.json`, `ar_SA.json`, `pt_BR.json`, `tr_TR.json`,
   indent=4) or `tests/test_i18n_completeness.py` fails.
2. **Version rule** — `app_version.py` must match the latest `changelog.md`
   entry, and every release needs a tag with the same number (the auto-updater
   compares tags).
3. **Lint** — `ruff check .` must pass (config in `pyproject.toml`).
4. **Tests** — the full suite must stay green: `python -m pytest tests/`.
5. **Never touch the face-crop loop in `scripts/edit_video.py`** for aspect
   changes — that path was the root cause of the v6.6 A/V-sync fix. New
   framing formats go through `scripts/reframe.py` (post-stage).
6. **Safety is sacred** — this tool exists so channels don't get strikes.
   Never weaken the blocklist/censor/scorecard defaults without discussion.

## Development loop

```bash
uv sync            # reproducible dev env (or install_dependencies.bat)
ruff check .       # lint
python -m pytest tests/   # full suite
python -m scripts.preflight --check   # environment sanity
```

New features need tests. Run the current suite rather than relying on historical test counts. Keep tests hermetic where possible; SDK-dependent paths should be covered with deterministic mocks.

## Submitting

- Small, focused PRs.
- Update `changelog.md` and maintained documentation when behavior or release
  details change. `docs/REMAINING_AFTER_V6_9.md` is historical, not the current
  roadmap.
- Treat `.github/workflows/*` changes as high impact: explain the reason and risk in the PR, keep permissions least-privilege, and verify the workflow results before merge.
