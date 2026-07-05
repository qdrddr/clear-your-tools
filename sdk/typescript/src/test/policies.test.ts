import assert from "node:assert/strict";
import test from "node:test";

import {
  PolicyContext,
  batchToolPassThrough,
  isDescriptionPolicy,
  needsDescriptionReinstate,
  scoringPolicy,
  toolPolicies,
} from "../policies.js";

test("toolPolicies includes description variants", () => {
  const policies = toolPolicies();
  assert.ok(policies.includes("prune_optional_descriptions"));
  assert.ok(policies.includes("prune_all_descriptions"));
  assert.equal(policies.length, 5);
});

test("isDescriptionPolicy recognizes description variants only", () => {
  assert.equal(isDescriptionPolicy("prune_optional_descriptions"), true);
  assert.equal(isDescriptionPolicy("prune_all_descriptions"), true);
  assert.equal(isDescriptionPolicy("prune_optional"), false);
  assert.equal(isDescriptionPolicy("prune_all"), false);
  assert.equal(isDescriptionPolicy("always_include"), false);
  assert.equal(isDescriptionPolicy("not-a-policy"), false);
});

test("scoringPolicy maps description variants to base policies", () => {
  assert.equal(scoringPolicy("prune_optional_descriptions"), "prune_optional");
  assert.equal(scoringPolicy("prune_all_descriptions"), "prune_all");
  assert.equal(scoringPolicy("prune_optional"), "prune_optional");
  assert.equal(scoringPolicy("always_include"), "always_include");
});

test("needsDescriptionReinstate reflects context policies", () => {
  const base = new PolicyContext("prune_optional", "prune_all");
  assert.equal(needsDescriptionReinstate(base), false);

  const systemDesc = new PolicyContext(
    "prune_optional_descriptions",
    "prune_all",
  );
  assert.equal(needsDescriptionReinstate(systemDesc), true);

  const mcpDesc = new PolicyContext("prune_optional", "prune_all_descriptions");
  assert.equal(needsDescriptionReinstate(mcpDesc), true);

  const perTool = new PolicyContext("prune_optional", "prune_all");
  perTool.perTool = { Agent: "prune_optional_descriptions" };
  assert.equal(needsDescriptionReinstate(perTool), true);
});

test("batchToolPassThrough returns flags for each tool id", () => {
  const ctx = new PolicyContext("always_include", "always_include");
  const flags = batchToolPassThrough(["Agent", "grep"], ctx);
  assert.deepEqual(flags, [true, true]);
});
