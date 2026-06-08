package cytindexer

import (
	"encoding/json"
	"fmt"
	"strconv"
)

// ValueToString stringifies a JSON value for IDs, names, and display.
func ValueToString(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case float64:
		if x == float64(int64(x)) {
			return strconv.FormatInt(int64(x), 10)
		}
		return strconv.FormatFloat(x, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(x)
	case nil:
		return ""
	default:
		b, err := json.Marshal(x)
		if err != nil {
			return fmt.Sprint(x)
		}
		return string(b)
	}
}

// CloneValue deep-clones a JSON-compatible value via marshal round-trip.
func CloneValue(v any) any {
	if v == nil {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		return v
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		return v
	}
	return out
}

// AsObject returns v as map[string]any when possible.
func AsObject(v any) (map[string]any, bool) {
	m, ok := v.(map[string]any)
	return m, ok
}

// AsArray returns v as []any when possible.
func AsArray(v any) ([]any, bool) {
	a, ok := v.([]any)
	return a, ok
}

// StrField reads a string field from an object map.
func StrField(obj map[string]any, key string) string {
	if obj == nil {
		return ""
	}
	return ValueToString(obj[key])
}

// JSONF64 parses a JSON number or numeric string.
func JSONF64(v any) (float64, bool) {
	if v == nil {
		return 0, false
	}
	switch x := v.(type) {
	case float64:
		return x, true
	case int:
		return float64(x), true
	case int64:
		return float64(x), true
	case string:
		f, err := strconv.ParseFloat(x, 64)
		return f, err == nil
	default:
		return 0, false
	}
}
