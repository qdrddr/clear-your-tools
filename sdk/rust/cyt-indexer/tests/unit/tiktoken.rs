use cyt_indexer::tiktoken::{count_tokens, truncate_description};

#[test]
fn count_hello_world() -> Result<(), String> {
    let n = count_tokens("hello world")?;
    assert!((1..=4).contains(&n));
    Ok(())
}

#[test]
fn truncate_respects_budget() -> Result<(), String> {
    let long = "word ".repeat(200);
    let out = truncate_description(&long, 10)?;
    assert!(count_tokens(&out)? <= 10);
    Ok(())
}
