# Benchmark design

CYT Agent Task Benchmark — real MCP tools + synthetic distractors, deterministic success criteria.

---

## Dataset composition

| Component | Target | Source in repo |
|-----------|--------|----------------|
| Real MCP backends | 5–10 servers | `~/.config/cyt/mcp/<agent>.json`, cyt-mcp `backends.py` |
| Total tools | 100–500 | Real + synthetic distractors |
| Tasks | 50–100 (v1: 50) | **To build** under `evals/cursor/tasks/` |
| Gold annotations | Per task | Required tools, properties, enums, assertion script |

### Suggested real MCP domains

| Domain | Example tasks |
|--------|---------------|
| GitHub | Issues, PRs, comments, repos |
| Filesystem | Read/write/search files |
| Database | Query, insert (test DB) |
| Slack | Post message, list channels (sandbox) |
| Kubernetes | Get pods, logs (kind/minikube fixture) |

Use sandbox credentials; never hit production.

### Distractor tools

Unrelated tools from other domains (calendar, weather, search, Drive) to inflate catalog without affecting gold chain.

---

## Task categories

Build ~50 tasks distributed across categories (weights are suggestions):

### A. Simple single-tool tasks (~10%)

**Example:** "Find repository `org/example`."

**Gold:** `list_repositories` or `search_repositories` with correct args.

**Tests:** Basic tool selection under pruning.

---

### B. Required-property tasks (~15%)

**Example:** "Get pull request #123 from `org/example`."

**Gold:** Tool requiring `owner`, `repo`, `pull_number` — all required fields must survive prune.

---

### C. Enum-sensitive tasks (~15%)

**Example:** "List repositories sorted by updated."

**Schema:** `"sort": { "enum": ["created", "updated", "name"] }`

**Gold:** Enum value `updated` must remain in pruned schema.

**Codebase:** Enum pruning in Rust BM25 (`prune_enums: true`).

---

### D. Optional-property tasks (~15%)

**Example:** "Search issues created after 2025-01-01, limit 20."

**Gold:** Relevant optional params (`since`, `limit`) preserved; irrelevant optional params removed.

---

### E. Multi-tool tasks (~20%)

**Example:** "Find repo X, inspect latest release, create issue referencing that release."

**Gold chain:**

1. `get_repository` / search
2. `list_releases` / `get_latest_release`
3. `create_issue`

**Tests:** Pruning across multiple agent turns (session continuity via hook + session JSONL).

---

### F. Distractor-heavy tasks (~25%) — **most important**

**Setup:** 100+ tools in catalog; only 3–5 relevant.

**Example:** Same as E but with large distractor catalog.

**Tests:** Whether CYT preserves the 3–5 required tools at high catalog sizes.

---

## Task definition schema (proposed)

```yaml
id: task-037
category: multi_tool
prompt: |
  Find open pull requests in repository org/example,
  identify the one authored by alice, and add a comment "LGTM".
initial_state:
  fixture: github/org-example-prs
gold:
  tools: [list_pull_requests, create_pull_request_review_comment]
  properties:
    list_pull_requests: [owner, repo, state]
    create_pull_request_review_comment: [owner, repo, pull_number, body]
  enums:
    list_pull_requests.state: [open]
assertions:
  - type: github_comment_exists
    repo: org/example
    author: agent
    body_contains: LGTM
  - type: pr_author
    pr_number: from_state
    author: alice
```

---

## Verification benchmark (shape errors)

Separate from agent tasks — labeled tool-call corpus.

### Categories

| Case | Example args | Expected |
|------|--------------|----------|
| Valid | `{"repo": "foo/bar", "limit": 10}` | Allow |
| Missing required | `{"limit": 10}` | Deny |
| Wrong type | `{"repo": 123}` | Deny |
| Invalid enum | `{"sort": "foobar"}` | Deny |
| Additional property | `{"repo": "foo/bar", "banana": true}` | Deny (if schema disallows) |
| Unknown tool | `nonexistent_tool` | Deny |

### Implementation path

Extend `src/cyt_client/schema_validate.py` tests + export fixture JSONL.

**Existing:** Gherkin `hallucination_gate.feature`, unit tests in `src/tests/unit/`.

**Gap:** Labeled corpus with precision/recall/FBR aggregation.

---

## Recovery evaluation

CYT recovery flow (implemented):

```
LLM → malformed call → preToolUse → DENY + full schema → LLM retries → correct call
```

### Measure

| Metric | How |
|--------|-----|
| Recovery rate | Tasks that failed first call but succeeded within K turns |
| Recovery overhead | Extra tokens/latency/calls after first deny |
| Exposure persistence | `session_pre_tool_exposure.py` writes exposure on deny |

### Test design

Inject tasks that **prompt** common shape errors (or use weaker model) and count recovery within session.

**Gap:** No aggregate recovery metric in stats today.

---

## Shape vs semantic error cases (document in paper)

Include explicit **negative examples** CYT does not catch:

| Call | Schema accepts? | Semantically valid? |
|------|-----------------|---------------------|
| `{"repo": "/home/user/myrepo"}` | ✅ string | ❌ expects `owner/repo` |
| `{"repo": "does-not-exist"}` | ✅ string | ❌ repo missing |

These belong in Limitations, not hidden.

---

## Example task with gold annotation

**Task 37**

**Prompt:** "Find open pull requests in repository X, identify the one authored by Y, add a comment."

**Gold chain:**

1. Search/list PRs (`state=open`)
2. Inspect PR metadata (author filter)
3. Create comment

**CYT must preserve:** All three tools + `state` enum + `body` property on comment tool.

---

## Fixture strategy

| Approach | Pros | Cons |
|----------|------|------|
| Live sandbox APIs | Realistic | Flaky, costly |
| Recorded MCP responses | Deterministic | Maintenance |
| Local mock MCP server | Fast, CI-friendly | Less realistic |

**Recommendation:** Local mock MCP for CI; live sandbox for paper numbers.

Consider extending `sdk/e2e/fixtures/bm25_catalog.json` pattern for eval catalogs.

---

## Directory layout (proposed, not yet created)

```
evals/cursor/
├── tasks/           # YAML task definitions
├── fixtures/        # Initial state snapshots
├── assertions/      # Deterministic check functions
├── catalogs/        # Tool catalogs at 10/25/50/100/200/500 sizes
├── verification/    # Labeled malformed-call corpus
└── harness/         # run_task() implementation
```
