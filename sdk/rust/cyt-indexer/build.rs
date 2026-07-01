fn main() -> Result<(), Box<dyn std::error::Error>> {
    if std::env::var("CARGO_FEATURE_NODE").is_ok() {
        napi_build::setup();
    }
    if std::env::var("CARGO_FEATURE_FFI").is_ok() {
        let crate_dir = std::env::var("CARGO_MANIFEST_DIR")?;
        let output_file = std::path::PathBuf::from(&crate_dir).join("cyt_indexer.h");
        let config = cbindgen::Config::from_file("cbindgen.toml")?;
        cbindgen::Builder::new()
            .with_crate(&crate_dir)
            .with_config(config)
            .generate()?
            .write_to_file(&output_file);
        println!("cargo:rerun-if-changed=src/ffi/");
        println!("cargo:rerun-if-changed=cbindgen.toml");
    }
    Ok(())
}
