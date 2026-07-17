"""Check a local environment against the pinned X500 V2 simulation baseline.

PX4 and Gazebo live outside this repository, so a clean checkout cannot tell
which tree the committed evidence came from, and a drifted PX4 or Gazebo would
silently change what a passing scenario means. ``shared/config/x500v2/baseline.yaml``
is the record; this module is the check, and it reports drift rather than
guessing which side is right.

The baseline pins content, not just versions: PX4 v1.17.0 does not build on
Apple Silicon macOS as released, so the tree carries a recorded patch set and
reports itself as ``v1.17.0-dirty``. Hashing the patched files pins the tree to
its base commit plus exactly that patch, which a version string cannot do.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import subprocess

import yaml


DEFAULT_BASELINE_PATH = Path(__file__).resolve().parents[1] / "shared/config/x500v2/baseline.yaml"
DEFAULT_PX4_ROOT = Path(__file__).resolve().parents[1] / "PX4-Autopilot"

CommandRunner = Callable[..., subprocess.CompletedProcess]


class BaselineError(ValueError):
    """Raised when the baseline record itself cannot be read or trusted."""


@dataclass(frozen=True)
class Baseline:
    """The pinned simulation stack the twin's evidence is valid against."""

    version: str
    px4_commit: str
    px4_describe: str
    gz_submodule_commit: str
    gazebo_sim_version: str
    baseline_world: str
    required_patch: str
    file_hashes: Mapping[str, str]


@dataclass(frozen=True)
class Finding:
    """One baseline property and what the local environment actually holds."""

    subject: str
    expected: str
    actual: str

    @property
    def matches(self) -> bool:
        return self.expected == self.actual


@dataclass(frozen=True)
class BaselineReport:
    """An immutable comparison of one environment against the baseline."""

    findings: tuple[Finding, ...]

    @property
    def drift(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if not finding.matches)

    @property
    def matches_baseline(self) -> bool:
        return not self.drift


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> Baseline:
    """Read the pinned baseline, failing closed on anything it cannot trust."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise BaselineError(f"Cannot read the baseline '{path}': {error.strerror}.") from error
    except yaml.YAMLError as error:
        raise BaselineError(f"The baseline '{path}' is not valid YAML.") from error
    if not isinstance(document, Mapping):
        raise BaselineError("The baseline root must be a mapping.")

    px4 = _mapping(document, "px4")
    gazebo = _mapping(document, "gazebo")
    world = _mapping(document, "world")
    files = _mapping(document, "files")
    if not files:
        raise BaselineError("The baseline must pin at least one file; a version alone is not a baseline.")
    for name, digest in files.items():
        if not isinstance(name, str) or not _is_sha256(digest):
            raise BaselineError(f"The baseline file entry '{name}' must map to a sha256 digest.")

    return Baseline(
        version=_text(document, "version"),
        px4_commit=_text(px4, "commit"),
        px4_describe=_text(px4, "describe"),
        gz_submodule_commit=_text(px4, "gz_submodule_commit"),
        gazebo_sim_version=_text(gazebo, "sim_version"),
        baseline_world=_text(world, "baseline"),
        required_patch=_text(px4, "required_patch"),
        file_hashes=dict(files),
    )


def verify_baseline(
    baseline: Baseline,
    *,
    px4_root: Path = DEFAULT_PX4_ROOT,
    run_command: CommandRunner = subprocess.run,
) -> BaselineReport:
    """Compare a local PX4 and Gazebo against the baseline, without changing either."""
    findings = [
        Finding("px4 commit", baseline.px4_commit, _px4_commit(px4_root, run_command)),
        Finding(
            "px4 gz submodule commit",
            baseline.gz_submodule_commit,
            _gz_submodule_commit(px4_root, run_command),
        ),
        Finding("gazebo sim version", baseline.gazebo_sim_version, _gazebo_version(run_command)),
    ]
    findings.extend(
        Finding(f"px4 file {name}", digest, _file_digest(px4_root / name))
        for name, digest in sorted(baseline.file_hashes.items())
    )
    return BaselineReport(tuple(findings))


def _px4_commit(px4_root: Path, run_command: CommandRunner) -> str:
    return _git(px4_root, ("rev-parse", "HEAD"), run_command)


def _gz_submodule_commit(px4_root: Path, run_command: CommandRunner) -> str:
    return _git(px4_root / "Tools/simulation/gz", ("rev-parse", "HEAD"), run_command)


def _git(repository: Path, arguments: tuple[str, ...], run_command: CommandRunner) -> str:
    try:
        completed = run_command(
            ("git", "-C", str(repository), *arguments),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except OSError as error:
        return f"unavailable: {error}"
    except subprocess.TimeoutExpired:
        return "unavailable: git timed out"
    if completed.returncode != 0:
        return f"unavailable: {completed.stderr.strip() or 'git failed'}"
    return completed.stdout.strip()


def _gazebo_version(run_command: CommandRunner) -> str:
    try:
        completed = run_command(
            ("gz", "sim", "--versions"), capture_output=True, text=True, timeout=10.0, check=False
        )
    except OSError as error:
        return f"unavailable: {error}"
    except subprocess.TimeoutExpired:
        return "unavailable: gz timed out"
    if completed.returncode != 0:
        return f"unavailable: {completed.stderr.strip() or 'gz failed'}"
    versions = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return versions[0] if versions else "unavailable: gz reported no version"


def _file_digest(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        return f"unavailable: {error.strerror}"


def _mapping(document: Mapping, key: str) -> Mapping:
    value = document.get(key)
    if not isinstance(value, Mapping):
        raise BaselineError(f"The baseline field '{key}' must be a mapping.")
    return value


def _text(document: Mapping, key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BaselineError(f"The baseline field '{key}' must be a non-empty string.")
    return value.strip()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def main(arguments: tuple[str, ...] | None = None) -> int:
    """Report whether this environment matches the baseline; non-zero on drift."""
    parser = argparse.ArgumentParser(description="Verify the local PX4/Gazebo stack against the pinned baseline.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    parser.add_argument("--px4-root", type=Path, default=DEFAULT_PX4_ROOT)
    parsed = parser.parse_args(arguments)

    baseline = load_baseline(parsed.baseline)
    report = verify_baseline(baseline, px4_root=parsed.px4_root)

    print(f"Baseline {baseline.version}: PX4 {baseline.px4_describe}, Gazebo {baseline.gazebo_sim_version}, "
          f"world '{baseline.baseline_world}'")
    for finding in report.findings:
        status = "ok  " if finding.matches else "DRIFT"
        print(f"  {status} {finding.subject}")
        if not finding.matches:
            print(f"        expected {finding.expected}")
            print(f"        actual   {finding.actual}")
    if report.matches_baseline:
        print("This environment matches the baseline.")
        return 0
    print(f"\n{len(report.drift)} property/properties drifted from the baseline; evidence from this "
          f"environment is not comparable with the committed reports.")
    print(f"PX4 needs the recorded patch applied: {baseline.required_patch}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
