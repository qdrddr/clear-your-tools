Feature: Tool example project isolation
  Captures and injected examples must stay scoped to the active git project,
  even when multiple repos share one tool_examples.db (dual-window regression).

  Scenario: Workspace-scoped tool examples do not leak across projects
    Given two git projects sharing one tool examples database
    And repo A has a recorded workspace-scoped fff_grep capture
    When repo B enriches fff_grep for a workspace search prompt
    Then repo B should have no injected examples
    And repo B examples must not reference repo A

  Scenario: User-scoped tool examples do not leak across projects
    Given two git projects sharing one tool examples database
    And repo A has a recorded user-scoped context7_query-docs capture
    When repo B enriches context7_query-docs for a documentation prompt
    Then repo B should have no injected examples
    And repo B examples must not reference repo A
