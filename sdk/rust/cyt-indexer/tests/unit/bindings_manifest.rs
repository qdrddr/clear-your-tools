use cyt_indexer::bindings::manifest::{CBINDGEN_STUB_SYMBOLS, EXPORTS};

#[test]
fn exports_are_unique() {
    let mut seen = std::collections::HashSet::new();
    for exp in EXPORTS {
        assert!(seen.insert(exp.name), "duplicate export: {}", exp.name);
    }
}

#[test]
fn cbindgen_stubs_listed_in_exports() {
    for name in CBINDGEN_STUB_SYMBOLS {
        assert!(
            EXPORTS.iter().any(|e| e.name == *name),
            "stub symbol missing from EXPORTS: {name}"
        );
    }
}
