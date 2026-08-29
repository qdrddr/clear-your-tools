//go:build !windows

package cytindexer

import "fmt"

const maxCULongTokens = ^uint64(0)

func truncateMaxTokensArg(maxTokens uint64) (uint64, error) {
	if maxTokens > maxCULongTokens {
		return 0, fmt.Errorf("maxTokens exceeds platform unsigned long maximum")
	}
	return maxTokens, nil
}
