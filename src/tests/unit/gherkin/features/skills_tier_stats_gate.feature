Feature: Skill tier stats gate when skills.enabled is false
  When skill injection is disabled, tier prompt eval and stats display must hide skills
  while tier_state and stats.db continue accumulating skill signals.

  Scenario: tiers stats JSON omits skills when injection is disabled
    Given skills tier stats fixtures with skills.enabled false
    When cyt tiers stats runs with JSON output
    Then tiers stats JSON should omit skills

  Scenario: tiers stats text omits skills and historical skill lines
    Given skills tier stats fixtures with skills.enabled false
    When cyt tiers stats runs with text output
    Then tiers stats text should omit skills sections

  Scenario: tiers stats --kind skills errors when injection is disabled
    Given skills tier stats fixtures with skills.enabled false
    When cyt tiers stats runs for skills kind
    Then tiers stats should report skills stats unavailable

  Scenario: tier prompt eval is skipped when injection is disabled
    Given skills tier prompt eval is disabled in config
    When tiered skill matches are resolved for a query
    Then skill tier partition and candidate recording should be skipped

  Scenario: tier state still accumulates skill usage when display is disabled
    Given a workspace with skills tier tracking enabled but injection disabled
    When a skill usage event is recorded
    Then tier state should reflect the skill usage
