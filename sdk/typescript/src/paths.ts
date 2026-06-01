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
  let rel = relPath;
  if (rel.startsWith(DECOMPOSED_PREFIX)) {
    rel = rel.slice(DECOMPOSED_PREFIX.length);
  }
  const parts = rel.split(/[/\\]/);
  if (parts.length === 0) {
    const stem = rel.replace(/\.[^./\\]+$/, "");
    return stem;
  }
  const first = parts[0] ?? "";
  if (first.endsWith(JSON_EXT)) {
    return first.slice(0, -JSON_EXT.length);
  }
  return first;
}

export function getRootToolKey(filePath: string): string | null {
  const key = toDecomposedKey(filePath);
  if (key === null) {
    return null;
  }
  const rel = key.startsWith(DECOMPOSED_PREFIX)
    ? key.slice(DECOMPOSED_PREFIX.length)
    : key;
  const parts = rel.split(/[/\\]/);
  if (parts.length === 0) {
    return null;
  }
  if (parts.length === 1 && parts[0]?.endsWith(JSON_EXT)) {
    return key;
  }
  const toolId = parts[0];
  return `${DECOMPOSED_PREFIX}${toolId}${JSON_EXT}`;
}

export function collectEnums(schema: unknown): unknown[] {
  const found: unknown[] = [];
  if (schema && typeof schema === "object" && !Array.isArray(schema)) {
    const record = schema as Record<string, unknown>;
    if (Array.isArray(record.enum)) {
      found.push(...record.enum);
    }
    for (const value of Object.values(record)) {
      if (value && typeof value === "object") {
        found.push(...collectEnums(value));
      }
    }
  } else if (Array.isArray(schema)) {
    for (const item of schema) {
      if (item && typeof item === "object") {
        found.push(...collectEnums(item));
      }
    }
  }
  return found;
}
