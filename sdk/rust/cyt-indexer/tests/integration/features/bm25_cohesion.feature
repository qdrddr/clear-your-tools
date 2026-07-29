Feature: BM25 cohesion chunking
  Public chunker invariants mirrored from tests/unit/bm25_cohesion.rs.

  Scenario: chunk_size zero returns a single full-text chunk
    Given cohesion chunk_size is 0
    And input text is "Alpha one two three. Beta finance market stocks."
    When the text is chunked
    Then there should be 1 chunk equal to the input text

  Scenario: empty whitespace input yields no chunks
    Given default cohesion config
    And input text is "   "
    When the text is chunked
    Then there should be 0 chunks

  Scenario: chunking is deterministic
    Given cohesion chunk_size is 50
    And input text is "First topic sentence here. Second topic follows now. Third unrelated finance news. Fourth finance detail here."
    When the text is chunked twice
    Then both chunk runs should match
