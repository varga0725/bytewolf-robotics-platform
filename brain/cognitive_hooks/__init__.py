"""ByteWolf Cognitive Hooks — background LLM job runtime.

Generalises the existing deterministic ``post_turn_memory_hook`` into a
proposal-based runtime: background LLM work flows through
``proposal -> schema validation -> policy/admission -> canonical store``.

Safety boundary: hooks, proposals and admission never reach the MAVSDK/PX4 or any
actuator API, and no background job can produce a flight action. Fail-closed: an
invalid, timed-out or malformed proposal writes nothing. Personal memory and the
evidence-backed world store stay separate; face identity is out of scope.

See ``docs/workstreams/cognitive-hooks.md`` for the versioned-contract plan, the
Definition of Done and the acceptance criteria.
"""

from brain.cognitive_hooks.admission import (
    MAX_OPERATIONS,
    MAX_VALUE_LENGTH,
    AdmissionResult,
    admit,
)
from brain.cognitive_hooks.contracts import (
    COGNITIVE_HOOKS_CONTRACT_VERSION,
    Proposal,
    ProposalContractError,
    load_proposal,
)
from brain.cognitive_hooks.runtime import (
    AdmissionRecord,
    HookRuntime,
    ProposalStore,
)

CONTRACT_VERSION = COGNITIVE_HOOKS_CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "COGNITIVE_HOOKS_CONTRACT_VERSION",
    "MAX_OPERATIONS",
    "MAX_VALUE_LENGTH",
    "AdmissionRecord",
    "AdmissionResult",
    "HookRuntime",
    "Proposal",
    "ProposalContractError",
    "ProposalStore",
    "admit",
    "load_proposal",
]
