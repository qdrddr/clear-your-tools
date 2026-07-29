use cyt_indexer::pageindex::{parse_line_nums, parse_node_ids};

#[test]
fn parse_line_num_variants() {
    assert_eq!(parse_line_nums("5-7"), Ok(vec![5, 6, 7]));
    assert_eq!(parse_line_nums("3,8"), Ok(vec![3, 8]));
    assert_eq!(parse_line_nums("12"), Ok(vec![12]));
}

#[test]
fn parse_node_id_variants() {
    assert_eq!(parse_node_ids("5-7"), Ok(vec![5, 6, 7]));
    assert_eq!(parse_node_ids("3,8"), Ok(vec![3, 8]));
    assert_eq!(parse_node_ids("0012"), Ok(vec![12]));
}
