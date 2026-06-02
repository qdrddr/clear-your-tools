import { appendDecomposedCatalogEntry } from "./catalog-json.js";
import { buildCatalogIndexNative, catalogToolCountNative } from "./native.js";
import { DECOMPOSED_PREFIX } from "./decomposed-paths.js";
import type { JsonRecord } from "./types.js";

export { collectSchemaEnums as collectEnums } from "./catalog-json.js";

export function catalogToolCount(data: JsonRecord): number {
  return catalogToolCountNative(data);
}

export class CatalogIndex {
  constructor(
    public readonly tools: JsonRecord[],
    public readonly files: Record<string, string> = {},
  ) {}

  toCatalogDict(catalogPrefix = "src/catalog"): {
    md: JsonRecord[];
    json: JsonRecord[];
    tools: JsonRecord[];
  } {
    const mdEntries: JsonRecord[] = [];
    const jsonEntries: JsonRecord[] = [];

    for (const relPath of Object.keys(this.files).sort()) {
      if (!relPath.startsWith(DECOMPOSED_PREFIX)) {
        continue;
      }
      appendDecomposedCatalogEntry(
        relPath,
        this.files[relPath] ?? "",
        catalogPrefix,
        mdEntries,
        jsonEntries,
      );
    }

    return { md: mdEntries, json: jsonEntries, tools: this.tools };
  }
}

export function buildCatalogIndex(
  tools: JsonRecord[],
  allEnums: unknown[],
): CatalogIndex {
  const raw = buildCatalogIndexNative(tools, allEnums);
  return new CatalogIndex([...raw.tools], { ...raw.files });
}
