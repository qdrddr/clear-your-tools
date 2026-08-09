# Benchmark design

Two layers: **Layer A** (existing external benchmark) + **Layer B** (CYT stress tests).

Task quality uses **Level 4 verifiers** only — independent of tool-call metrics ([evaluation-framework.md](./evaluation-framework.md)).

---

## Layer A — Existing benchmark

Run No CYT vs CYT configurations against a published MCP/tool-use evaluation.

| Advantage | Notes |
|-----------|-------|
| Externally recognizable | Pick benchmark with MCP or tool-calling tasks |
| Less construction bias | CYT wraps benchmark, doesn't own task authorship |
| Reproducibility | Cite benchmark + CYT version |

**Status:** Not integrated. Select benchmark (e.g. tool-use/agent benchmarks with MCP adapters) and add thin wrapper in harness.

---

## Layer B — CYT stress benchmark

50–100 tasks targeting CYT-specific mechanisms:

| Stress target | Category |
|---------------|----------|
| Tool selection under distractors | F — distractor-heavy |
| Optional property preservation | D |
| Enum pruning | C |
| Schema bloat (props/enums) | Isolation experiment |
| Schema-invalid calls | Verification corpus |
| Execution-unsuccessful (semantic) | Level 2 — valid schema, bad value |
| Pre-exposure / repeated tool use | Session promotion |
| Post-compaction reinjection | Compaction ablation |

### Task categories (Layer B)

| Cat | % | Tests |
|-----|---|-------|
| A. Single-tool | ~10% | Basic selection |
| B. Required-property | ~15% | Required fields survive prune |
| C. Enum-sensitive | ~15% | Relevant enum kept |
| D. Optional-property | ~15% | Relevant optional kept |
| E. Multi-step | ~20% | Cross-turn pruning |
| F. Distractor-heavy | ~25% | 3–5 relevant of 100+ tools |

---

## Task definition schema

```yaml
id: task-037
category: multi_tool
prompt: |
  Find open PRs in org/example, find alice's PR, comment "LGTM".
initial_state:
  fixture: github/org-example-prs
gold:
  tools: [list_pull_requests, create_pull_request_review_comment]
  properties:
    list_pull_requests: [owner, repo, state]
  enums:
    list_pull_requests.state: [open]
verifier:                    # Level 4 only
  - type: github_comment_exists
    repo: org/example
    body_contains: LGTM
  - type: pr_author
    author: alice
```

**Verifier determines task success — not tool-call counts.**

---

## Verification corpus (Level 1)

Labeled tool calls for MPR / FBR — separate from agent tasks.

| Case | Example | Expected |
|------|---------|----------|
| Valid | `{"repo": "foo/bar", "limit": 10}` | Allow |
| Missing required | `{"limit": 10}` | Deny (schema-invalid) |
| Wrong type | `{"repo": 123}` | Deny |
| Invalid enum | `{"sort": "foobar"}` | Deny |
| Additional property | `{"repo": "foo/bar", "x": true}` | Deny if disallowed |
| Unknown tool | `fake_tool` | Deny |

Run through `validate_pre_tool_call()` / `validate_json_schema()`.

**Existing:** Gherkin `hallucination_gate.feature`, unit tests.

**Gap:** Labeled JSONL corpus + MPR/FBR aggregator.

---

## Execution-unsuccessful corpus (Level 2)

Schema-valid calls that fail at backend — **Verify-Prevent does not catch**:

| Case | Args | Failure |
|------|------|---------|
| Wrong repo format | `{"repo": "/home/user/r"}` | Backend error |
| Nonexistent repo | `{"repo": "no/such"}` | 404 |
| Wrong ID | `{"pull_number": 99999}` | Not found |

Measure TESR separately from MPR.

---

## Recovery evaluation (Level 3)

Tasks designed to elicit schema-invalid first call:

```
malformed → deny + schema → retry → success
```

Metrics: recovery rate, extra calls/tokens/latency.

**Code path:** `PreToolDenyExposure` → session log → agent retry.

---

## Schema-bloat isolation

One tool, growing irrelevant schema:

| Condition | Bloat |
|-----------|-------|
| Control | 0 extra props |
| Bloat-10 | 10 irrelevant optional properties |
| Bloat-500e | 500 irrelevant enum values |

Same task every time; correct tool always in prune result.

**Expected:** Baseline tokens ↑, CYT ≈ flat.

---

## Pre-exposure / compaction tasks

### Pre-exposure

Multi-turn task requiring same tool 3+ times:

- Measure inject vs skip counts
- Compare tokens with pre-exposure enabled vs disabled (config/ablation)

**Code:** `filter_pre_exposed_tools()` in `pre_exposed.py`.

### Compaction

Multi-turn task spanning `preCompact`:

- Tool A used repeatedly before compaction
- After compaction: verify tool A still callable (re-inject if needed)
- Compare task success before/after compaction event

**Code:** `preCompact` hook, `test_session_compaction.py`.

---

## Unexposed-but-allowed calls (secondary)

Tasks where gold tool is **in Type-2 catalog** but **not injected** (pruned away):

- If model still calls with valid schema → allowed per Type-2 authority
- Log rate for "tools in weights" discussion

---

## Dataset composition

| Component | Target |
|-----------|--------|
| Real MCP backends | 5–10 via cyt-mcp |
| Total tools | 100–500 (real + synthetic distractors) |
| Tasks | 50–100 Layer B + Layer A subset |

Synthetic distractors: unrelated domains (calendar, weather, k8s, etc.).

---

## Fixture strategy

| Tier | Use |
|------|-----|
| Mock MCP | CI, fast iteration |
| Sandbox APIs | Paper numbers |
| Recorded responses | Deterministic regression |

Extend pattern from `sdk/e2e/fixtures/bm25_catalog.json`.

---

## Proposed layout (not yet created)

```
evals/cursor/
├── tasks/              # Layer B YAML
├── verification/       # Level 1 labeled corpus
├── execution-failures/ # Level 2 labeled corpus
├── fixtures/
├── catalogs/           # 25/100/250/500 tool sets
└── harness/
```
