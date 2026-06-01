fn main() {
    if std::env::var("CARGO_FEATURE_NODE").is_ok() {
        napi_build::setup();
    }
}
