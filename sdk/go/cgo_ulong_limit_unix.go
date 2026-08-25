//go:build !windows

package cytindexer

const maxCULongTokens = ^uint64(0)

func truncateMaxTokensArg(maxTokens uint64) (uint64, error) {
	return maxTokens, nil
}
