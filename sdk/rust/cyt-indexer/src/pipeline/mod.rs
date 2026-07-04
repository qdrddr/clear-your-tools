//! Composite pipeline APIs (single-call orchestration for Python/C/Go hosts).

mod skills;
mod tools;

pub use skills::{SearchSkillsOptions, build_skill_node_catalog, search_skills_and_select};
pub use tools::{
    PruneBm25Options, PruneRetrieveResult, classify_and_count_catalog,
    prune_catalog_bm25_and_retrieve,
};
