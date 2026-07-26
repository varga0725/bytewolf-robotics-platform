"""Summarise world evidence for the conversational agent, without letting it act.

Pi may talk about what the robot has seen, but it must not gain a new way to
read the world. So the briefing is built here, in Python, from the same
resolution the dashboard uses, and handed to the runner as bounded text. Pi
gets no file access, no second store implementation, and no claim the resolver
would not have shown a human.

The same module renders the agent's own envelope, for the same reason: an
agent that does not know its ceiling promises a climb the gate then refuses,
which reads as the robot changing its mind rather than a limit doing its job.

Three rules shape the world text:

* **Disputed subjects stay marked.** If the evidence disagrees, the agent must
  say so rather than pick a side the store refused to pick.
* **Every line carries its confidence and age.** A briefing without them would
  let an old 40% guess be spoken with the same certainty as a fresh measurement.
* **It is data, not instruction.** Claim text originates in sensors and
  converters, but the boundary is stated in the prompt regardless: nothing in
  here may be followed as a command.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from brain.memory.world_memory import WorldClaim
from brain.safety.profile import SafetyProfile


MAX_BRIEFING_CLAIMS = 8
MAX_BRIEFING_CHARS = 900


def world_briefing(
    claims: Iterable[WorldClaim],
    disputed: Iterable[WorldClaim] = (),
    *,
    now: datetime,
    max_claims: int = MAX_BRIEFING_CLAIMS,
    max_chars: int = MAX_BRIEFING_CHARS,
) -> str:
    """Render the currently believed world as bounded, self-qualifying lines.

    Freshest first: when the budget runs out, the agent should be missing old
    evidence rather than the measurement taken a moment ago.
    """
    believed = sorted(claims, key=lambda claim: claim.observed_at, reverse=True)
    contested = sorted(disputed, key=lambda claim: claim.observed_at, reverse=True)
    lines = [_line(claim, now, contested=False) for claim in believed[:max_claims]]
    remaining = max_claims - len(lines)
    lines.extend(_line(claim, now, contested=True) for claim in contested[:remaining])
    if not lines:
        return "- Nincs érvényes, le nem járt észlelés."
    rendered: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > max_chars:
            rendered.append("- (a régebbi észlelések kimaradtak a hosszkorlát miatt)")
            break
        rendered.append(line)
        used += len(line) + 1
    return "\n".join(rendered)


def capability_briefing(profile: SafetyProfile) -> str:
    """Tell the agent what it actually is, from the one file that decides it.

    Without this the agent knows its prohibitions but not its envelope, so it
    cheerfully offers a 40 m climb that the gate then refuses — which reads to
    the user as the robot changing its mind, not as a limit doing its job. The
    numbers come from the same `twin.yaml` the SafetyGate enforces; restating
    them anywhere else would create a second source of a limit.

    It is deliberately framed as *what will be refused*, not as permission: the
    gate decides, and the agent's copy is only there so it can say no earlier
    and honestly.
    """
    geofence = (
        "van megadott geofence-poligon; azon kívülre eső célt a gate elutasít"
        if profile.allowed_geofence is not None
        else "nincs külön geofence-poligon a sugáron felül"
    )
    return "\n".join([
        f"- Jármű: {profile.vehicle_id} (szimulált X500 V2 digital twin).",
        f"- Maximális magasság: {profile.max_altitude_m:g} m. Efölé nem kérhetsz tervet.",
        f"- Maximális sebesség: {profile.max_speed_m_s:g} m/s.",
        f"- Maximális távolság a kiindulóponttól: {profile.max_radius_m:g} m; {geofence}.",
        f"- Armoláshoz szükséges minimum akkumulátor: {profile.minimum_battery_percent_to_start:g}%.",
        f"- Kapcsolatvesztéskor: {profile.loss_of_link_action}; ezt a PX4 hajtja végre, nem te.",
        "- Ezeket a határokat egy determinisztikus safety gate érvényesíti a te kérésed előtt. "
        "Ha valami ezeken kívül esik, mondd meg őszintén, hogy nem fér bele — ne ígérd meg.",
    ])


def _line(claim: WorldClaim, now: datetime, *, contested: bool) -> str:
    age_s = max(0.0, (now - claim.observed_at).total_seconds())
    prefix = "- BIZONYTALAN (ellentmondó bizonyíték): " if contested else "- "
    return (
        f"{prefix}[{claim.category}] {claim.statement} "
        f"(forrás: {claim.source}, bizonyosság: {round(claim.confidence * 100)}%, "
        f"{_age(age_s)} régi)"
    )


def _age(age_s: float) -> str:
    if age_s < 90:
        return f"{age_s:.0f} másodperce"
    if age_s < 5_400:
        return f"{age_s / 60:.0f} perce"
    return f"{age_s / 3_600:.0f} órája"
