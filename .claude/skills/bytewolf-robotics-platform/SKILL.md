```markdown
# bytewolf-robotics-platform Development Patterns

> Auto-generated skill from repository analysis

## Overview

This skill teaches you how to contribute effectively to the `bytewolf-robotics-platform` codebase, a Python-based robotics platform. You'll learn the project's coding conventions, commit patterns, and the main development workflows—from implementing features and simulation scenarios to updating contracts, documentation, and mission CLI scripts. This guide also covers how to write and organize tests, and provides handy `/commands` for common tasks.

---

## Coding Conventions

### File Naming

- **Style:** kebab-case for files (e.g., `fly-takeoff-hover-land.py`)
- **Example:**
  ```
  brain/cli/fly-takeoff-hover-land.py
  simulation/headless/scenarios.py
  ```

### Imports

- **Style:** Relative imports within modules
- **Example:**
  ```python
  from ..mission import MissionSpec
  from .artifacts import persist_artifact
  ```

### Exports

- **Style:** Named exports (explicitly listing what is exported)
- **Example:**
  ```python
  __all__ = ["MissionRunner", "MissionSpec"]
  ```

### Commit Messages

- **Style:** Conventional commits
- **Prefixes:** `feat`, `fix`, `docs`, `chore`
- **Example:**
  ```
  feat: add artifact persistence to mission runner
  fix: correct telemetry schema validation error
  docs: update ROS2 telemetry bridge documentation
  chore: refactor scenario loading logic
  ```

---

## Workflows

### Feature Implementation with Tests

**Trigger:** When adding a new feature or capability to a core module  
**Command:** `/new-feature`

1. Implement the feature in one or more module files (e.g., `brain/cli/`, `brain/mission/`, `simulation/`, `apps/dashboard/`, `robots/drone/`).
2. Create or update corresponding test files in `tests/` to cover the new feature.

**Example:**
```python
# brain/mission/mission_runner.py
class MissionRunner:
    def run(self):
        # New feature logic here
        pass

# tests/test_mission_runner.py
def test_run():
    runner = MissionRunner()
    assert runner.run() is not None
```

---

### Add or Update Simulation Scenario with Tests

**Trigger:** When adding or modifying a simulation scenario  
**Command:** `/new-simulation-scenario`

1. Add or update scenario logic in `simulation/headless/scenarios.py` or `simulation/scenarios/scenarios.py`.
2. Update or create tests in `tests/test_headless_scenarios.py` or `tests/test_simulation_configuration.py`.

**Example:**
```python
# simulation/headless/scenarios.py
def new_scenario():
    # Scenario logic here
    pass

# tests/test_headless_scenarios.py
def test_new_scenario():
    assert new_scenario() == expected_result
```

---

### Add Contract or Schema with Docs and Tests

**Trigger:** When defining a new interface, contract, or schema for system integration  
**Command:** `/new-contract`

1. Add or update schema/contract file (e.g., `shared/schemas/`, `interfaces/`, `brain/telemetry/ros2_contract.py`).
2. Update or add documentation in `docs/`.
3. Update or add config files in `platforms/` or `shared/config/`.
4. Create or update test files in `tests/`.

**Example:**
```python
# brain/telemetry/ros2_contract.py
class Ros2TelemetryContract:
    # Contract definition

# docs/ros2-telemetry-bridge-v0_1.md
# ROS2 Telemetry Bridge v0.1
...

# tests/test_ros2_telemetry_contract.py
def test_ros2_contract():
    contract = Ros2TelemetryContract()
    assert contract.is_valid()
```

---

### Add Artifacts or Persistence to Missions or Scenarios

**Trigger:** When persisting new types of artifacts or outcomes from missions or simulations  
**Command:** `/add-artifact-persistence`

1. Implement artifact logic in `brain/mission/artifacts.py`, `brain/cli/artifacts.py`, or `simulation/headless/scenarios.py`.
2. Update CLI mission scripts if needed (e.g., `brain/cli/fly_return_to_home.py`).
3. Update or create tests (e.g., `tests/test_cli_mission_artifacts.py`, `tests/test_mission_artifacts.py`).

**Example:**
```python
# brain/mission/artifacts.py
def persist_artifact(data):
    # Persistence logic

# tests/test_mission_artifacts.py
def test_persist_artifact():
    assert persist_artifact({"result": "ok"})
```

---

### Update Documentation with Code Changes

**Trigger:** When changing core workflows, contracts, or features that require updated documentation  
**Command:** `/update-docs`

1. Edit or add documentation files in `docs/` or `README.md`.
2. Optionally update related config or schema files.

**Example:**
```markdown
# docs/new-feature.md
## New Feature Documentation
...
```

---

### Fix or Enhance Mission CLI and Adapter

**Trigger:** When fixing bugs or improving mission execution logic in CLI and adapters  
**Command:** `/fix-mission-cli`

1. Edit mission CLI scripts (e.g., `brain/cli/fly_return_to_home.py`).
2. Edit adapter logic (e.g., `brain/adapters/mavsdk_adapter.py`).
3. Update or create tests (e.g., `tests/test_mavsdk_adapter.py`, `tests/test_cli_mission_artifacts.py`).

**Example:**
```python
# brain/adapters/mavsdk_adapter.py
def connect():
    # Fix connection logic

# tests/test_mavsdk_adapter.py
def test_connect():
    assert connect() is True
```

---

## Testing Patterns

- **Framework:** Unknown (likely pytest or unittest)
- **Test File Pattern:** `tests/test_*.py`
- **Test Example:**
  ```python
  # tests/test_example.py
  def test_feature():
      assert feature() == expected
  ```
- **Location:** All test files are in the `tests/` directory, matching the modules they cover.

---

## Commands

| Command                    | Purpose                                                        |
|----------------------------|----------------------------------------------------------------|
| /new-feature               | Implement a new feature with corresponding tests               |
| /new-simulation-scenario   | Add or update a simulation scenario and its tests              |
| /new-contract              | Add a new contract/schema, with docs and tests                 |
| /add-artifact-persistence  | Add or enhance artifact persistence in missions or scenarios   |
| /update-docs               | Update documentation in sync with code changes                 |
| /fix-mission-cli           | Fix or enhance mission CLI scripts and adapters                |
```
