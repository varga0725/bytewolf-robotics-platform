---
name: add-or-update-simulation-scenario-with-tests
description: Workflow command scaffold for add-or-update-simulation-scenario-with-tests in bytewolf-robotics-platform.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /add-or-update-simulation-scenario-with-tests

Use this workflow when working on **add-or-update-simulation-scenario-with-tests** in `bytewolf-robotics-platform`.

## Goal

Adds or modifies a simulation scenario and updates or creates corresponding tests.

## Common Files

- `simulation/headless/scenarios.py`
- `simulation/scenarios/scenarios.py`
- `tests/test_headless_scenarios.py`
- `tests/test_simulation_configuration.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Add or update scenario logic in simulation/headless/scenarios.py or simulation/scenarios/scenarios.py
- Update or create test in tests/test_headless_scenarios.py or tests/test_simulation_configuration.py

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.