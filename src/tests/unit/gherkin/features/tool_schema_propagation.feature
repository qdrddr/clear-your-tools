Feature: Tool schema propagation contract
  Backend required/optional properties propagate through cyt-mcp pipeline stages.
  Type-2 is the unpruned normalized master catalog for preToolUse gating.
  Type-1 and injection carry tier-scoped schemas for tools that survive pruning.

  Background:
    Given agent cursor

  Scenario: Unpruned Type-2 catalog contains dual-required tool with all backend required properties
    Given an unpruned cyt_mcp master catalog with tool semble_search query string repo string required
    When Type-2 tool_catalog session log is emitted from the master catalog
    Then Type-2 record semble_search should have required properties query repo

  Scenario: Tool absent from pruned subset remains in unpruned Type-2 catalog
    Given an unpruned cyt_mcp master catalog with tools semble_search gitnexus_cypher
    And a pruned subset containing only semble_search
    When Type-2 tool_catalog session log is emitted from the master catalog
    Then Type-2 catalog should include gitnexus_cypher
    And Type-2 catalog tool count should exceed pruned subset count
    When session gate builds Type-1 log from pruned subset only
    Then Type-1 catalog should not include gitnexus_cypher

  Scenario: Survived T2 tool injection exposes required properties only
    Given a dual_schema tool dual_tool_required_optional with tier t2
    When the tool is prepared for tier injection with master catalog merge
    Then injected schema should have required query only without optional limit

  Scenario: Survived T3 tool injection exposes required and optional properties
    Given a dual_schema tool dual_tool_required_optional with tier t3
    When the tool is prepared for tier injection with master catalog merge
    Then injected schema should have required query and optional limit

  Scenario: Type-1 session entry retains required properties for survived tool
    Given an unpruned cyt_mcp master catalog with tool semble_search query string repo string required
    And a pruned semble_search tool with tier t2 partial schema query only
    When session gate builds Type-1 tool log entry with master catalog peers
    Then Type-1 record semble_search should have required properties query repo

  Scenario: Cross-tool required properties are not mixed
    Given master catalog tools semble_search and gitnexus_cypher from full disk fixture
    Then property query must not appear on gitnexus_cypher schema

  Scenario: Single-required tool does not gain spurious required properties
    Given an unpruned cyt_mcp master catalog with tool gitnexus_cypher statement string required
    When Type-2 tool_catalog session log is emitted from the master catalog
    Then Type-2 record gitnexus_cypher should have required properties statement

  Scenario: Degraded partial registry without disk keeps partial schema
    Given a partial workspace registry with query-only semble_search
    When catalog is fetched without disk enrichment
    Then fetched semble_search required should be query only

  Scenario: Backend server and tool identity preserved in Type-2 record
    Given an unpruned cyt_mcp master catalog with tool semble_search query string repo string required
    When Type-2 tool_catalog session log is emitted from the master catalog
    Then Type-2 record semble_search should have server_key semble and tool_name search

  Scenario: Backend server and tool identity preserved in Type-1 record
    Given an unpruned cyt_mcp master catalog with tool semble_search query string repo string required
    And a pruned semble_search tool with tier t2 partial schema query only
    When session gate builds Type-1 tool log entry with master catalog peers
    Then Type-1 record semble_search should have server_key semble and tool_name search

  Scenario: preToolUse validates against Type-2 authority catalog
    Given a Type-2 cyt_mcp catalog with tool semble_search query string repo string required
    When preToolUse validates cyt-mcp tool semble_search with args query bm25 repo /tmp/repo
    Then validation should allow

  Scenario Outline: Tier pipeline preserves backend server_key and tool_name
    Given a reference cyt_mcp tool <tool_ref> from propagation contract
    When tier prep materializes the tool for tier <tier>
    Then tool backend identity should match reference <tool_ref>

    Examples:
      | tool_ref        | tier |
      | semble_search   | t0   |
      | semble_search   | t2   |
      | semble_search   | t4   |
      | gitnexus_cypher | t1   |
      | gitnexus_cypher | t3   |

  Scenario: Runtime cache catalog payload preserves backend identity
    Given a reference cyt_mcp tool semble_search from propagation contract
    When runtime cache catalog payload is built for the tool
    Then tool backend identity should match reference semble_search

  Scenario: Hook cache normalization preserves backend identity
    Given a reference cyt_mcp tool semble_search from propagation contract
    When hook cache normalizes the tool catalog entry
    Then tool backend identity should match reference semble_search

  Scenario Outline: Contract reference tool required properties propagate to Type-2
    Given an unpruned cyt_mcp master catalog from full disk fixture
    When Type-2 tool_catalog session log is emitted from the master catalog
    Then Type-2 record <tool_ref> should have required properties <properties>

    Examples:
      | tool_ref                  | properties   |
      | semble_search             | query repo   |
      | gitnexus_cypher           | statement    |
      | fff_grep                  | query        |
      | jcodemunch_search_symbols | repo query   |
