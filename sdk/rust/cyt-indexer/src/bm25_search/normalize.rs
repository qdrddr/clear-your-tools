#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NormalizeMode {
    MinMax,
    ExpSimilarity,
}

#[must_use]
pub fn min_max_normalize(values: &[f64]) -> Vec<f64> {
    if values.is_empty() {
        return Vec::new();
    }
    let min = values.iter().copied().fold(f64::INFINITY, f64::min);
    let max = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if (max - min).abs() < f64::EPSILON {
        return vec![0.5; values.len()];
    }
    values.iter().map(|v| (v - min) / (max - min)).collect()
}

#[must_use]
pub fn exp_similarity(raw: f64) -> f64 {
    if raw <= 0.0 { 0.0 } else { 1.0 - (-raw).exp() }
}

#[must_use]
pub fn normalize_scores(raw: &[f64], mode: NormalizeMode) -> Vec<f64> {
    match mode {
        NormalizeMode::MinMax => min_max_normalize(raw),
        NormalizeMode::ExpSimilarity => raw.iter().map(|&r| exp_similarity(r)).collect(),
    }
}
