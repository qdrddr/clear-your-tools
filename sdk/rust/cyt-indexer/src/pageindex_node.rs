use crate::pageindex::{
    build_skills_index, get_content_retrieve_result, get_document, get_document_structure,
    get_line_content, get_line_content_from_spec, md_to_tree, parse_node_ids,
    reconstruct_skill_markdown, spec_refs::OwnedSpecRefs, write_reconstructed_skill, PageIndexConfig,
    ReconstructOptions, SkillDocument, SkillsIndex,
};
use crate::skills_builder::SkillsBuilder;
use crate::skills_io::{
    load_skills_index_from_dir, skills_index_from_decomposed_dir, write_skills_index,
};
use napi::bindgen_prelude::*;
use napi_derive::napi;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;

#[napi(object)]
pub struct ReconstructOptionsNapi {
    pub keep_all_headers: Option<bool>,
}

fn reconstruct_options_from_napi(opts: Option<ReconstructOptionsNapi>) -> ReconstructOptions {
    ReconstructOptions {
        keep_all_headers: opts.and_then(|o| o.keep_all_headers).unwrap_or(false),
    }
}

#[napi(object)]
pub struct PageIndexConfigNapi {
    pub if_add_node_id: Option<bool>,
    pub if_add_node_text: Option<bool>,
}

fn page_index_config_from_napi(config: Option<PageIndexConfigNapi>) -> PageIndexConfig {
    let mut cfg = PageIndexConfig::default();
    if let Some(c) = config {
        if let Some(v) = c.if_add_node_id {
            cfg.if_add_node_id = v;
        }
        if let Some(v) = c.if_add_node_text {
            cfg.if_add_node_text = v;
        }
    }
    cfg
}

#[must_use]
fn skills_index_to_napi(index: &SkillsIndex) -> HashMap<String, Value> {
    let mut out = HashMap::new();
    out.insert("documents".to_string(), index.documents_as_json());
    let mut files = HashMap::new();
    for (k, v) in &index.files {
        files.insert(k.clone(), Value::String(v.clone()));
    }
    out.insert(
        "files".to_string(),
        Value::Object(files.into_iter().collect()),
    );
    out
}

#[must_use]
fn skills_index_from_value(val: &Value) -> SkillsIndex {
    let mut skills = SkillsIndex::default();
    if let Some(docs) = val.get("documents").and_then(|v| v.as_object()) {
        for (doc_id, doc_val) in docs {
            if let Some(doc) = SkillDocument::from_json(doc_val) {
                skills.documents.insert(doc_id.clone(), doc);
            }
        }
    }
    if let Some(files) = val.get("files").and_then(|v| v.as_object()) {
        for (k, v) in files {
            if let Some(s) = v.as_str() {
                skills.files.insert(k.clone(), s.to_string());
            }
        }
    }
    skills
}

/// # Errors
///
/// Returns an error when a skill directory is missing or a markdown file cannot be read.
#[napi]
pub fn build_skills_index_napi(
    skill_dirs: Vec<String>,
    config: Option<PageIndexConfigNapi>,
) -> Result<HashMap<String, Value>> {
    let cfg = page_index_config_from_napi(config);
    let dirs: Vec<PathBuf> = skill_dirs.into_iter().map(PathBuf::from).collect();
    let index = build_skills_index(&dirs, &cfg).map_err(Error::from_reason)?;
    Ok(skills_index_to_napi(&index))
}

/// # Errors
///
/// Returns an error when the index shape is invalid or files cannot be written.
#[napi]
pub fn write_skills_index_napi(index: Value, output_dir: String) -> Result<()> {
    let index = Box::new(index);
    let skills = skills_index_from_value(&index);
    let output_dir = output_dir.into_boxed_str();
    write_skills_index(&skills, PathBuf::from(output_dir.as_ref()).as_path())
        .map_err(Error::from_reason)
}

/// # Errors
///
/// Returns an error when the catalog directory is invalid or files cannot be read.
#[napi]
pub fn load_skills_index_from_dir_napi(catalog_dir: String) -> Result<HashMap<String, Value>> {
    let catalog_dir = catalog_dir.into_boxed_str();
    let index = load_skills_index_from_dir(PathBuf::from(catalog_dir.as_ref()).as_path())
        .map_err(Error::from_reason)?;
    Ok(skills_index_to_napi(&index))
}

/// # Errors
///
/// Returns an error when the decomposed directory is missing or contains no documents.
#[napi]
pub fn skills_index_from_decomposed_dir_napi(dir: String) -> Result<HashMap<String, Value>> {
    let dir = dir.into_boxed_str();
    let index = skills_index_from_decomposed_dir(PathBuf::from(dir.as_ref()).as_path())
        .map_err(Error::from_reason)?;
    Ok(skills_index_to_napi(&index))
}

/// # Errors
///
/// Does not fail; always returns a tree structure for the given markdown.
#[napi]
pub fn md_to_tree_napi(
    markdown_content: String,
    source_path: String,
    config: Option<PageIndexConfigNapi>,
) -> Result<Value> {
    let cfg = page_index_config_from_napi(config);
    let markdown_content = markdown_content.into_boxed_str();
    let source_path = source_path.into_boxed_str();
    let result = md_to_tree(
        markdown_content.as_ref(),
        source_path.as_ref(),
        &cfg,
    );
    Ok(serde_json::json!({
        "doc_name": result.doc_name,
        "line_count": result.line_count,
        "structure": result.structure,
    }))
}

/// # Errors
///
/// Does not fail; returns an empty object when the document id is unknown.
#[napi]
pub fn get_skill_document_napi(documents: Value, doc_id: String) -> Result<Value> {
    let documents = Box::new(documents);
    let doc_id = doc_id.into_boxed_str();
    let docs_obj = documents
        .as_object()
        .ok_or_else(|| Error::from_reason("documents must be object"))?;
    let mut docs = HashMap::new();
    for (k, v) in docs_obj {
        if let Some(doc) = SkillDocument::from_json(v) {
            docs.insert(k.clone(), doc);
        }
    }
    Ok(get_document(&docs, doc_id.as_ref()))
}

/// # Errors
///
/// Does not fail; returns an empty array when the document id is unknown.
#[napi]
pub fn get_skill_structure_napi(documents: Value, doc_id: String) -> Result<Value> {
    let documents = Box::new(documents);
    let doc_id = doc_id.into_boxed_str();
    let docs_obj = documents
        .as_object()
        .ok_or_else(|| Error::from_reason("documents must be object"))?;
    let mut docs = HashMap::new();
    for (k, v) in docs_obj {
        if let Some(doc) = SkillDocument::from_json(v) {
            docs.insert(k.clone(), doc);
        }
    }
    Ok(get_document_structure(&docs, doc_id.as_ref()))
}

/// # Errors
///
/// Does not fail; returns an empty array when line numbers or document id are unknown.
#[napi]
pub fn get_skill_line_content_from_spec_napi(
    index: Value,
    doc_id: String,
    line_num_spec: String,
) -> Result<Value> {
    let index = Box::new(index);
    let doc_id = doc_id.into_boxed_str();
    let line_num_spec = line_num_spec.into_boxed_str();
    let skills = skills_index_from_value(&index);
    Ok(get_line_content_from_spec(
        &skills,
        doc_id.as_ref(),
        line_num_spec.as_ref(),
    ))
}

/// # Errors
///
/// Returns an error when specs are invalid or the document is missing.
#[napi]
pub fn get_skill_content_retrieve_result_napi(
    index: Value,
    doc_id: String,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
    options: Option<ReconstructOptionsNapi>,
) -> Result<Value> {
    let index = Box::new(index);
    let doc_id = doc_id.into_boxed_str();
    let skills = skills_index_from_value(&index);
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs);
    Ok(get_content_retrieve_result(
        &skills,
        doc_id.as_ref(),
        &specs.line_refs(),
        &specs.node_refs(),
        &reconstruct_options_from_napi(options),
    ))
}

/// # Errors
///
/// Returns an error when specs are invalid or the document is missing.
#[napi]
pub fn reconstruct_skill_markdown_napi(
    index: Value,
    doc_id: String,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
    options: Option<ReconstructOptionsNapi>,
) -> Result<Value> {
    let index = Box::new(index);
    let doc_id = doc_id.into_boxed_str();
    let skills = skills_index_from_value(&index);
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs);
    let result = reconstruct_skill_markdown(
        &skills,
        doc_id.as_ref(),
        &specs.line_refs(),
        &specs.node_refs(),
        &reconstruct_options_from_napi(options),
    )
    .map_err(Error::from_reason)?;
    Ok(serde_json::json!({
        "markdown": result.markdown,
        "matched_node_ids": result.matched_node_ids,
        "node_ids": result.node_ids,
        "output_rel_path": result.output_rel_path,
    }))
}

/// # Errors
///
/// Returns an error when reconstruction fails or the file cannot be written.
#[napi]
pub fn write_reconstructed_skill_napi(
    catalog_dir: String,
    index: Value,
    doc_id: String,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
    options: Option<ReconstructOptionsNapi>,
) -> Result<String> {
    let index = Box::new(index);
    let doc_id = doc_id.into_boxed_str();
    let catalog_dir = catalog_dir.into_boxed_str();
    let skills = skills_index_from_value(&index);
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs);
    let output = write_reconstructed_skill(
        PathBuf::from(catalog_dir.as_ref()).as_path(),
        &skills,
        doc_id.as_ref(),
        &specs.line_refs(),
        &specs.node_refs(),
        &reconstruct_options_from_napi(options),
    )
    .map_err(Error::from_reason)?;
    Ok(output.display().to_string())
}

/// # Errors
///
/// Does not fail; returns an empty array when line numbers or document id are unknown.
#[napi]
pub fn get_skill_line_content_napi(
    index: Value,
    doc_id: String,
    line_num_specs: Option<Vec<String>>,
    node_id_specs: Option<Vec<String>>,
) -> Result<Value> {
    let index = Box::new(index);
    let doc_id = doc_id.into_boxed_str();
    let skills = skills_index_from_value(&index);
    let specs = OwnedSpecRefs::new(line_num_specs, node_id_specs);
    Ok(get_line_content(
        &skills,
        doc_id.as_ref(),
        &specs.line_refs(),
        &specs.node_refs(),
    ))
}

/// # Errors
///
/// Returns an error when the node-id spec format is invalid.
#[napi]
pub fn parse_skill_node_ids_napi(spec: String) -> Result<Vec<u32>> {
    let spec = spec.into_boxed_str();
    parse_node_ids(spec.as_ref()).map_err(Error::from_reason)
}

#[napi]
pub struct SkillsBuilderNapi {
    inner: SkillsBuilder,
}

#[napi]
impl SkillsBuilderNapi {
    #[napi(constructor)]
    pub fn new(memory_only: Option<bool>, output_dir: Option<String>) -> Self {
        Self {
            inner: SkillsBuilder::new(
                memory_only.unwrap_or(true),
                output_dir.map(PathBuf::from),
            ),
        }
    }

    /// # Errors
    ///
    /// Returns an error when a skill directory is missing or a markdown file cannot be read.
    #[napi]
    pub fn build_from_dirs(
        &mut self,
        skill_dirs: Vec<String>,
        config: Option<PageIndexConfigNapi>,
    ) -> Result<HashMap<String, Value>> {
        let cfg = page_index_config_from_napi(config);
        let dirs: Vec<PathBuf> = skill_dirs.into_iter().map(PathBuf::from).collect();
        let index = self
            .inner
            .build_from_dirs(&dirs, &cfg)
            .map_err(Error::from_reason)?;
        Ok(skills_index_to_napi(index))
    }

    /// # Errors
    ///
    /// Returns an error when the index has not been built or files cannot be written.
    #[napi]
    pub fn write_catalog(&mut self) -> Result<HashMap<String, Value>> {
        let index = self
            .inner
            .write_catalog()
            .map_err(Error::from_reason)?;
        Ok(skills_index_to_napi(index))
    }

    /// # Errors
    ///
    /// Returns an error when the index has not been built.
    #[napi]
    pub fn to_skills_index_json(&self) -> Result<Value> {
        self.inner
            .to_skills_index_json()
            .ok_or_else(|| Error::from_reason("index not built"))
    }

    /// # Errors
    ///
    /// Returns an error when the index has not been built.
    #[napi]
    pub fn to_skills_dict(&self) -> Result<Value> {
        self.inner
            .to_skills_dict()
            .ok_or_else(|| Error::from_reason("index not built"))
    }
}
