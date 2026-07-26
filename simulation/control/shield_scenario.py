"""Score what the runtime shield did in SITL. Pure function of the recording.

The roadmap's G4 gate asks for numbers before the shield is allowed to act:
minimum clearance, intervention rate, false-stop rate, and whether fail-closed
behaviour actually held when the sensor went quiet. This turns one recorded run
into those numbers.

Clearance is measured from **Gazebo ground truth**, never from the flight
stack's own estimate. The whole question is whether the shield kept the vehicle
away from a real object, and asking the same estimator the shield was reading
would answer a different one.

Two failure directions matter equally and are scored separately. A shield that
never stops is useless; a shield that always stops is worse than useless,
because it is the one that gets switched off.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from math import hypot, isfinite


@dataclass(frozen=True)
class ShieldSample:
    """One control-loop iteration, as it was recorded."""

    at_s: float
    #: What the nominal planner asked for, before the shield saw it.
    nominal_speed_m_s: float
    #: The shield's verdict, by name.
    verdict: str
    #: What was actually commanded after the shield.
    commanded_speed_m_s: float
    #: Ground-truth distance to the obstacle at this instant, when there is one.
    clearance_m: float | None = None


@dataclass(frozen=True)
class ShieldScenarioReport:
    """What one shield run measured, and what it is allowed to claim."""

    scenario: str
    recorded_at: str
    mode: str
    samples: int
    #: How often the shield refused what the planner asked for.
    interventions: int
    intervention_rate: float
    #: Interventions with no obstacle anywhere near: the shield crying wolf.
    false_stops: int
    false_stop_rate: float
    #: The closest the vehicle ever came, from Gazebo ground truth.
    minimum_clearance_m: float | None
    #: What the twin insists on standing off by.
    required_clearance_m: float
    #: Ground-truth clearance at the shield's first blocking verdict. In shadow
    #: this is the number that matters: nothing stopped the vehicle, so the
    #: question is whether the shield would have stopped it in time.
    clearance_at_first_intervention_m: float | None = None
    verdicts: dict[str, int] = field(default_factory=dict)
    passed: bool = False
    findings: list[str] = field(default_factory=list)
    proof_level: str = "app+SITL"

    def as_document(self) -> dict:
        return asdict(self)


def evaluate_shield_run(
    *,
    scenario: str,
    mode: str,
    samples: Sequence[ShieldSample],
    required_clearance_m: float,
    expect_intervention: bool,
    recorded_at: str,
    #: True when the scenario is about what the sensor sees, so a run in which
    #: it saw nothing has not tested what it claims to.
    requires_sensor: bool = False,
    #: True when the verdict depends on how close the vehicle actually came.
    requires_ground_truth: bool = False,
    #: Beyond this a stop cannot be explained by anything the sensor saw.
    false_stop_clearance_m: float = 15.0,
) -> ShieldScenarioReport:
    """Turn one recorded run into the gate's numbers, and a verdict."""
    findings: list[str] = []
    if not samples:
        return _empty(scenario, mode, required_clearance_m, recorded_at)

    verdicts: dict[str, int] = {}
    for sample in samples:
        verdicts[sample.verdict] = verdicts.get(sample.verdict, 0) + 1

    interventions = [sample for sample in samples if sample.verdict != "clear"]
    clearances = [
        sample.clearance_m
        for sample in samples
        if sample.clearance_m is not None and isfinite(sample.clearance_m)
    ]
    minimum_clearance = min(clearances) if clearances else None

    # A stop is "false" when nothing the sensor could see justifies it. Judged
    # on ground truth rather than on the shield's own reasoning, so a shield
    # that hallucinates obstacles cannot exonerate itself.
    false_stops = [
        sample
        for sample in interventions
        if sample.clearance_m is not None and sample.clearance_m > false_stop_clearance_m
    ]

    if expect_intervention and not interventions:
        findings.append("The shield never intervened in a run that placed something in its way.")
    # A run where the sensor never produced anything tested fail-closed, not
    # obstacle avoidance. It must not pass as the latter: the first attempt at
    # this scenario "passed" with 109 straight no_observation verdicts and no
    # clearance measurement at all.
    sensor_failures = verdicts.get("no_observation", 0) + verdicts.get("unusable_observation", 0)
    if requires_sensor and sensor_failures == len(samples):
        findings.append(
            f"Every one of the {len(samples)} samples was a sensor failure "
            f"({sensor_failures} no/unusable observation). The run measured fail-closed "
            "behaviour, not whether the shield sees obstacles."
        )
    if requires_ground_truth and minimum_clearance is None:
        findings.append(
            "No ground-truth clearance was recorded, so there is nothing to check the "
            "stopping distance against."
        )
    if not expect_intervention and interventions:
        findings.append(
            f"The shield intervened {len(interventions)} times on a clear path; "
            "a shield that stops for nothing is the one that gets switched off."
        )
    if false_stops:
        findings.append(
            f"{len(false_stops)} stops happened with more than {false_stop_clearance_m:g} m of "
            "clearance, which nothing the sensor saw can explain."
        )
    first_intervention_clearance = next(
        (sample.clearance_m for sample in interventions if sample.clearance_m is not None), None
    )
    if expect_intervention and mode == "active" and minimum_clearance is not None:
        # Active mode is judged on where the vehicle actually ended up, because
        # the shield was allowed to stop it.
        if minimum_clearance < required_clearance_m:
            findings.append(
                f"The vehicle came within {minimum_clearance:.2f} m, inside the "
                f"{required_clearance_m:.2f} m the twin insists on."
            )
    elif expect_intervention and mode == "shadow" and requires_ground_truth:
        # Shadow mode is judged on timing instead. Nothing stopped the vehicle,
        # so its closest approach measures the nominal planner, not the shield.
        # What the shield is answerable for is whether it raised the alarm while
        # there was still room -- which is exactly what the gate needs to know
        # before the shield is allowed to act.
        if first_intervention_clearance is None:
            findings.append(
                "No ground-truth clearance was recorded at the first intervention, so "
                "there is nothing to judge the shield's timing by."
            )
        elif first_intervention_clearance < required_clearance_m:
            findings.append(
                f"The shield first objected at {first_intervention_clearance:.2f} m, already "
                f"inside the {required_clearance_m:.2f} m standoff: it would not have "
                "stopped the vehicle in time."
            )
    if mode == "shadow" and any(
        sample.verdict != "clear" and sample.commanded_speed_m_s != sample.nominal_speed_m_s
        for sample in samples
    ):
        findings.append("A shadow run changed a commanded velocity; shadow must observe only.")
    if mode == "active" and any(
        sample.verdict != "clear" and sample.commanded_speed_m_s > 0.0 for sample in samples
    ):
        findings.append("An active run kept commanding motion through a blocking verdict.")

    return ShieldScenarioReport(
        scenario=scenario,
        recorded_at=recorded_at,
        mode=mode,
        samples=len(samples),
        interventions=len(interventions),
        intervention_rate=round(len(interventions) / len(samples), 4),
        false_stops=len(false_stops),
        false_stop_rate=round(len(false_stops) / len(samples), 4),
        minimum_clearance_m=None if minimum_clearance is None else round(minimum_clearance, 3),
        clearance_at_first_intervention_m=(
            None if first_intervention_clearance is None else round(first_intervention_clearance, 3)
        ),
        required_clearance_m=round(required_clearance_m, 3),
        verdicts=dict(sorted(verdicts.items())),
        passed=not findings,
        findings=findings,
    )


def _empty(scenario: str, mode: str, required_clearance_m: float, recorded_at: str):
    return ShieldScenarioReport(
        scenario=scenario,
        recorded_at=recorded_at,
        mode=mode,
        samples=0,
        interventions=0,
        intervention_rate=0.0,
        false_stops=0,
        false_stop_rate=0.0,
        minimum_clearance_m=None,
        required_clearance_m=round(required_clearance_m, 3),
        passed=False,
        findings=["The run recorded no samples, so it measured nothing."],
    )


def clearance_to(position_xy: tuple[float, float], obstacle_xy: tuple[float, float]) -> float:
    """Horizontal ground-truth distance between the vehicle and the obstacle."""
    return hypot(position_xy[0] - obstacle_xy[0], position_xy[1] - obstacle_xy[1])
