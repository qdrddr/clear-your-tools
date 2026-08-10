# Benchmark design

**Layer B — CYT stress benchmark** only. Task quality uses **Level 4 verifiers** — independent of tool-call metrics ([evaluation-framework.md](./evaluation-framework.md)).

External benchmarks deferred (integration cost; Layer B covers CYT-specific claims).

---

## Layer B — CYT stress benchmark

~50 tasks targeting mechanisms existing generic benchmarks won't isolate:

| Stress target | Category |
| --------------- | ---------- |
| Tool selection under distractors | F — distractor-heavy |
| Optional property preservation | D |
| Enum pruning | C |
| Schema-invalid calls | Verification corpus (static, not agent tasks) |
| Multi-step cross-turn pruning | E |

### Task categories

| Cat | % | Tests |
| ----- | --- | ------- |
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
verifier:                    # Level 4 only
  - type: github_comment_exists
    repo: org/example
    body_contains: LGTM
  - type: pr_author
    author: alice
```

**Verifier determines task success — not tool-call counts.**

Gold `tools` list used for required-tool recall only (not property/enum gold sets).

---

## Verification corpus (Level 1)

Labeled tool calls for MPR — **separate from agent tasks**, run through gate statically.

| Case | Example | Expected |
| ------ | --------- | ---------- |
| Valid | `{"repo": "foo/bar", "limit": 10}` | Allow |
| Missing required | `{"limit": 10}` | Deny (schema-invalid) |
| Wrong type | `{"repo": 123}` | Deny |
| Invalid enum | `{"sort": "foobar"}` | Deny |
| Additional property | `{"repo": "foo/bar", "x": true}` | Deny if disallowed |
| Unknown tool | `fake_tool` | Deny |

Run through `validate_pre_tool_call()` / `validate_json_schema()`.

**Existing:** Gherkin `hallucination_gate.feature`, unit tests.

**Gap:** Labeled JSONL corpus + MPR aggregator in harness.

---

## Dataset composition

| Component | Target |
| ----------- | -------- |
| Real MCP backends | 5–10 via cyt-mcp |
| Total tools | 100–250 (real + synthetic distractors) |
| Tasks | ~50 Layer B |

Synthetic distractors: unrelated domains (calendar, weather, k8s, etc.).

---

## Fixture strategy

| Tier | Use |
| ------ | ----- |
| Mock MCP | CI, fast iteration |
| Sandbox APIs | Paper numbers |
| Recorded responses | Deterministic regression |

Extend pattern from `sdk/e2e/fixtures/bm25_catalog.json`.

---

## Proposed layout (not yet created)

```text
evals/cursor/
├── tasks/              # Layer B YAML (~50)
├── verification/       # Level 1 labeled corpus
├── fixtures/
├── catalogs/           # 25/100/250 tool sets
└── harness/
```
