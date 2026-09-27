Feature: cyt-mcp meta tools excluded from tier manager
  Frontend-only get-tool-definitions must never enter hook catalog registration,
  master catalog hydration, or tier tracking for cyt-mcp usr and ws layers.

  Scenario Outline: catalog registration strips meta tools from <layer> layer
    Given a workspace with cyt-mcp <layer> catalog registration including meta tools
    When hook catalog is merged for cyt-mcp injection
    Then merged catalog should include backend tool <backend_tool>
    And merged catalog should exclude get-tool-definitions wire name

  Examples:
    | layer | backend_tool                 |
    | ws    | semble_search                |
    | usr   | context7_resolve-library-id  |

  Scenario: tier tracking filter excludes meta tools from fixture list
    Given cyt-mcp tier capture meta tools fixture
    When tools are filtered for tier tracking with a backend tool present
    Then tier tracking should include only backend tools

  Scenario: tier feedback HTTP ignores get-tool-definitions
    Given a workspace with cyt-mcp tier tracking enabled
    When tier feedback HTTP is posted for get-tool-definitions
    Then tier state should not contain cyt_mcp get-tool-definitions entity

  Scenario: usr and ws union master catalog excludes meta tools
    Given a workspace with usr and ws cyt-mcp catalog layers registered
    When master hook catalog is rebuilt for tier tracking
    Then master catalog should exclude get-tool-definitions and canonical meta name
