/** LLM token counting via tiktoken-rs in the Rust core. */

import {
  configureTokenizerDefaultsNative,
  countJsonTokensNative,
  countTokensBatchNative,
  countTokensNative,
} from "./native.js";

export function countTokens(text: string): number {
  return countTokensNative(text);
}

export function countTokensBatch(texts: string[]): number[] {
  return countTokensBatchNative(texts);
}

export function countJsonTokens(value: unknown): number {
  return countJsonTokensNative(value);
}

export function configureTokenizerDefaults(options?: {
  encoding?: string;
  allowedSpecial?: "all" | "none";
}): void {
  configureTokenizerDefaultsNative(options?.encoding, options?.allowedSpecial);
}
