/**
 * One Pi SDK turn for the local ByteWolf dashboard.
 *
 * stdin  { session_id, text, world_context?, capability_context? }
 * stdout { text, requests_drone_action, memory_update }
 *
 * This process deliberately has no generic shell, file-edit, network, MAVSDK,
 * or PX4 tool. Durable memory is updated only by a separate post-turn hook;
 * Flight remains entirely in Python's
 * reviewed MissionSpec → SafetyGate → executor path.
 */

import { mkdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import {
  createAgentSession,
  DefaultResourceLoader,
  defineTool,
  ModelRuntime,
  SessionManager,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { extractMemoryDelta } from "./memory.mjs";
import { diagnosticFailureMessage } from "./post_turn.mjs";
import { systemPrompt } from "./prompt.mjs";
import { telemetryLine } from "./telemetry_view.mjs";

const ROOT = process.cwd();
const RUNTIME_DIR = path.join(ROOT, "var", "pi-agent");
const SESSIONS_DIR = path.join(RUNTIME_DIR, "sessions");
const MEMORY_DIR = path.join(RUNTIME_DIR, "memory");
const MODELS_PATH = path.join(ROOT, "apps", "pi_agent", "models.json");
const SESSION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_MESSAGE_CHARS = 2000;
const MAX_WORLD_CONTEXT_CHARS = 1200;

function toolResult(status, summary, nextActions = [], artifacts = [], data = undefined) {
  // One well-formed JSON document per tool result. An earlier version appended a
  // second, differently-shaped line after the JSON; a reader that parsed the
  // first line then had to guess at the rest, which is a contract only by habit.
  return JSON.stringify({
    status,
    summary,
    next_actions: nextActions,
    artifacts,
    ...(data === undefined ? {} : { data }),
  });
}

async function readJsonOr(pathname, fallback) {
  try {
    return JSON.parse(await readFile(pathname, "utf8"));
  } catch {
    return fallback;
  }
}

async function memoryFor(sessionId) {
  const document = await readJsonOr(path.join(MEMORY_DIR, `${sessionId}.json`), { facts: [] });
  if (!Array.isArray(document.facts)) return [];
  return document.facts
    .filter((fact) => fact && typeof fact.category === "string" && typeof fact.fact === "string")
    .slice(-40);
}

// The Node side is now only the extractor. Admission and the canonical store
// moved to the Python cognitive-hooks runtime, so this returns the raw proposed
// delta (or null on any extractor failure) and lets Python decide what is kept.
async function extractDeltaForTurn(request, assistantReply) {
  return extractMemoryDelta({
    fetchImpl: fetch,
    baseUrl: process.env.NIM_BASE_URL || "https://integrate.api.nvidia.com/v1",
    apiKey: process.env.NVIDIA_API_KEY,
    model: process.env.NIM_MEMORY_MODEL || process.env.NIM_MISSION_MODEL,
    userMessage: request.text,
    assistantReply,
  });
}

async function telemetrySummary() {
  const document = await readJsonOr(path.join(ROOT, "simulation", "artifacts", "dashboard", "live-telemetry.json"), null);
  const view = telemetryLine(document, Date.now());
  return view.usable
    ? toolResult(
        "success",
        view.summary,
        [],
        ["simulation/artifacts/dashboard/live-telemetry.json"],
        view.line,
      )
    : toolResult("warning", view.summary, ["Kérdezd meg, indítsam-e a szimulációt."], [], view.line);
}

async function visionSummary() {
  const candidates = [
    path.join(ROOT, "simulation", "artifacts", "dashboard", "detections.json"),
    path.join(ROOT, "simulation", "artifacts", "dashboard", "detections-down.json"),
  ];
  const results = [];
  for (const candidate of candidates) {
    const document = await readJsonOr(candidate, null);
    if (!document || document.validity !== "valid" || !Array.isArray(document.detections)) continue;
    results.push(...document.detections.slice(0, 8).map((item) => item?.label).filter((label) => typeof label === "string"));
  }
  return toolResult(
    results.length ? "success" : "warning",
    results.length ? `A kamerák ${results.length} észlelést jelentenek: ${results.join(", ")}.` : "Nincs használható vision észlelés.",
    results.length ? [] : ["Kérdezd meg, szeretne-e megfigyelési küldetést tervezni."],
    candidates,
  );
}

async function readRequest() {
  const raw = await new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { data += chunk; });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
  const request = JSON.parse(raw);
  if (!request || typeof request.session_id !== "string" || !SESSION_ID.test(request.session_id)
    || typeof request.text !== "string" || !request.text.trim() || request.text.length > MAX_MESSAGE_CHARS) {
    throw new Error("Invalid Pi request.");
  }
  // The briefing is optional and never trusted for length: an oversized one is
  // truncated rather than allowed to crowd out the safety instructions above it.
  const bounded = (value) => (typeof value === "string" ? value.slice(0, MAX_WORLD_CONTEXT_CHARS) : "");
  return {
    ...request,
    world_context: bounded(request.world_context),
    capability_context: bounded(request.capability_context),
  };
}

async function main() {
  const request = await readRequest();
  if (!process.env.NVIDIA_API_KEY || !process.env.NIM_MISSION_MODEL) {
    throw new Error("NVIDIA configuration is missing.");
  }
  await mkdir(SESSIONS_DIR, { recursive: true });
  await mkdir(MEMORY_DIR, { recursive: true });
  const sessionPath = path.join(SESSIONS_DIR, `${request.session_id}.jsonl`);
  const sessionManager = existsSync(sessionPath)
    ? SessionManager.open(sessionPath, SESSIONS_DIR, ROOT)
    : SessionManager.create(ROOT, SESSIONS_DIR, { id: request.session_id });
  const settingsManager = SettingsManager.inMemory({ compaction: { enabled: true }, retry: { enabled: false } });
  const loader = new DefaultResourceLoader({
    cwd: ROOT,
    agentDir: RUNTIME_DIR,
    settingsManager,
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    systemPromptOverride: () => systemPrompt(awaitedMemory, request.world_context, request.capability_context),
    appendSystemPromptOverride: () => [],
  });
  const awaitedMemory = await memoryFor(request.session_id);
  await loader.reload();
  const modelRuntime = await ModelRuntime.create({ modelsPath: MODELS_PATH, authPath: path.join(RUNTIME_DIR, "auth.json") });
  const model = modelRuntime.getModel("nvidia-nim", process.env.NIM_MISSION_MODEL);
  if (!model) throw new Error("Configured NIM model is unavailable.");
  let requestedFlight = false;
  let finalReply = null;
  const tools = [
    defineTool({
      name: "get_drone_state", label: "Drónállapot", description: "Read the current telemetry evidence. Never controls the drone.",
      parameters: Type.Object({}),
      execute: async () => ({ content: [{ type: "text", text: await telemetrySummary() }], details: {} }),
    }),
    defineTool({
      name: "get_vision_summary", label: "Vision összegzés", description: "Read the current camera detection evidence. Never controls the drone.",
      parameters: Type.Object({}),
      execute: async () => ({ content: [{ type: "text", text: await visionSummary() }], details: {} }),
    }),
    defineTool({
      name: "draft_flight_request", label: "Repülési terv kérése", description: "Request a reviewed flight plan. This never executes a flight.",
      parameters: Type.Object({ request: Type.String({ minLength: 1, maxLength: MAX_MESSAGE_CHARS }) }),
      execute: async (_id, params) => {
        requestedFlight = true;
        return { content: [{ type: "text", text: toolResult("success", "A repülési tervkérés a SafetyGate felé került.", ["Várd meg a dashboard külön jóváhagyását."]) }], details: { request: params.request } };
      },
    }),
    defineTool({
      name: "respond_to_user", label: "Válasz a felhasználónak", description: "Emit the sole short, natural Hungarian reply shown to the user after all required tools.",
      parameters: Type.Object({ reply: Type.String({ minLength: 1, maxLength: MAX_MESSAGE_CHARS }) }),
      execute: async (_id, params) => {
        finalReply = params.reply.trim();
        return { content: [{ type: "text", text: toolResult("success", "A végválasz elkészült.") }], details: {}, terminate: true };
      },
    }),
  ];
  const { session } = await createAgentSession({
    cwd: ROOT,
    agentDir: RUNTIME_DIR,
    modelRuntime,
    model,
    thinkingLevel: "off",
    sessionManager,
    settingsManager,
    resourceLoader: loader,
    customTools: tools,
    tools: ["get_drone_state", "get_vision_summary", "draft_flight_request", "respond_to_user"],
  });
  try {
    await session.prompt(request.text, { expandPromptTemplates: false });
    // A tool-capable model can complete a valid flight-request tool turn but
    // omit its final presentation tool.  The intent is still safe and typed;
    // use a fixed human-facing acknowledgement rather than ever exposing its
    // raw reasoning or silently discarding the plan request.
    if (typeof finalReply !== "string" || !finalReply || finalReply.length > MAX_MESSAGE_CHARS) {
      if (requestedFlight) {
        finalReply = "Rendben, készítek egy biztonságos tervet a szimulációhoz.";
      } else {
        throw new Error("Pi did not produce a safe final reply.");
      }
    }
    // Memory extraction is isolated from the conversational turn. Its failure
    // must never suppress an otherwise safe chat reply or alter flight intent.
    // The raw delta crosses to Python, which validates, admits and stores it.
    const memoryDelta = await extractDeltaForTurn(request, finalReply);
    process.stdout.write(JSON.stringify({ text: finalReply, requests_drone_action: requestedFlight, memory_delta: memoryDelta }));
  } finally {
    session.dispose();
  }
}

main().catch((error) => {
  // stderr is captured by the Python boundary and never returned to the UI.
  // Only messages authored here survive: a third-party error text can carry
  // request content, an endpoint, or a key fragment.
  process.stderr.write(`Pi runner failed: ${diagnosticFailureMessage(error)}\n`);
  process.exitCode = 1;
});
