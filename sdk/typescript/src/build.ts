import {
  buildCatalogIndexNative,
  catalogIndexToCatalogDictNative,
  catalogToolCountNative,
} from "./native.js";
import { collectEnums } from "./paths.js";
import type { JsonRecord } from "./types.js";

export { collectEnums };

export function catalogToolCount(data: JsonRecord): number {
  return catalogToolCountNative(data);
}

export class CatalogIndex {
  constructor(
    public readonly tools: JsonRecord[],
    public readonly files: Record<string, string> = {},
  ) {}

  toCatalogDict(catalogPrefix?: string): {
    md: JsonRecord[];
    json: JsonRecord[];
    tools: JsonRecord[];
  } {
    return catalogIndexToCatalogDictNative(
      { tools: this.tools, files: this.files },
      catalogPrefix,
    ) as {
      md: JsonRecord[];
      json: JsonRecord[];
      tools: JsonRecord[];
    };
  }
}

export function buildCatalogIndex(
  tools: JsonRecord[],
  allEnums: unknown[],
): CatalogIndex {
  const raw = buildCatalogIndexNative(tools, allEnums);
  return new CatalogIndex([...raw.tools], { ...raw.files });
}
