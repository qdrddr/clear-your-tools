Feature: Rules refresh and pre-exposure lifecycle compatibility

  Background:
    Given a Cursor workspace with lifecycle placeholder rules

  Scenario: First prompt after daemon restart forces rules refresh
    Given a new session with no injection log
    When the client resolves rules refresh for beforeSubmitPrompt
    Then cyt_force_rules_refresh should be true
    And hook pre-exposure bypass should be allowed
    When the hook runs first-prompt injection for demo_tool
    Then hook should inject demo_tool without pre-exposure skip
    And the session log should contain a demo_tool entry

  Scenario: Placeholder after pre-exposure skip does not force rules refresh
    Given a session log with demo_tool and three completed assistant turns
    When the client resolves rules refresh for beforeSubmitPrompt
    Then cyt_force_rules_refresh should be false
    And hook pre-exposure bypass should be blocked
    When the hook runs follow-up injection for demo_tool
    Then hook should skip demo_tool injection

  Scenario: Five repeated prompts avoid rules-refresh flapping
    Given a BM25 locate prompt for demo_tool
    When five beforeSubmitPrompt cycles run through client and hook
    Then force refresh should only be true on the first iteration
    And iterations four and five should skip injection without re-flapping

  Scenario: sessionStart does not wipe substantive rules synced on first prompt
    Given substantive rules synced before sessionStart
    When sessionStart lifecycle sync runs
    Then the Cursor rules file should retain substantive injection
