use std::path::PathBuf;

use crate::pageindex::{PageIndexConfig, SkillsIndex, build_skills_index};
use crate::skills_io::write_skills_index;

pub struct SkillsBuilder {
    pub memory_only: bool,
    pub output_dir: Option<PathBuf>,
    index: Option<SkillsIndex>,
}

impl SkillsBuilder {
    #[must_use]
    pub const fn new(memory_only: bool, output_dir: Option<PathBuf>) -> Self {
        Self {
            memory_only,
            output_dir,
            index: None,
        }
    }

    /// Build a skills index from one or more skill directories.
    ///
    /// # Errors
    ///
    /// Returns an error when a directory is missing or a markdown file cannot be read.
    pub fn build_from_dirs(
        &mut self,
        skill_dirs: &[PathBuf],
        config: &PageIndexConfig,
    ) -> Result<&SkillsIndex, String> {
        let index = build_skills_index(skill_dirs, config)?;
        self.index = Some(index);
        self.index
            .as_ref()
            .ok_or_else(|| "internal error: skills index not stored".to_string())
    }

    /// Persist the skills index to disk when not in `memory_only` mode.
    ///
    /// # Errors
    ///
    /// Returns an error when `build_from_dirs` has not been called or files cannot be written.
    pub fn write_catalog(&mut self) -> Result<&SkillsIndex, String> {
        let index = self
            .index
            .as_ref()
            .ok_or_else(|| "call build_from_dirs before write_catalog".to_string())?;
        if !self.memory_only {
            let dir = self
                .output_dir
                .clone()
                .unwrap_or_else(|| PathBuf::from(".catalog"));
            write_skills_index(index, &dir)?;
        }
        Ok(index)
    }

    #[must_use]
    pub const fn index(&self) -> Option<&SkillsIndex> {
        self.index.as_ref()
    }

    #[must_use]
    pub fn to_skills_index_json(&self) -> Option<serde_json::Value> {
        self.index.as_ref().map(SkillsIndex::to_skills_index_json)
    }

    #[must_use]
    pub fn to_skills_dict(&self) -> Option<serde_json::Value> {
        self.index.as_ref().map(SkillsIndex::to_skills_dict)
    }
}

impl Default for SkillsBuilder {
    fn default() -> Self {
        Self::new(true, None)
    }
}
