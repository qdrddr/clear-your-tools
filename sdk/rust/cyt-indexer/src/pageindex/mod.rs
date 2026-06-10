pub mod build;
pub mod config;
pub mod decompose;
pub mod index;
pub mod node_id;
pub mod parse;
pub mod reconstruct;
pub mod retrieve;
#[cfg(any(feature = "python", feature = "node"))]
pub(crate) mod spec_refs;
pub mod tree;
pub mod types;

pub use build::build_skills_index;
pub use config::PageIndexConfig;
pub use index::md_to_tree;
pub use reconstruct::{
    get_content_retrieve_result, reconstruct_skill_markdown, retrieve_output_rel_path,
    write_reconstructed_skill, ReconstructOptions, ReconstructResult, RETRIEVE_DIR,
};
pub use node_id::{node_id_from_value, node_id_key, node_id_value, parse_node_id_token};
pub use retrieve::{
    get_document, get_document_structure, get_line_content, get_line_content_from_spec,
    parse_line_nums, parse_node_ids,
};
pub use tree::{
    finalize_skill_structure, is_frontmatter_node, is_preamble_node, CONTENT_NODE_ID_START,
    NODE_ID_FRONTMATTER, NODE_ID_PREAMBLE, NODE_KIND_FRONTMATTER, NODE_KIND_PREAMBLE,
};
pub use types::{MdIndexResult, SkillDocument, SkillsIndex};
