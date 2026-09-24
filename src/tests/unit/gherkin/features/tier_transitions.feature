Feature: Tier manager slow epoch and fast hot transitions
  Tools and skills move between tiers via the slow clock at epoch boundaries
  and via the fast/hot path for temporary jumps into the next tier.

  Scenario: Slow clock demotes unused tool at epoch boundary
    Given tier transition fixtures for slow tool demotion
    When the tier manager runs an expired epoch for tools
    Then the tool stable tier should be COLD
    And the epoch log should include slow_demote_t2_t1_unused

  Scenario: Slow clock promotes tool at epoch boundary
    Given tier transition fixtures for slow tool promotion
    When the tier manager runs an expired epoch for tools
    Then the tool stable tier should be ACTIVE
    And the epoch log should include slow_promote_t1_t2

  Scenario: Slow clock promotes skill at epoch boundary
    Given tier transition fixtures for slow skill promotion
    When the tier manager runs an expired epoch for skills
    Then the skill stable tier should be ACTIVE
    And the epoch log should include slow_promote_t1_t2

  Scenario: Slow clock demotes unused skill at epoch boundary
    Given tier transition fixtures for slow skill demotion
    When the tier manager runs an expired epoch for skills
    Then the skill stable tier should be COLD
    And the epoch log should include slow_demote_t2_t1_unused

  Scenario: Fast hot path temporarily jumps tool to HOT on use
    Given tier transition fixtures for fast hot tool jump
    When the tier manager records a successful tool use
    Then the tool effective tier should be HOT
    And the tool stable tier should remain COLD
    And the tool should have a temporary promotion expiry

  Scenario: Fast hot path temporarily jumps skill to HOT on use
    Given tier transition fixtures for fast hot skill jump
    When the tier manager records a successful skill use
    Then the skill effective tier should be HOT
    And the skill stable tier should remain COLD
    And the skill should have a temporary promotion expiry

  Scenario: Epoch crystallizes successful fast hot tool promotion
    Given tier transition fixtures for fast hot epoch crystallize
    When the tier manager records a successful tool use
    And the tier manager runs an expired epoch for tools
    Then the tool stable tier should be HOT
    And the tool temporary promotion should be cleared

  Scenario: Epoch crystallizes successful fast hot skill promotion
    Given tier transition fixtures for fast hot skill epoch crystallize
    When the tier manager records a successful skill use
    And the tier manager runs an expired epoch for skills
    Then the skill stable tier should be HOT
    And the skill temporary promotion should be cleared

  Scenario: Fast wake promotes dormant tool from last user prompt
    Given tier transition fixtures for fast wake tool from prompt
    When the tier manager processes the last user prompt in the background for tools
    Then the tool stable tier should be COLD
    And the entity should have a wake lease from fast promotion

  Scenario: Fast wake promotes dormant skill from last user prompt
    Given tier transition fixtures for fast wake skill from prompt
    When the tier manager processes the last user prompt in the background for skills
    Then the skill stable tier should be COLD
    And the entity should have a wake lease from fast promotion

  Scenario: Fast wake promotes all dormant tools on the same MCP server
    Given tier transition fixtures for fast wake MCP server batch
    When the tier manager processes the last user prompt in the background for tools
    Then every dormant tool on the MCP server should be COLD
    And every MCP server tool should have a wake lease from fast promotion
