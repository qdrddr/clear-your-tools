Feature: Tools hook injection
  Hook handler tools pruning and stdout injection.
  Mirrors ``test_tools_hook_inject.py``.

  Scenario: Hook skips tools when tools pruning is disabled
    Given tools hook pruning is disabled
    When the tools hook runs with a user prompt payload
    Then hook stdout should be empty
    And hook outcome should be skipped_inject_via_proxy

  Scenario: Hook injects agent-tools block from definitions catalog
    Given a tools hook config with MCP definitions fixture
    And coordinated prune returns one pruned tool
    When the tools hook CLI runs
    Then hook output should include agent-tools context
    And hook output should reference read_file tool
