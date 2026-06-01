import assert from "node:assert/strict";
import test from "node:test";

import { countTokens } from "../tokens.js";

test("countTokens returns a positive count", () => {
  assert.ok(countTokens("hello") > 0);
});
