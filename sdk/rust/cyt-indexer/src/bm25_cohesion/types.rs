#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum WindowMode {
    #[default]
    Sentence,
    Word,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum IncludeDelimMode {
    #[default]
    Prev,
    Next,
}

#[derive(Debug, Clone)]
pub struct TextUnit {
    pub text: String,
    pub start_index: usize,
    pub end_index: usize,
    pub token_count: usize,
}

#[derive(Debug, Clone)]
pub struct CohesionChunk {
    pub text: String,
    pub start_index: usize,
    pub end_index: usize,
    pub token_count: usize,
}
