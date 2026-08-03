"""Coverage for pinning the simulation stack the twin's evidence is valid against.

The baseline only earns its keep by catching drift, so every test here is about
a way PX4 or Gazebo could move without anyone noticing.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest.mock import Mock, patch

from simulation.baseline import (
    DEFAULT_BASELINE_PATH,
    Baseline,
    BaselineError,
    host_profile,
    load_baseline,
    verify_baseline,
)


_PX4_COMMIT = "d6f12ad1c4f70ad3230afd7d86e971421e02fef4"
_GZ_COMMIT = "b6127f4ec20de867e215fb5f78ae88b80f371909"
_MODEL_DIGEST = "cfe92f98360967faa895b77aa8a6fff3fc9b290286e94e45297c4bdd7b62fbf9"


def _baseline(**overrides: object) -> Baseline:
    defaults: dict[str, object] = {
        "version": "v0.2",
        "profile": "macos",
        "px4_commit": _PX4_COMMIT,
        "px4_describe": "v1.17.0",
        "gz_submodule_commit": _GZ_COMMIT,
        "gazebo_sim_version": "8.12.0",
        "baseline_world": "default",
        "required_patch": "simulation/px4/macos-build.patch",
        "file_hashes": {},
    }
    return Baseline(**{**defaults, **overrides})


def _document(
    *,
    shared_files: str = "",
    profile_files: str = "",
    required_patch: str = "p",
    profiles: str | None = None,
) -> str:
    """Render a minimal but structurally valid baseline document."""
    if profiles is None:
        profiles = (
            "profiles:\n"
            "  macos:\n"
            f"    required_patch: {required_patch}\n"
            "    gazebo_sim_version: '8'\n"
            "    files:\n" + (profile_files or "      {}\n")
        )
    return (
        "version: v0.2\n"
        "default_profile: macos\n"
        f"{profiles}"
        "px4:\n  describe: v1\n  commit: abc\n  gz_submodule_commit: def\n"
        "gazebo:\n  distribution: harmonic\n"
        "world:\n  baseline: default\n"
        "files:\n" + (shared_files or "  {}\n")
    )


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

    def test_the_linux_profile_needs_no_patch_and_says_so_explicitly(self) -> None:
        """PX4 v1.17.0 builds natively on Linux; applying the macOS patch there would be wrong."""
        baseline = load_baseline(DEFAULT_BASELINE_PATH, profile="linux")

        self.assertIsNone(baseline.required_patch)
        self.assertEqual(baseline.profile, "linux")

    def test_the_two_profiles_differ_only_where_the_host_really_differs(self) -> None:
        """A profile that diverged anywhere else would be excusing drift, not describing a host."""
        macos = load_baseline(DEFAULT_BASELINE_PATH, profile="macos")
        linux = load_baseline(DEFAULT_BASELINE_PATH, profile="linux")

        self.assertEqual(macos.px4_commit, linux.px4_commit)
        self.assertEqual(macos.gz_submodule_commit, linux.gz_submodule_commit)
        self.assertEqual(macos.baseline_world, linux.baseline_world)
        self.assertNotEqual(macos.gazebo_sim_version, linux.gazebo_sim_version)

        differing = {
            name
            for name in macos.file_hashes.keys() | linux.file_hashes.keys()
            if macos.file_hashes.get(name) != linux.file_hashes.get(name)
        }
        self.assertEqual(
            differing,
            {
                "CMakeLists.txt",
                "src/modules/simulation/gz_msgs/CMakeLists.txt",
                "src/modules/simulation/gz_plugins/moving_platform_controller/MovingPlatformController.cpp",
                "src/modules/simulation/gz_plugins/optical_flow/optical_flow.cmake",
            },
        )

    def test_the_world_and_model_hashes_are_shared_by_every_profile(self) -> None:
        """These carry the evidence's meaning, so no host may hold its own copy."""
        shared = (
            "Tools/simulation/gz/models/x500/model.sdf",
            "Tools/simulation/gz/worlds/default.sdf",
            "Tools/simulation/gz/worlds/windy.sdf",
            "Tools/simulation/gz/server.config",
        )
        macos = load_baseline(DEFAULT_BASELINE_PATH, profile="macos")
        linux = load_baseline(DEFAULT_BASELINE_PATH, profile="linux")

        for name in shared:
            with self.subTest(file=name):
                self.assertEqual(macos.file_hashes[name], linux.file_hashes[name])

    def test_refuses_a_profile_it_does_not_declare(self) -> None:
        with self.assertRaisesRegex(BaselineError, "declares no profile 'freebsd'"):
            load_baseline(DEFAULT_BASELINE_PATH, profile="freebsd")

    def test_refuses_a_profile_that_overrides_a_shared_file_hash(self) -> None:
        """Letting a host redefine a shared hash would turn real drift into a clean report."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.yaml"
            path.write_text(
                _document(
                    shared_files=f"  model.sdf: {_MODEL_DIGEST}\n",
                    profile_files=f"      model.sdf: {'f' * 64}\n",
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BaselineError, "redefines shared file hashes"):
                load_baseline(path)

    def test_requires_the_patch_field_to_be_present_even_when_it_is_null(self) -> None:
        """An explicit null claims 'this host needs none'; a missing key claims nothing."""
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.yaml"
            path.write_text(
                _document(
                    shared_files=f"  model.sdf: {_MODEL_DIGEST}\n",
                    profiles=(
                        "profiles:\n  macos:\n    gazebo_sim_version: '8'\n    files:\n      {}\n"
                    ),
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(BaselineError, "must be present, using null"):
                load_baseline(path)


class HostProfileTests(unittest.TestCase):
    def test_darwin_selects_macos_and_anything_else_selects_linux(self) -> None:
        with patch("simulation.baseline.platform.system", return_value="Darwin"):
            self.assertEqual(host_profile(), "macos")
        with patch("simulation.baseline.platform.system", return_value="Linux"):
            self.assertEqual(host_profile(), "linux")

    def test_refuses_a_baseline_that_pins_nothing(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.yaml"
            path.write_text(_document(), encoding="utf-8")

            with self.assertRaisesRegex(BaselineError, "a version alone is not a baseline"):
                load_baseline(path)

    def test_refuses_a_file_entry_that_is_not_a_digest(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.yaml"
            path.write_text(_document(shared_files="  model.sdf: latest\n"), encoding="utf-8")

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
