use cyt_indexer::bm25_search::score_corpus;

#[test]
fn scores_nonempty_corpus() -> Result<(), String> {
    let corpus = ["alpha beta gamma", "alpha beta different"];
    let scores = score_corpus("alpha beta", &corpus)?;
    assert_eq!(scores.len(), 2);
    assert!(scores[0] >= scores[1]);
    Ok(())
}
