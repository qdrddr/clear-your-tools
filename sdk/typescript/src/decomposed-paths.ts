export const JSON_EXT = ".json";
export const MD_EXT = ".md";
export const DECOMPOSED_PREFIX = "schemas/decomposed/";

export function toDecomposedKey(filePath: string): string | null {
  const parts = filePath.split(/[/\\]/);
  for (let i = 0; i < parts.length - 1; i += 1) {
    if (parts[i] === "schemas" && parts[i + 1] === "decomposed") {
      return parts.slice(i).join("/");
    }
  }
  return null;
}

export function toolIdFromDecomposedRel(relPath: string): string {
  const rel = relPath.startsWith(DECOMPOSED_PREFIX)
    ? relPath.slice(DECOMPOSED_PREFIX.length)
    : relPath;
  const first = rel.split(/[/\\]/)[0] ?? rel;
  return first.endsWith(JSON_EXT)
    ? first.slice(0, -JSON_EXT.length)
    : first;
}

function rootToolKeyFromDecomposedKey(key: string | null): string | null {
  if (key === null) {
    return null;
  }
  const rel = key.slice(DECOMPOSED_PREFIX.length);
  if (rel.length === 0) {
    return null;
  }
  const slash = rel.search(/[/\\]/);
  if (slash === -1) {
    return key;
  }
  return `${DECOMPOSED_PREFIX}${rel.slice(0, slash)}${JSON_EXT}`;
}

export function getRootToolKey(filePath: string): string | null {
  return rootToolKeyFromDecomposedKey(toDecomposedKey(filePath));
}
