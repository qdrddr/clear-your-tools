Feature: cyt-client hook forwarding
  The cyt-client CLI must forward hook payloads and fail silently when appropriate.
  Mirrors ``test_cyt_client.py``.

  Scenario: CLI stays silent when hook server is unavailable
    Given a UserPromptSubmit hook payload
    And no hook server URL can be resolved
    When cyt-client runs without verbose logging
    Then cyt-client stdout should be empty
    And cyt-client stderr should be empty

  Scenario: CLI writes hook response body to stdout
    Given a UserPromptSubmit hook payload
    And the hook server returns injected context
    When cyt-client runs without verbose logging
    Then cyt-client stdout should contain hook injection output

  Scenario: beforeSubmitPrompt reformats Cursor hook output
    Given a beforeSubmitPrompt hook payload with a workspace
    And the hook server returns injected context
    When cyt-client runs without verbose logging
    Then cyt-client stdout should be Cursor beforeSubmitPrompt JSON
    And a Cursor rules file should contain the injected context

  Scenario Outline: session lifecycle resets rules file to frontmatter placeholder
    Given a Cursor workspace with a stale rules file
    And a <event> hook payload for that workspace
    When cyt-client runs for session lifecycle
    Then cyt-client stdout should be Cursor continue JSON
    And the Cursor rules file should be a frontmatter-only placeholder
    And the hook server should not have been called

    Examples:
      | event        |
      | sessionStart |
      | sessionEnd   |
