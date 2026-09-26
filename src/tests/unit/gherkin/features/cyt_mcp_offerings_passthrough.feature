Feature: cyt-mcp offerings passthrough
  Prompts, resources, and resource templates must live-proxy to backend MCP servers.
  Regression guard: offerings must never be served from cached snapshots again.

  Scenario: Cold prompts/list proxies to mounted backend offerings
    Given a cyt-mcp aggregator with mounted backend offerings
    When middleware handles prompts/list on a cold start
    Then prompts/list should return backend prompt names gitnexus_review

  Scenario: Cold resources/list proxies to mounted backend offerings
    Given a cyt-mcp aggregator with mounted backend offerings
    When middleware handles resources/list on a cold start
    Then resources/list should return backend resource uri gitnexus://gitnexus/repo/demo

  Scenario: Cold resource templates/list proxies to mounted backend offerings
    Given a cyt-mcp aggregator with mounted backend offerings
    When middleware handles resources/templates/list on a cold start
    Then resource templates/list should return backend template name repo-template

  Scenario: Tools list remains stubbed while offerings passthrough
    Given a cyt-mcp aggregator with mounted backend offerings and populated tool cache
    When middleware handles tools/list
    Then tools/list should serve stub projections including gitnexus_cypher
    And tools/list should not proxy to backend tools/list
