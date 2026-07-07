import {
  configureRuntimeDefaultsNative,
  decomposedScoreNative,
  emptyOptionalFallbackKNative,
  enumScoreNative,
  rerankScoreNative,
  runtimeDefaultMcpPolicyNative,
  runtimeDefaultSystemPolicyNative,
} from "./native.js";

export interface RuntimeDefaultsConfig {
  decomposedScore: number;
  enumScore: number;
  rerankScore: number;
  emptyOptionalFallbackK: number;
  defaultSystemPolicy: string;
  defaultMcpPolicy: string;
}

/** Push host overrides into native RuntimeConfig (Rust defaults when not called). */
export function configureRuntimeDefaults(opts: RuntimeDefaultsConfig): void {
  configureRuntimeDefaultsNative(
    opts.decomposedScore,
    opts.enumScore,
    opts.rerankScore,
    opts.emptyOptionalFallbackK,
    opts.defaultSystemPolicy,
    opts.defaultMcpPolicy,
  );
}

export function decomposedScore(): number {
  return decomposedScoreNative();
}

export function enumScore(): number {
  return enumScoreNative();
}

export function rerankScore(): number {
  return rerankScoreNative();
}

export function emptyOptionalFallbackK(): number {
  return emptyOptionalFallbackKNative();
}

export function defaultSystemPolicy(): string {
  return runtimeDefaultSystemPolicyNative();
}

export function defaultMcpPolicy(): string {
  return runtimeDefaultMcpPolicyNative();
}
