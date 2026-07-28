Feature: Agent harness detection
  cyt-client must infer the correct agent harness from env and payload signals.
  Mirrors ``test_cyt_client_agent.py``.

  Scenario: Cursor version env beats launch agent
    Given the harness environment is cleared
    And environment variable "CURSOR_VERSION" is "3.10.17"
    And environment variable "CYT_LAUNCH_AGENT" is "claude"
    When harness agent is inferred from an empty payload
    Then harness agent should be "cursor"

  Scenario: beforeSubmitPrompt beats launch agent
    Given the harness environment is cleared
    And environment variable "CYT_LAUNCH_AGENT" is "claude"
    When harness agent is inferred from a beforeSubmitPrompt payload
    Then harness agent should be "cursor"

  Scenario: Codex home env selects codex
    Given the harness environment is cleared
    And environment variable "CODEX_HOME" is "/Users/me/.codex"
    When harness agent is inferred from an empty payload
    Then harness agent should be "codex"
