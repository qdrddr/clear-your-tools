use cyt_indexer::analyzer::analyze_text;

#[test]
fn english_stopwords_removed() {
    let tokens = analyze_text("the fox is crafty");
    assert!(tokens.iter().any(|t| t.starts_with("fox") || t == "fox"));
    assert!(!tokens.contains(&"the".to_string()));
}
