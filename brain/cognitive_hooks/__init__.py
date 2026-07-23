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
    MAX_MEMORY_ITEMS,
    AdmissionRecord,
    HookRuntime,
    ProposalStore,
)
from brain.cognitive_hooks.memory_hook import (
    MEMORY_UPDATE_STATES,
    run_post_turn_memory,
)

CONTRACT_VERSION = COGNITIVE_HOOKS_CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "COGNITIVE_HOOKS_CONTRACT_VERSION",
    "MAX_MEMORY_ITEMS",
    "MAX_OPERATIONS",
    "MAX_VALUE_LENGTH",
    "MEMORY_UPDATE_STATES",
    "AdmissionRecord",
    "AdmissionResult",
    "HookRuntime",
    "Proposal",
    "ProposalContractError",
    "ProposalStore",
    "admit",
    "load_proposal",
    "run_post_turn_memory",
]
