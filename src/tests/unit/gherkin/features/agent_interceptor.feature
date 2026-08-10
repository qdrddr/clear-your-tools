Feature: Agent skill read interceptor
  Native Read of skill markdown under skill directories can be rewritten to a pruned skinny file.

  Scenario: Read outside skill directories is allowed unchanged
    Given agent interceptor is enabled
    And a preToolUse Read payload for path outside skill dirs
    When cyt-client handles preToolUse
    Then preToolUse permission is allow
    And hook daemon was not called

  Scenario: Read with offset skips intercept
    Given agent interceptor is enabled
    And a preToolUse Read payload for a skill file with offset
    When cyt-client handles preToolUse
    Then preToolUse permission is allow
    And hook daemon was not called

  Scenario: Empty session query allows original read
    Given agent interceptor is enabled
    And a preToolUse Read payload for a skill file under skill dirs
    And session log has no turn entries
    When cyt-client handles preToolUse
    Then preToolUse permission is allow
    And hook daemon was not called

  Scenario: Skill already in session log from prompt is denied
    Given agent interceptor is enabled
    And a preToolUse Read payload for a skill file under skill dirs
    And session log has a prompt-injected skill entry for that file
    When cyt-client handles preToolUse
    Then preToolUse permission is deny

  Scenario: Intercepted read returns updated_input skinny path
    Given agent interceptor is enabled
    And skills are enabled in config
    And a preToolUse Read payload for a skill file under skill dirs
    And session log has a turn with user and assistant text
    And hook daemon returns intercept skinny response
    When cyt-client handles preToolUse
    Then preToolUse permission is allow
    And preToolUse updated_input points to skinny file
    And session log gains a skinny skill entry

  Scenario: Daemon fail-open on HTTP 500
    Given agent interceptor is enabled
    And a preToolUse Read payload for a skill file under skill dirs
    And session log has a turn with user and assistant text
    And hook daemon returns HTTP 500
    When cyt-client handles preToolUse
    Then preToolUse permission is allow
    And preToolUse has no updated_input

  Scenario: Skills disabled daemon returns allow without prune
    Given agent interceptor is enabled
    And skills are disabled in config
    When hook daemon handles preToolUse intercept
    Then intercept response permission is allow
    And intercept response has no updated_input

  Scenario: Full promotion allows original path
    Given agent interceptor is enabled
    And a preToolUse Read payload for a skill file under skill dirs
    And session log has three skinny skill entries for that file
    When cyt-client handles preToolUse
    Then preToolUse permission is allow
    And preToolUse has no updated_input
