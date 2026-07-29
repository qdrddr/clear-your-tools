Feature: MCPC hook injection formatting
  MCPC agent-tools XML formatting from tool schemas.
  Mirrors ``test_mcpc_inject.py``.

  Scenario: Flat object schema becomes CLI payload template
    Given a flat object input schema with libraryName and query
    When CLI payload is built from the input schema
    Then CLI payload should equal flat string placeholders

  Scenario: MCPC agent-tools block groups tools by server
    Given a Context7 resolve-library-id MCP tool definition
    When MCPC agent-tools text is formatted for workspace /workspace/repo
    Then formatted text should include MCPC server and CLI sections
    And formatted text should include resolve-library-id CLI example
