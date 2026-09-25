Feature: Cache layout consolidation
  Guard consolidated ~/.config/cyt/cache paths, flat on-disk layout, and inject preview diagnostics.

  Scenario: decomposed tool cache uses flat content hash directory
    Given an isolated cache layout workspace
    When a tool catalog is built to disk cache
    Then tool cache entry should be under cache tools without entries subdir

  Scenario: skills registry uses flat content hash directory
    Given an isolated cache layout workspace
    When a skills registry is built to disk cache
    Then skills cache entry should be under cache skills without entries subdir

  Scenario: legacy registry snapshot is compacted on load
    Given a legacy registry snapshot without catalog layer
    When catalog registry loads from disk
    Then registry snapshot file should contain no legacy entries

  Scenario: inject preview verbose reports phase timing
    Given a workspace with cyt-mcp disk catalog seeded
    When cyt inject preview runs with verbose flag
    Then preview stderr should include phase timing total

  Scenario: cache clear removes consolidated tools directory
    Given an isolated cache layout workspace with seeded tool cache
    When cyt cache clear runs for tools
    Then consolidated tools cache directory should be absent
