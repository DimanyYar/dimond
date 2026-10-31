# Contributing

How code gets from an idea into `main`. The coding rules themselves are in `CLAUDE.md`; they
apply to people and AI assistants alike.

## The path of every change

```
task → branch → commits → pull request → checks → review → squash merge → release tag
```

### Branches

One task, one branch from `main`, named by what it does: `feat/magic-link-login`,
`fix/expired-link-message`, `docs/adr-0010`. Nobody pushes to `main` directly.

### Commits

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
`test:`, `refactor:`, `chore:`, followed by a short sentence in the present tense.

```
feat: client can sign in by email link
fix: expired link shows the right message
```

### Pull requests

- Small: readable in one sitting, under ~800 changed lines. Big features are cut into steps
  (models → screen → emails).
- One PR does one thing.
- Fill in the template: what and why (with the PRD section), how to check by hand, screenshot
  for UI changes, the definition-of-done checklist.

### Checks

GitHub Actions runs on every PR: ruff (lint and format), mypy, the missing-migrations check
and pytest. A red PR is not reviewed.

### Review

1. **CodeRabbit** reviews every PR that is not a draft automatically; drafts are skipped
   (`auto_review.drafts: false` in `.coderabbit.yaml`). The author also posts
   `@coderabbitai review` as a PR comment, after opening the PR and after pushing fixes: a
   manual request starts a review even where automatic ones are off, so drafts get reviewed
   too. The author fixes its findings or replies why not.
2. **Dmitry** reviews for intent: does it do what the PRD says, and is the change
   understandable. "I don't understand this part" is a valid review comment.

### Merge

Only Dmitry merges, always with **Squash and merge**, so `main` reads as a list of features.
`main` is protected: green CI and one approval required, no force pushes.

### Releases

When `main` has something worth shipping, tag it (`v0.1.0`, `v0.2.0`, …). Deploys happen from
tags. Environments: local (Docker Compose) and production. The demo uses the mock tracker
instead of a staging server.

### Dependencies

Dependabot opens weekly update PRs. They go through the same checks and review. Adding a new
dependency by hand needs an ADR in `docs/adr/`.

## Definition of done

- [ ] Tests for the new behaviour, all tests green
- [ ] `ruff`, `mypy` and the migrations check green
- [ ] Migrations included in the same PR
- [ ] No new dependency without an ADR
- [ ] Screenshot for UI changes
- [ ] PRD section updated or flagged if behaviour changed
