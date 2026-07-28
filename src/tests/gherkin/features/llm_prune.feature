Feature: LLM pruning integration
  End-to-end selector and hook-daemon paths for tool and skill pruning.
  Mirrors ``test_llm_prune_integration.py``.

  Scenario Outline: Selector and hook daemon prune <mode>
    Given default pruning fixtures are available
    And LLM pruning credentials are configured
    And pruning mode is "<mode>"
    When the LLM selector scenario runs
    And the hook daemon scenario runs
    Then the enriched payload identifies the configured agent
    And the hook daemon reports a successful injection

    Examples:
      | mode     |
      | tools    |
      | skills   |
      | combined |
