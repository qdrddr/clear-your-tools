# cyt-indexer-sdk

TypeScript/Node bindings for the [cyt-indexer](https://crates.io/crates/cyt-indexer) Rust library.

## Development

```bash
cd sdk/typescript
npm install
npm run build
npm test
```

Requires a Rust toolchain (same as the Python SDK maturin flow).

Local `npm run build:native` produces a binding for the current platform only.
Release builds that ship all platforms run in GitHub Actions (`.github/workflows/publish-npm-sdk.yml`).

## Publishing

The npm package `cyt-indexer-sdk` is a **single fat package**: all platform `.node` files are included in one tarball (~30MB).
Only one [trusted publisher](https://docs.npmjs.com/trusted-publishers/) entry is required on npm.

Do not run `napi create-npm-dirs`, `napi artifacts`, or `napi prepublish` for releases;
CI stages every `cyt-indexer-sdk.*.node` into the package root and runs `npm publish` once.

## Usage

```typescript
import {
  anthropicToolsToCatalogEntries,
  buildCatalogFromTools,
  buildCatalogIndex,
  retrieveTools,
} from "cyt-indexer-sdk";
```
