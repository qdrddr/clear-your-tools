Feature: Session log cyt_mcp backend identity contract
  Type-1 and Type-2 JSONL records must carry deterministic server_key and tool_name.
  Writers fail hard when backend identity is missing; resolution trusts explicit fields.

  Background:
    Given agent cursor

  Scenario Outline: Session log writers stamp server_key and tool_name on cyt_mcp records
    Given a reference cyt_mcp tool <tool_ref> from propagation contract
    When session log identity writer <writer> emits a record for the tool
    Then emitted record should preserve reference identity for <tool_ref>

    Examples:
      | tool_ref          | writer       |
      | semble_search     | hook_type1   |
      | semble_search     | hook_type2   |
      | fff_grep          | hook_type1   |
      | gitnexus_cypher   | hook_type2   |
      | semble_search     | client_type1 |
      | semble_search     | client_type2 |

  Scenario Outline: Session log writers reject cyt_mcp tools missing backend identity
    Given a reference cyt_mcp tool <tool_ref> from propagation contract without backend identity
    When session log identity writer <writer> emits a record for the tool
    Then build should fail with missing identity error

    Examples:
      | tool_ref      | writer       |
      | semble_search | hook_type1   |
      | semble_search | hook_type2   |
      | semble_search | client_type1 |

  Scenario: Type-1 log round-trip restores backend identity fields
    Given a reference cyt_mcp tool fff_grep from propagation contract
    When hook Type-1 session log entry is built and converted back to tool dict
    Then restored tool dict should preserve reference identity for fff_grep

  Scenario: Client session capture enriches wire-only definition into identity triple
    When client session capture builds Type-2 record for codebase-memory-mcp_search_graph
    Then Type-2 record codebase-memory-mcp_search_graph should have server_key codebase-memory-mcp and tool_name search_graph

  Scenario: Explicit catalog identity wins over wire-name split heuristic
    Given a cyt_mcp catalog tool with explicit server_key search and tool_name graph for wire codebase-memory_search_graph
    When backend identity is resolved for post-tool capture
    Then resolved identity should be server_key search and tool_name graph
