use crate::build::{build_catalog_index, CatalogIndex};
use crate::catalog_io::write_catalog_index;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;

pub struct CatalogBuilder {
    pub tools: Vec<Value>,
    pub all_enums: Vec<Value>,
    index: Option<CatalogIndex>,
    lookup: HashMap<(String, String), usize>,
    pub memory_only: bool,
    pub output_dir: Option<PathBuf>,
}

impl CatalogBuilder {
    pub fn new_with_options(
        memory_only: Option<bool>,
        output_dir: Option<PathBuf>,
    ) -> Self {
        let cfg = crate::paths::snapshot();
        Self {
            tools: Vec::new(),
            all_enums: Vec::new(),
            index: None,
            lookup: HashMap::new(),
            memory_only: memory_only.unwrap_or(cfg.builder_memory_only),
            output_dir,
        }
    }

    pub fn new(memory_only: bool, output_dir: Option<PathBuf>) -> Self {
        Self::new_with_options(Some(memory_only), output_dir)
    }

    pub fn add_tool(&mut self, entry: Value) {
        let server = entry
            .get("server")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let tool = entry
            .get("tool")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let idx = self.tools.len();
        self.lookup.insert((server, tool), idx);
        if let Some(schema) = entry
            .get("full_schema")
            .and_then(|fs| fs.get("inputSchema"))
        {
            self.all_enums
                .extend(crate::paths::collect_enums(schema));
        }
        self.tools.push(entry);
        self.index = None;
    }

    pub fn get_tool_info(&self, server_name: &str, tool_name: &str) -> Option<&Value> {
        let key = (server_name.to_string(), tool_name.to_string());
        self.lookup.get(&key).and_then(|&i| self.tools.get(i))
    }

    pub fn build_index(&mut self) -> &CatalogIndex {
        if self.index.is_none() {
            self.index = Some(build_catalog_index(&self.tools, &self.all_enums));
        }
        self.index.as_ref().unwrap()
    }

    pub fn write_catalog(&mut self) -> Result<&CatalogIndex, String> {
        let index = self.build_index().clone();
        if !self.memory_only {
            let default_dir = crate::paths::default_catalog_dir();
            let dir = self.output_dir.as_deref().unwrap_or(&default_dir);
            write_catalog_index(&index, dir, crate::paths::write_catalog_prune())?;
        }
        Ok(self.index.as_ref().unwrap())
    }

    pub fn to_catalog_dict(&mut self) -> Value {
        self.build_index().to_catalog_dict()
    }

    pub fn to_catalog_dict_with_prefix(&mut self, catalog_prefix: &str) -> Value {
        self.build_index().to_catalog_dict_with_prefix(catalog_prefix)
    }
}

impl Default for CatalogBuilder {
    fn default() -> Self {
        Self::new_with_options(None, None)
    }
}
