//go:build windows

package cytindexer

import "fmt"

const maxCULongTokens = 1<<32 - 1

func truncateMaxTokensArg(maxTokens uint64) (uint32, error) {
	if maxTokens > maxCULongTokens {
		return 0, fmt.Errorf("maxTokens exceeds platform unsigned long maximum")
	}
	return uint32(maxTokens), nil
}
