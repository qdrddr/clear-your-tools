/** LLM token counting via tiktoken-rs in the Rust core. */

import {
  configureTokenizerDefaultsNative,
  countJsonTokensNative,
  countTokensNative,
} from "./native.js";

export function countTokens(text: string): number {
  return countTokensNative(text);
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
