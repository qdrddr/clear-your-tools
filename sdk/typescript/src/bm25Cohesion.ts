/** BM25 lexical cohesion chunker (standalone, no pageindex). */

import { bm25CohesionChunkNative } from "./native.js";

export interface Bm25CohesionConfig {
  windowMode?: "sentence" | "word";
  threshold?: number;
  mergeThreshold?: number;
  chunkSize?: number;
  tokenCounter?: "approximate" | "character" | "tiktoken";
  similarityWindow?: number;
  nextUnitSize?: number;
  skipWindow?: number;
  minUnitsPerChunk?: number;
  minimumWords?: number;
  minimumSentences?: number;
  minCharactersPerSentence?: number;
  minCharactersPerWord?: number;
  delimiters?: string[];
  includeDelim?: "prev" | "next";
  useStopwords?: boolean;
  filterWindow?: number;
  filterPolyorder?: number;
  filterTolerance?: number;
  stemLanguage?: string;
}

export interface CohesionChunk {
  text: string;
  start_index: number;
  end_index: number;
  token_count: number;
}

export function defaultBm25CohesionConfig(): Bm25CohesionConfig {
  return {
    windowMode: "sentence",
    threshold: 0.8,
    mergeThreshold: 0.7,
    chunkSize: 2048,
    tokenCounter: "tiktoken",
    similarityWindow: 3,
    nextUnitSize: 1,
    skipWindow: 0,
    minUnitsPerChunk: 1,
    minimumWords: 10,
    minimumSentences: 1,
    minCharactersPerSentence: 24,
    minCharactersPerWord: 2,
    delimiters: [". ", "! ", "? ", "\n"],
    includeDelim: "prev",
    useStopwords: true,
    filterWindow: 5,
    filterPolyorder: 3,
    filterTolerance: 0.2,
    stemLanguage: "english",
  };
}

/** Map camelCase SDK config to snake_case keys for the native bridge. */
export function cohesionConfigToNative(
  config?: Partial<Bm25CohesionConfig>,
): Record<string, unknown> | undefined {
  if (!config) return undefined;
  const out: Record<string, unknown> = {};
  if (config.windowMode !== undefined) out.window_mode = config.windowMode;
  if (config.threshold !== undefined) out.threshold = config.threshold;
  if (config.mergeThreshold !== undefined)
    out.merge_threshold = config.mergeThreshold;
  if (config.chunkSize !== undefined) out.chunk_size = config.chunkSize;
  if (config.tokenCounter !== undefined)
    out.token_counter = config.tokenCounter;
  if (config.similarityWindow !== undefined)
    out.similarity_window = config.similarityWindow;
  if (config.nextUnitSize !== undefined)
    out.next_unit_size = config.nextUnitSize;
  if (config.skipWindow !== undefined) out.skip_window = config.skipWindow;
  if (config.minUnitsPerChunk !== undefined)
    out.min_units_per_chunk = config.minUnitsPerChunk;
  if (config.minimumWords !== undefined)
    out.minimum_words = config.minimumWords;
  if (config.minimumSentences !== undefined)
    out.minimum_sentences = config.minimumSentences;
  if (config.minCharactersPerSentence !== undefined) {
    out.min_characters_per_sentence = config.minCharactersPerSentence;
  }
  if (config.minCharactersPerWord !== undefined) {
    out.min_characters_per_word = config.minCharactersPerWord;
  }
  if (config.delimiters !== undefined) out.delimiters = config.delimiters;
  if (config.includeDelim !== undefined)
    out.include_delim = config.includeDelim;
  if (config.useStopwords !== undefined)
    out.use_stopwords = config.useStopwords;
  if (config.filterWindow !== undefined)
    out.filter_window = config.filterWindow;
  if (config.filterPolyorder !== undefined)
    out.filter_polyorder = config.filterPolyorder;
  if (config.filterTolerance !== undefined)
    out.filter_tolerance = config.filterTolerance;
  if (config.stemLanguage !== undefined)
    out.stem_language = config.stemLanguage;
  return out;
}

export function bm25CohesionChunk(
  text: string,
  config?: Partial<Bm25CohesionConfig>,
): CohesionChunk[] {
  return bm25CohesionChunkNative(
    text,
    cohesionConfigToNative(config),
  ) as CohesionChunk[];
}
