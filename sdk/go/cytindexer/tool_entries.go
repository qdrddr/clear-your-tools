package cytindexer

import "unicode/utf8"

func approximateTokenCount(text string) int {
	count := 0
	for _, c := range text {
		if c < 128 {
			count++
		} else {
			count += 2
		}
	}
	return count/2 + 1
}

// TruncateDescription truncates text to at most maxTokens (approximate).
func TruncateDescription(description string, maxTokens int) string {
	if description == "" {
		return ""
	}
	if approximateTokenCount(description) <= maxTokens {
		return description
	}
	suffix := "..."
	suffixTokens := approximateTokenCount(suffix)
	bodyBudget := maxTokens - suffixTokens
	if bodyBudget <= 0 {
		return suffix
	}

	runes := []rune(description)
	lo, hi := 0, len(runes)
	for lo < hi {
		mid := (lo + hi + 1) / 2
		if approximateTokenCount(string(runes[:mid])) <= bodyBudget {
			lo = mid
		} else {
			hi = mid - 1
		}
	}
	body := string(runes[:lo])
	if sp := lastSpace(body); sp > 0 {
		body = body[:sp]
	}
	return body + suffix
}

func lastSpace(s string) int {
	for i := len(s) - 1; i >= 0; i-- {
		if s[i] == ' ' {
			return i
		}
		if s[i] >= utf8.RuneSelf {
			r, size := utf8.DecodeLastRuneInString(s[:i+1])
			if r == ' ' {
				return i - size + 1
			}
		}
	}
	return -1
}

func anthropicInputSchema(tool map[string]any) any {
	if s, ok := tool["input_schema"]; ok {
		return s
	}
	if s, ok := tool["inputSchema"]; ok {
		return s
	}
	if s, ok := tool["parameters"]; ok {
		return s
	}
	return map[string]any{}
}

// IsCatalogToolEntry reports whether tool matches catalog entry shape.
func IsCatalogToolEntry(tool any) bool {
	obj, ok := AsObject(tool)
	if !ok {
		return false
	}
	id, ok := obj["id"].(string)
	_, hasSchema := AsObject(obj["full_schema"])
	return ok && id != "" && hasSchema
}

// PrepareToolEntry builds one catalog entry from tool metadata.
func PrepareToolEntry(serverName, name, description string, inputSchema any) map[string]any {
	fullSchema := map[string]any{
		"id": name, "name": name, "description": description, "inputSchema": inputSchema,
	}
	return map[string]any{
		"id": name, "server": serverName, "tool": name,
		"summary": TruncateDescription(description, 60), "full_schema": fullSchema,
	}
}

// AnthropicToolToCatalogEntry converts one Anthropic API tool to a catalog entry.
func AnthropicToolToCatalogEntry(tool any) (map[string]any, bool) {
	obj, ok := AsObject(tool)
	if !ok {
		return nil, false
	}
	name, ok := obj["name"].(string)
	if !ok || name == "" {
		return nil, false
	}
	description := StrField(obj, "description")
	entry := PrepareToolEntry("", name, description, anthropicInputSchema(obj))
	return entry, true
}

// NormalizeToolsForCatalog normalizes a tool list for indexing.
func NormalizeToolsForCatalog(tools []any) ([]any, []any) {
	entries := make([]any, 0, len(tools))
	var allEnums []any
	for _, tool := range tools {
		var entry any
		if IsCatalogToolEntry(tool) {
			entry = CloneValue(tool)
		} else if e, ok := AnthropicToolToCatalogEntry(tool); ok {
			entry = e
		} else {
			continue
		}
		if obj, ok := AsObject(entry); ok {
			if fs, ok := AsObject(obj["full_schema"]); ok {
				if schema, ok := fs["inputSchema"]; ok {
					allEnums = append(allEnums, CollectEnums(schema)...)
				}
			}
		}
		entries = append(entries, entry)
	}
	return entries, allEnums
}

// BuildCatalogFromTools builds a decomposed catalog index from API tools or catalog entries.
func BuildCatalogFromTools(tools []any) CatalogIndex {
	entries, enums := NormalizeToolsForCatalog(tools)
	return BuildCatalogIndex(entries, enums)
}

// AnthropicToolsToCatalogEntries converts Anthropic API tools to catalog entries.
func AnthropicToolsToCatalogEntries(tools []any) ([]any, []any) {
	var entries []any
	var allEnums []any
	for _, tool := range tools {
		entry, ok := AnthropicToolToCatalogEntry(tool)
		if !ok {
			continue
		}
		if fs, ok := AsObject(entry["full_schema"]); ok {
			if schema, ok := fs["inputSchema"]; ok {
				allEnums = append(allEnums, CollectEnums(schema)...)
			}
		}
		entries = append(entries, entry)
	}
	return entries, allEnums
}
