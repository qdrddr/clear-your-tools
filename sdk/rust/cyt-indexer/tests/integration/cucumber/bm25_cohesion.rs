#![allow(
    clippy::expect_used,
    clippy::needless_pass_by_ref_mut,
    clippy::trivial_regex,
    clippy::unwrap_used
)]

use cucumber::{World, given, then, when};
use cyt_indexer::{Bm25CohesionChunker, Bm25CohesionConfig, CohesionChunk};

#[derive(Debug, Default, World)]
struct Bm25World {
    config: Bm25CohesionConfig,
    text: String,
    chunks: Vec<CohesionChunk>,
    second_run: Vec<CohesionChunk>,
}

fn chunker_for(config: Bm25CohesionConfig) -> Bm25CohesionChunker {
    Bm25CohesionChunker::new(config).expect("chunker config")
}

#[given("default cohesion config")]
fn default_config(world: &mut Bm25World) {
    world.config = Bm25CohesionConfig::default();
}

#[given(expr = "cohesion chunk_size is {int}")]
fn chunk_size(world: &mut Bm25World, size: usize) {
    world.config = Bm25CohesionConfig {
        chunk_size: size,
        ..Default::default()
    };
}

#[given(expr = "input text is {string}")]
fn input_text(world: &mut Bm25World, text: String) {
    world.text = text;
}

#[when("the text is chunked")]
fn chunk_once(world: &mut Bm25World) {
    let chunker = chunker_for(world.config.clone());
    world.chunks = chunker.chunk(&world.text);
}

#[when("the text is chunked twice")]
fn chunk_twice(world: &mut Bm25World) {
    let chunker = chunker_for(world.config.clone());
    world.chunks = chunker.chunk(&world.text);
    world.second_run = chunker.chunk(&world.text);
}

#[then(expr = "there should be {int} chunks")]
fn chunk_count(world: &mut Bm25World, expected: usize) {
    assert_eq!(world.chunks.len(), expected);
}

#[then("there should be 1 chunk equal to the input text")]
fn one_full_chunk(world: &mut Bm25World) {
    assert_eq!(world.chunks.len(), 1);
    assert_eq!(world.chunks[0].text, world.text);
    assert_eq!(world.chunks[0].start_index, 0);
    assert_eq!(world.chunks[0].end_index, world.text.len());
}

#[then("both chunk runs should match")]
fn deterministic_runs(world: &mut Bm25World) {
    assert_eq!(world.chunks.len(), world.second_run.len());
    for (left, right) in world.chunks.iter().zip(world.second_run.iter()) {
        assert_eq!(left.text, right.text);
    }
}

#[tokio::main]
async fn main() {
    Bm25World::run("tests/integration/features/bm25_cohesion.feature").await;
}
