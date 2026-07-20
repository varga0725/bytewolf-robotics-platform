"""Summarise world evidence for the conversational agent, without letting it act.

Pi may talk about what the robot has seen, but it must not gain a new way to
read the world. So the briefing is built here, in Python, from the same
resolution the dashboard uses, and handed to the runner as bounded text. Pi
gets no file access, no second store implementation, and no claim the resolver
would not have shown a human.

Three rules shape the text:

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
