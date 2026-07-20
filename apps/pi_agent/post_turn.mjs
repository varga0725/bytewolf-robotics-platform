/**
 * Post-turn memory hook isolation and privacy-safe diagnostics.
 *
 * The hook runs after a safe final reply already exists.  Nothing it does may
 * suppress that reply, alter flight intent, or leak conversation content into
 * a log line.  Every failure resolves to a status word, never to an exception
 * or to a message the caller could render.
 */

import { admitMemoryDelta, mergeMemory } from "./memory.mjs";

/** The only values a caller — or the dashboard — may ever see. */
export const MEMORY_UPDATE_STATES = Object.freeze(["updated", "skipped", "unavailable"]);

/**
 * Messages this runner authors itself.  Anything else is replaced, because a
 * third-party message can carry request data, a URL, or a key fragment.
 */
const KNOWN_RUNNER_FAILURES = new Set([
  "Invalid Pi request.",
  "NVIDIA configuration is missing.",
  "Configured NIM model is unavailable.",
  "Pi did not produce a safe final reply.",
]);

const UNKNOWN_RUNNER_FAILURE = "unexpected runner failure";

export function safeMemoryUpdate(value) {
  return MEMORY_UPDATE_STATES.includes(value) ? value : "unavailable";
}

/**
 * Reduce a thrown value to an allow-listed diagnostic.
 *
 * The caller writes this to stderr, which is captured next to the user's
 * conversation.  Only literals authored in this repository survive.
 */
export function diagnosticFailureMessage(error) {
  const message = error instanceof Error ? error.message : "";
  return KNOWN_RUNNER_FAILURES.has(message) ? message : UNKNOWN_RUNNER_FAILURE;
}

/**
 * Run one isolated extraction attempt and merge whatever survives admission.
 *
 * `extract` is injected so the network call, the clock and the store stay
 * outside this decision.  A rejected promise anywhere — extractor, load, or
 * write — is a hook failure, reported as `unavailable` and never raised.
 */
export async function runPostTurnMemoryHook({
  extract,
  loadFacts,
  saveFacts,
  now,
  sessionId,
  turnId,
  userMessage,
  assistantReply,
}) {
  try {
    const operations = admitMemoryDelta(await extract({ userMessage, assistantReply }));
    if (!operations.length) return "skipped";
    const merged = mergeMemory(await loadFacts(sessionId), operations, now(), turnId);
    await saveFacts(sessionId, merged);
    return "updated";
  } catch {
    return "unavailable";
  }
}
