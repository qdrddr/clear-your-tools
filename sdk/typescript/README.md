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

## Usage

```typescript
import {
  buildCatalogIndex,
  CatalogIndex,
  countTokens,
} from "cyt-indexer-sdk";
```
