Feature: Tool tier stats gate when tools.enabled is false
  When tool injection is disabled, tier prompt eval and stats display must hide tools
  while tier_state and stats.db continue accumulating tool signals.

  Scenario: tiers stats JSON omits tools when injection is disabled
    Given tools tier stats fixtures with tools.enabled false
    When cyt tiers stats runs with JSON output
    Then tiers stats JSON should omit tools

  Scenario: tiers stats text omits tools and historical tool lines
    Given tools tier stats fixtures with tools.enabled false
    When cyt tiers stats runs with text output
    Then tiers stats text should omit tools sections

  Scenario: tiers stats --kind tools errors when injection is disabled
    Given tools tier stats fixtures with tools.enabled false
    When cyt tiers stats runs for tools kind
    Then tiers stats should report tools stats unavailable

  Scenario: tiers stats --server errors when injection is disabled
    Given tools tier stats fixtures with tools.enabled false
    When cyt tiers stats runs for server filter
    Then tiers stats should report tools stats unavailable

  Scenario: tier prompt eval is skipped when injection is disabled
    Given tools tier prompt eval is disabled in config
    When tier prune context is prepared for a query
    Then tool tier apply and candidate recording should be skipped

  Scenario: tier state still accumulates tool usage when display is disabled
    Given a workspace with tools tier tracking enabled but injection disabled
    When a tool usage event is recorded
    Then tier state should reflect the tool usage
