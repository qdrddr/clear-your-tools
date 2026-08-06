Feature: Pre-exposure and compaction session corpus

  Background:
    Given agent cursor

  Scenario: preCompact persists compaction entry to agent-home session log
    Given a preCompact hook payload with session id compact-sess
    And inject_via proxy for claude
    When preCompact hook is handled
    Then session log should contain compaction entry
    And session log path should be under agent home

  Scenario: preCompact resets Cursor rules file to placeholder
    Given a Cursor workspace with injected rules file content
    When preCompact hook is handled for that workspace
    Then the Cursor rules file should be a session lifecycle placeholder

  Scenario: cyt_session_log attached to hook payload is post-compaction slice only
    Given a session log with pre and post compaction tool entries
    When hook payload is enriched with cyt_session_log
    Then attached cyt_session_log should exclude pre-compaction tools

  Scenario: Hook tool re-injects after compaction when not in payload
    Given a post-compaction session log without Type-1 demo_tool
    When hook gates demo_tool against post-compaction corpus
    Then hook should not skip demo_tool injection

  Scenario: Hook skill re-injects after compaction without tool-catalog gate
    Given a post-compaction session log without skill demo-skill
    When hook gates demo-skill against post-compaction corpus
    Then hook should not skip demo-skill injection

  Scenario: Proxy payload gate drops verbatim tool in prior user turn
    Given proxy body containing tool fragment for demo_tool
    When proxy payload gate filters demo_tool
    Then proxy should skip demo_tool for payload gate

  Scenario: Proxy session-log gate drops Type-1 tool when entry on disk
    Given proxy session index with Type-1 demo_tool full entry
    When proxy session gate filters demo_tool
    Then proxy should skip demo_tool for session gate

  Scenario: Native proxy tools array omits tool already in session log Type-1
    Given proxy session index with Type-1 native_tool full entry
    When native proxy tools gate filters native_tool
    Then native proxy should omit native_tool

  Scenario: Native proxy tools array omits MCP tool name already in payload corpus
    Given proxy payload corpus containing mcp__demo__run
    When native proxy tools gate filters mcp__demo__run
    Then native proxy should omit mcp__demo__run

  Scenario: Proxy persists Type-1 tool and Type-2 catalog after inject
    Given proxy inject produced tool log entries and catalog entry
    When proxy session log writer persists inject results
    Then session log should contain persisted tool and catalog lines

  Scenario: inject_via proxy skips hook daemon on UserPromptSubmit
    Given inject_via proxy for claude
    And a UserPromptSubmit hook payload
    When non-cursor hook inject is attempted
    Then hook daemon should not be called

  Scenario: Compaction-scoped read_tool_catalog_hashes ignores pre-compaction catalog
    Given a session log with pre and post compaction catalog hashes
    When read_tool_catalog_hashes is called post-compaction
    Then catalog hashes should reflect post-compaction only

  Scenario: hallucination gate configuration remains active
    Given prevent-hallucination hook overlay config
    Then verify-only mode should be enabled for hook agent
