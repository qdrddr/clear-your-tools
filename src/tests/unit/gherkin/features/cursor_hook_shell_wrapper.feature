Feature: Cursor hook shell wrappers for fish and bash compatibility
  Cursor runs hooks with the user's login shell (often fish or bash). Inline
  CYT_WORKSPACE=${workspaceFolder} prefixes are bash-only and fail under fish.
  cyt hook cursor installs shell wrapper scripts instead.

  Scenario: Cursor hook install writes fish-safe shell wrapper commands
    Given cyt hook development mode for the current repo
    When cursor hook entries are built with shell wrappers
    Then cursor hook commands should use shell wrapper scripts
    And cursor hook commands should not use fish-breaking inline env prefixes

  Scenario: Legacy fish-breaking inline hooks are upgraded to shell wrappers
    Given cyt hook development mode for the current repo
    And cursor hooks.json contains legacy fish-breaking inline commands
    When cursor hooks are upserted for development mode
    Then cursor hooks.json should reference shell wrapper scripts
    And cursor hooks.json should not contain fish-breaking inline env prefixes

  Scenario: Shell wrapper resolves workspace and runs cyt-client under fish
    Given cyt hook development mode for the current repo
    And a Cursor workspace with stale rules injection
    When the cyt-client shell wrapper runs from fish
    Then cyt-client should continue successfully
    And the Cursor rules file should reset to the lifecycle placeholder

  Scenario: Production hook install replaces development wrapper scripts
    Given cursor hooks.json references development shell wrappers
    When cursor hooks are upserted switching to production mode
    Then cursor hooks.json should reference production shell wrapper scripts
    And development shell wrapper scripts should be removed from disk

  Scenario: Development hook install replaces production wrapper scripts
    Given cursor hooks.json references production shell wrappers
    When cursor hooks are upserted switching to development mode
    Then cursor hooks.json should reference development shell wrapper scripts
    And production shell wrapper scripts should be removed from disk

  Scenario: Skipping hook update does not mutate wrappers when switching modes
    Given cursor hooks.json references development shell wrappers
    When cursor hook install is skipped switching to production mode
    Then cursor hooks.json should still reference development shell wrapper scripts
    And development shell wrapper scripts should remain on disk
    And production shell wrapper scripts should not exist on disk
