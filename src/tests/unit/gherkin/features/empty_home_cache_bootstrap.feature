Feature: Empty cyt home cache bootstrap
  Guard inject preview and master catalog when ~/.config/cyt starts empty.

  Scenario: inject preview fails on empty home without catalog push
    Given an empty cyt home cache for the workspace
    When cyt inject preview runs for the BM25 bootstrap prompt
    Then inject preview should fail with disk cache miss

  Scenario: inject preview succeeds after cyt-mcp populates cache
    Given an empty cyt home cache for the workspace
    And a workspace cyt-mcp catalog is pushed to the hook daemon and disk
    When cyt inject preview runs for the BM25 bootstrap prompt
    Then inject preview should include expected cyt-mcp tool names
    And inject preview stderr should not mention empty master hook catalog

  Scenario: daemon warm hydrates master catalog after cyt-mcp push
    Given an empty cyt home cache for the workspace
    And a workspace cyt-mcp catalog is pushed to the hook daemon and disk
    And in-memory hook catalog caches were cleared like a fresh CLI process
    When daemon warm caches run for the workspace
    Then master catalog tool count should meet the bootstrap scenario minimum
