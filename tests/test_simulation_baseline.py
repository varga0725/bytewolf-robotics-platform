"""Coverage for pinning the simulation stack the twin's evidence is valid against.

The baseline only earns its keep by catching drift, so every test here is about
a way PX4 or Gazebo could move without anyone noticing.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest.mock import Mock

from simulation.baseline import (
    DEFAULT_BASELINE_PATH,
    Baseline,
    BaselineError,
    load_baseline,
    verify_baseline,
)


_PX4_COMMIT = "d6f12ad1c4f70ad3230afd7d86e971421e02fef4"
_GZ_COMMIT = "b6127f4ec20de867e215fb5f78ae88b80f371909"
_MODEL_DIGEST = "cfe92f98360967faa895b77aa8a6fff3fc9b290286e94e45297c4bdd7b62fbf9"


def _baseline(**overrides: object) -> Baseline:
    defaults: dict[str, object] = {
        "version": "v0.1",
        "px4_commit": _PX4_COMMIT,
        "px4_describe": "v1.17.0",
        "gz_submodule_commit": _GZ_COMMIT,
        "gazebo_sim_version": "8.12.0",
        "baseline_world": "default",
        "required_patch": "simulation/px4/macos-build.patch",
        "file_hashes": {},
    }
    return Baseline(**{**defaults, **overrides})


def _runner(px4_commit: str = _PX4_COMMIT, gz_commit: str = _GZ_COMMIT, gz_version: str = "8.12.0") -> Mock:
    """Fake the two tools the check reads: git and gz."""

    def run(command, **_kwargs):
        if command[0] == "gz":
            return Mock(returncode=0, stdout=f"{gz_version}\n", stderr="")
        repository = command[2]
        commit = gz_commit if repository.endswith("Tools/simulation/gz") else px4_commit
        return Mock(returncode=0, stdout=f"{commit}\n", stderr="")

    return Mock(side_effect=run)


class LoadBaselineTests(unittest.TestCase):
    def test_reads_the_shipped_baseline(self) -> None:
        baseline = load_baseline(DEFAULT_BASELINE_PATH)

        self.assertEqual(baseline.px4_describe, "v1.17.0")
        self.assertEqual(baseline.gazebo_sim_version, "8.12.0")
        self.assertEqual(baseline.baseline_world, "default")
        self.assertIn("Tools/simulation/gz/models/x500/model.sdf", baseline.file_hashes)

    def test_the_shipped_baseline_pins_the_macos_build_patch_it_names(self) -> None:
        """PX4 does not build on Apple Silicon unpatched, so the patch is part of the baseline."""
        baseline = load_baseline(DEFAULT_BASELINE_PATH)
        patch = DEFAULT_BASELINE_PATH.parents[3] / baseline.required_patch

        self.assertTrue(patch.is_file(), f"{baseline.required_patch} must be committed")
        self.assertIn("CMakeLists.txt", baseline.file_hashes)

    def test_refuses_a_baseline_that_pins_nothing(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.yaml"
            path.write_text(
                "version: v0.1\npx4:\n  describe: v1\n  commit: abc\n  gz_submodule_commit: def\n"
                "  required_patch: p\ngazebo:\n  sim_version: '8'\nworld:\n  baseline: default\nfiles: {}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BaselineError, "a version alone is not a baseline"):
                load_baseline(path)

    def test_refuses_a_file_entry_that_is_not_a_digest(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.yaml"
            path.write_text(
                "version: v0.1\npx4:\n  describe: v1\n  commit: abc\n  gz_submodule_commit: def\n"
                "  required_patch: p\ngazebo:\n  sim_version: '8'\nworld:\n  baseline: default\n"
                "files:\n  model.sdf: latest\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BaselineError, "must map to a sha256 digest"):
                load_baseline(path)

    def test_refuses_an_unreadable_or_malformed_baseline(self) -> None:
        with self.assertRaisesRegex(BaselineError, "Cannot read the baseline"):
            load_baseline(Path("/nonexistent/baseline.yaml"))

        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.yaml"
            path.write_text("- not a mapping\n", encoding="utf-8")

            with self.assertRaisesRegex(BaselineError, "root must be a mapping"):
                load_baseline(path)


class VerifyBaselineTests(unittest.TestCase):
    def test_accepts_an_environment_that_matches(self) -> None:
        report = verify_baseline(_baseline(), px4_root=Path("px4"), run_command=_runner())

        self.assertTrue(report.matches_baseline)
        self.assertEqual(report.drift, ())

    def test_catches_a_moved_px4_tree(self) -> None:
        report = verify_baseline(
            _baseline(), px4_root=Path("px4"), run_command=_runner(px4_commit="0" * 40)
        )

        self.assertFalse(report.matches_baseline)
        self.assertEqual([finding.subject for finding in report.drift], ["px4 commit"])

    def test_catches_a_moved_gazebo_asset_submodule(self) -> None:
        """The worlds and models the fixtures are rendered from live in this submodule."""
        report = verify_baseline(_baseline(), px4_root=Path("px4"), run_command=_runner(gz_commit="1" * 40))

        self.assertEqual([finding.subject for finding in report.drift], ["px4 gz submodule commit"])

    def test_catches_a_different_gazebo_release(self) -> None:
        """The wind fixture's force model is gz-sim 8 behaviour."""
        report = verify_baseline(_baseline(), px4_root=Path("px4"), run_command=_runner(gz_version="9.0.0"))

        self.assertEqual([finding.subject for finding in report.drift], ["gazebo sim version"])
        self.assertEqual(report.drift[0].actual, "9.0.0")

    def test_catches_an_edited_px4_file(self) -> None:
        with TemporaryDirectory() as directory:
            px4_root = Path(directory)
            (px4_root / "model.sdf").write_text("edited by hand", encoding="utf-8")

            report = verify_baseline(
                _baseline(file_hashes={"model.sdf": _MODEL_DIGEST}),
                px4_root=px4_root,
                run_command=_runner(),
            )

            self.assertFalse(report.matches_baseline)
            self.assertEqual([finding.subject for finding in report.drift], ["px4 file model.sdf"])

    def test_reports_a_missing_px4_file_as_drift_rather_than_crashing(self) -> None:
        report = verify_baseline(
            _baseline(file_hashes={"gone.sdf": _MODEL_DIGEST}),
            px4_root=Path("/nonexistent"),
            run_command=_runner(),
        )

        self.assertFalse(report.matches_baseline)
        self.assertIn("unavailable", report.drift[0].actual)

    def test_reports_an_absent_toolchain_as_drift_rather_than_crashing(self) -> None:
        """A missing PX4 checkout or Gazebo is drift, not a reason to claim a match."""
        for failure in (OSError("No such file"), subprocess.TimeoutExpired("gz", 10.0)):
            with self.subTest(failure=type(failure).__name__):
                report = verify_baseline(
                    _baseline(), px4_root=Path("px4"), run_command=Mock(side_effect=failure)
                )

                self.assertFalse(report.matches_baseline)
                self.assertTrue(all("unavailable" in finding.actual for finding in report.drift))

    def test_does_not_touch_the_px4_tree(self) -> None:
        run = _runner()

        verify_baseline(_baseline(), px4_root=Path("px4"), run_command=run)

        for call in run.call_args_list:
            self.assertIn(call.args[0][0], ("git", "gz"))
            self.assertNotIn("checkout", call.args[0])
            self.assertNotIn("apply", call.args[0])


if __name__ == "__main__":
    unittest.main()
