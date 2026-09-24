Feature: cyt-injection.mdc session lifecycle
  Cursor rules file placeholder on session boundaries; pruned tools/skills on prompt.
  Mirrors ``test_cyt_injection_rules_lifecycle.py``.

  Scenario: New session creates lifecycle placeholder when rules file is absent
    Given a Cursor workspace with no cyt-injection rules file
    When cyt-client handles sessionStart for that workspace
    Then the cyt-injection rules file should be a session lifecycle placeholder

  Scenario: Session end resets substantive rules to lifecycle placeholder
    Given a Cursor workspace with substantive cyt-injection rules
    When cyt-client handles sessionEnd for that workspace
    Then the cyt-injection rules file should be a session lifecycle placeholder

  Scenario: User prompt populates rules with pruned MCP tools
    Given a workspace cyt-mcp catalog registered for hook injection
    When cyt-client handles beforeSubmitPrompt with the lifecycle BM25 prompt
    Then the cyt-injection rules file should contain pruned agent-tools
    And the cyt-injection rules file should include expected lifecycle tool names

  Scenario: Full session lifecycle placeholder inject placeholder
    Given a workspace cyt-mcp catalog registered for hook injection
    When cyt-client handles sessionStart for that workspace
    Then the cyt-injection rules file should be a session lifecycle placeholder
    When cyt-client handles beforeSubmitPrompt with the lifecycle BM25 prompt
    Then the cyt-injection rules file should contain pruned agent-tools
    When cyt-client handles sessionEnd for that workspace
    Then the cyt-injection rules file should be a session lifecycle placeholder
