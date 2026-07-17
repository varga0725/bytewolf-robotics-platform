---
name: feature-implementation-with-tests
description: Workflow command scaffold for feature-implementation-with-tests in bytewolf-robotics-platform.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /feature-implementation-with-tests

Use this workflow when working on **feature-implementation-with-tests** in `bytewolf-robotics-platform`.

## Goal

Implements a new feature (often in a domain module), and adds or updates corresponding test files.

## Common Files

- `brain/cli/*.py`
- `brain/mission/*.py`
- `brain/mission_spec/*.py`
- `brain/safety/*.py`
- `brain/telemetry/*.py`
- `simulation/headless/*.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Implement feature in one or more module files (e.g., brain/..., simulation/..., apps/...)
- Create or update test files in tests/ to cover the new feature

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.