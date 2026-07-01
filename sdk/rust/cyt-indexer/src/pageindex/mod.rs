pub mod build;
pub mod chunk_id;
pub mod config;
pub mod decompose;
pub mod index;
pub mod node_id;
pub mod parse;
pub mod reconstruct;
pub mod retrieve;
pub mod skills_repair;
#[cfg(any(feature = "python", feature = "node", feature = "ffi"))]
pub(crate) mod spec_refs;
pub mod tree;
pub mod types;

pub use build::build_skills_index;
pub use chunk_id::{chunk_id_from_value, next_chunk_id, parse_chunk_id_token, parse_chunk_ids};
pub use config::PageIndexConfig;
pub use index::md_to_tree;
pub use node_id::{node_id_from_value, node_id_key, node_id_value, parse_node_id_token};
pub use reconstruct::{
    RETRIEVE_DIR, ReconstructOptions, ReconstructResult, get_content_retrieve_result,
    reconstruct_skill_markdown, retrieve_output_rel_path, write_reconstructed_skill,
};
pub use retrieve::{
    get_document, get_document_structure, get_line_content, get_line_content_from_spec,
    parse_line_nums, parse_node_ids,
};
pub use skills_repair::repair_skill_chunks;
pub use tree::{
    CONTENT_NODE_ID_START, NODE_ID_FRONTMATTER, NODE_ID_PREAMBLE, NODE_KIND_FRONTMATTER,
    NODE_KIND_PREAMBLE, finalize_skill_structure, is_frontmatter_node, is_preamble_node,
};
pub use types::{MdIndexResult, SkillDocument, SkillsIndex, chunk_md_rel};
