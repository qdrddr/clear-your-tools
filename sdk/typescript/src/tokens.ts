import {
  compactJsonNative,
  countJsonTokensNative,
  countTokensNative,
} from "./native.js";

export function compactJson(obj: unknown): string {
  return compactJsonNative(obj);
}

export function countTokens(text: string): number {
  return countTokensNative(text);
}

export function countJsonTokens(obj: unknown): number {
  return countJsonTokensNative(obj);
}

export function logTokenUsage(label: string, tokens: number): void {
  const msg = `${label}: ${tokens} tokens`;
  console.log(msg);
}
