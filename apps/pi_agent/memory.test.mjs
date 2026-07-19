import assert from "node:assert/strict";
import test from "node:test";

import { admitMemoryDelta, extractMemoryDelta, memoryExtractorPayload, mergeMemory } from "./memory.mjs";

test("admits a bounded non-sensitive preference delta", () => {
  const operations = admitMemoryDelta({
    kind: "memory_delta",
    operations: [{ op: "upsert", category: "preference", value: "A Baylands világ legyen az alapértelmezett." }],
  });
  assert.deepEqual(operations, [{ op: "upsert", category: "preference", fact: "A Baylands világ legyen az alapértelmezett." }]);
});

test("rejects sensitive and unknown memory suggestions", () => {
  const operations = admitMemoryDelta({
    kind: "memory_delta",
    operations: [
      { op: "upsert", category: "preference", value: "Az API kulcsom: abc" },
      { op: "upsert", category: "place_label", value: "user@example.com" },
      { op: "upsert", category: "biometric_identity", value: "Ferenc arca" },
    ],
  });
  assert.deepEqual(operations, []);
});

test("merge deduplicates, replaces a name, and applies forget before upsert", () => {
  const existing = [
    { category: "name", fact: "Péter", recorded_at: "old" },
    { category: "preference", fact: "Baylands", recorded_at: "old" },
  ];
  const merged = mergeMemory(existing, [
    { op: "forget", category: "preference", fact: "Baylands" },
    { op: "upsert", category: "name", fact: "Ferenc" },
    { op: "upsert", category: "preference", fact: "Baylands" },
  ], "now", "turn-1");
  assert.deepEqual(merged.map(({ category, fact }) => ({ category, fact })), [
    { category: "name", fact: "Ferenc" },
    { category: "preference", fact: "Baylands" },
  ]);
});

test("extractor uses a forced typed call and fails closed for malformed output", async () => {
  const payload = memoryExtractorPayload("model", "szia", "Szia!");
  assert.equal(payload.tool_choice.function.name, "propose_memory_delta");
  const value = await extractMemoryDelta({
    fetchImpl: async () => ({ ok: true, json: async () => ({ choices: [{ message: { tool_calls: [] } }] }) }),
    baseUrl: "https://example.test/v1",
    apiKey: "test",
    model: "model",
    userMessage: "szia",
    assistantReply: "Szia!",
  });
  assert.equal(value, null);
});

test("extractor accepts only the structured memory proposal", async () => {
  const value = await extractMemoryDelta({
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({ choices: [{ message: { tool_calls: [{ function: { arguments: JSON.stringify({ kind: "memory_delta", operations: [{ op: "upsert", category: "name", value: "Ferenc" }] }) } }] } }] }),
    }),
    baseUrl: "https://example.test/v1/",
    apiKey: "test",
    model: "model",
    userMessage: "A nevem Ferenc.",
    assistantReply: "Örülök, Ferenc.",
  });
  assert.deepEqual(admitMemoryDelta(value), [{ op: "upsert", category: "name", fact: "Ferenc" }]);
});
