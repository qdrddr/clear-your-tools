use cyt_indexer::pageindex::next_chunk_id;
use serde_json::json;

#[test]
fn next_chunk_starts_at_zero_without_chunks() {
    let structure = json!([
        {"node_id": 0},
        {"node_id": 4},
    ]);
    assert_eq!(next_chunk_id(&structure), 0);
}

#[test]
fn next_chunk_after_existing_chunks() {
    let structure = json!([
        {"node_id": 4, "chunks": [{"chunk_id": 8}]},
    ]);
    assert_eq!(next_chunk_id(&structure), 9);
}
