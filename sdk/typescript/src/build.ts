import {
  buildCatalogIndex as buildCatalogIndexNative,
  catalogToolCount as catalogToolCountNative,
} from "./native.js";
import {
  collectEnums,
  DECOMPOSED_PREFIX,
  JSON_EXT,
  MD_EXT,
  toolIdFromDecomposedRel,
} from "./paths.js";

export { collectEnums };

export type JsonRecord = Record<string, unknown>;

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
      const content = this.files[relPath] ?? "";
      const filePath = `${catalogPrefix}/${relPath}`;
      const suffix = relPath.slice(relPath.lastIndexOf(".")).toLowerCase();

      if (suffix === MD_EXT) {
        const id = relPath.slice(
          relPath.lastIndexOf("/") + 1,
          relPath.length - MD_EXT.length,
        );
        mdEntries.push({
          id,
          file_path: filePath,
          score: 1.0,
          start_line: 1,
          end_line: 1,
          language: "markdown",
          content,
        });
      } else if (suffix === JSON_EXT) {
        const parsed = JSON.parse(content) as JsonRecord;
        const lineCount = content.split("\n").length;
        const entryId =
          (typeof parsed.id === "string" ? parsed.id : undefined) ??
          toolIdFromDecomposedRel(relPath);
        jsonEntries.push({
          id: entryId,
          name: entryId,
          file_path: filePath,
          score: 1.0,
          start_line: 1,
          end_line: lineCount,
          language: "json",
          content: parsed,
        });
      }
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
