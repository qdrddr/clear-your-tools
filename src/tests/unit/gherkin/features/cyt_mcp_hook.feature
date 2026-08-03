Feature: cyt-mcp hook integration
  Hook daemon catalog, injection XML, session logs, and disk snapshots.
  Mirrors plan §7 Hook and Hook cache tests.

  Scenario: cyt-mcp source section wraps pruned tools in XML
    Given a cyt-mcp tool definition for filesystem_read_file
    When cyt-mcp source section is formatted
    Then formatted text should include a cyt-mcp XML block
    And formatted text should include the tool name filesystem_read_file

  Scenario: Multi-source agent-tools lists cyt-mcp before legacy sources
    Given cyt-mcp and executor source sections
    When multi-source agent-tools text is assembled
    Then cyt-mcp section should appear before executor section

  Scenario: Session log uses cyt_mcp catalog kind and flat tool key
    Given a cyt-mcp catalog tool filesystem_read_file
    When a session tool log entry is built for cyt_mcp
    Then log entry catalog should be cyt_mcp
    And log entry key should be tool:cyt_mcp:filesystem_read_file
    And log entry should include input_schema

  Scenario: Hook daemon writes and hydrates disk catalog snapshots
    Given a cyt-mcp catalog payload from live fetch
    When the hook daemon applies fetched catalog to disk
    Then disk catalog should contain the same tool names
    And a cold hydrate should load tools into memory

  Scenario: Catalog normalizer preserves cyt-mcp tool names verbatim
    Given a cyt-mcp catalog JSON tool named filesystem_read_file
    When the hook catalog normalizer processes the payload
    Then normalized tool name should remain filesystem_read_file
