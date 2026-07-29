use cyt_indexer::bm25_search::{ScoreCatalogOptions, score_catalog_in_place};
use serde_json::json;

#[test]
fn scores_catalog_items() -> Result<(), String> {
    let mut data = json!({
        "json": [
            {"content": "alpha beta tool", "score": "0"},
            {"content": "unrelated finance news", "score": "0"}
        ],
        "md": []
    });
    score_catalog_in_place(&mut data, "alpha beta", &ScoreCatalogOptions::default())?;
    let first = data["json"][0]["score"]
        .as_str()
        .ok_or_else(|| "missing first score".to_string())?;
    let second = data["json"][1]["score"]
        .as_str()
        .ok_or_else(|| "missing second score".to_string())?;
    let first_score = first.parse::<f64>().map_err(|err| err.to_string())?;
    let second_score = second.parse::<f64>().map_err(|err| err.to_string())?;
    assert!(first_score > second_score);
    Ok(())
}
