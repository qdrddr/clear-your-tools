package cytindexer

// ExtractLevelInfo recursively extracts description lines from a schema.
func ExtractLevelInfo(data any) []string {
	var results []string
	extractLevelInfoValue(data, &results)
	return results
}

func extractLevelInfoValue(data any, results *[]string) {
	switch d := data.(type) {
	case map[string]any:
		if desc, ok := d["description"].(string); ok && desc != "" {
			line := desc
			if defaultVal, ok := d["default"]; ok && defaultVal != nil {
				line += "; Default: " + ValueToString(defaultVal)
			}
			if enums, ok := AsArray(d["enum"]); ok && len(enums) > 0 {
				parts := make([]string, len(enums))
				for i, e := range enums {
					parts[i] = ValueToString(e)
				}
				line += "; Options: " + stringsJoin(parts, ", ")
			}
			*results = append(*results, line)
		}
		for _, val := range d {
			extractLevelInfoValue(val, results)
		}
	case []any:
		for _, item := range d {
			extractLevelInfoValue(item, results)
		}
	}
}

func stringsJoin(parts []string, sep string) string {
	if len(parts) == 0 {
		return ""
	}
	out := parts[0]
	for i := 1; i < len(parts); i++ {
		out += sep + parts[i]
	}
	return out
}

// ExtractDocumentText joins level info lines into document text.
func ExtractDocumentText(itemContent any) (string, bool) {
	lines := ExtractLevelInfo(itemContent)
	if len(lines) == 0 {
		return "", false
	}
	return stringsJoin(lines, "\n"), true
}

// ExtractJSONCatalogDocument extracts text from a json catalog entry.
func ExtractJSONCatalogDocument(item any) (string, bool) {
	obj, ok := AsObject(item)
	if !ok {
		return "", false
	}
	content, ok := obj["content"]
	if !ok {
		return "", false
	}
	return ExtractDocumentText(content)
}

// ExtractMDCatalogDocument extracts text from a markdown catalog entry.
func ExtractMDCatalogDocument(item any) (string, bool) {
	obj, ok := AsObject(item)
	if !ok {
		return "", false
	}
	content := obj["content"]
	if content == nil {
		return "", false
	}
	return ValueToString(content), true
}
