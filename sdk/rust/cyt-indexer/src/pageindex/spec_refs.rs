/// Owns line/node spec strings so `&str` slices can be passed to retrieve APIs safely.
pub struct OwnedSpecRefs {
    line_owned: Vec<String>,
    node_owned: Vec<String>,
}

impl OwnedSpecRefs {
    #[must_use]
    pub fn new(line_num_specs: Option<Vec<String>>, node_id_specs: Option<Vec<String>>) -> Self {
        Self {
            line_owned: line_num_specs.unwrap_or_default(),
            node_owned: node_id_specs.unwrap_or_default(),
        }
    }

    #[must_use]
    pub fn line_refs(&self) -> Vec<&str> {
        self.line_owned.iter().map(String::as_str).collect()
    }

    #[must_use]
    pub fn node_refs(&self) -> Vec<&str> {
        self.node_owned.iter().map(String::as_str).collect()
    }
}
