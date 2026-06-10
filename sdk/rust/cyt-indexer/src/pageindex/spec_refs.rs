/// Owns line/node/chunk spec strings so `&str` slices can be passed to retrieve APIs safely.
pub struct OwnedSpecRefs {
    lines: Vec<String>,
    nodes: Vec<String>,
    chunks: Vec<String>,
}

impl OwnedSpecRefs {
    #[must_use]
    pub fn new(
        line_num_specs: Option<Vec<String>>,
        node_id_specs: Option<Vec<String>>,
        chunk_id_specs: Option<Vec<String>>,
    ) -> Self {
        Self {
            lines: line_num_specs.unwrap_or_default(),
            nodes: node_id_specs.unwrap_or_default(),
            chunks: chunk_id_specs.unwrap_or_default(),
        }
    }

    #[must_use]
    pub fn line_refs(&self) -> Vec<&str> {
        self.lines.iter().map(String::as_str).collect()
    }

    #[must_use]
    pub fn node_refs(&self) -> Vec<&str> {
        self.nodes.iter().map(String::as_str).collect()
    }

    #[must_use]
    pub fn chunk_refs(&self) -> Vec<&str> {
        self.chunks.iter().map(String::as_str).collect()
    }
}
