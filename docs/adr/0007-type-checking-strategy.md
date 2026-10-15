# 0007. Type hints everywhere, strict checking where mistakes cost most

Status: Accepted, 2026-10-15

## Context

Type checking catches wrong arguments before runtime, but Django predates type hints and a
fully strict mypy setup adds friction while the skeleton is still moving.

## Decision

Type hints on every public function from day one. mypy runs in CI over `apps/` with
django-stubs; `apps.*.services` and `apps.tracker.*` run in strict mode. The rest moves to
strict as it settles.

## Consequences

+ The business actions and the Jira integration are checked strictly from the start.
− Less strict code elsewhere for a while; tightening it is a planned follow-up.
