package main

import (
	"fmt"
	"os"
	"runtime"
	"strings"
)

const defaultRepo = "qdrddr/clear-your-tools"

var supportedTriplets = []string{
	"x86_64-unknown-linux-gnu",
	"aarch64-unknown-linux-gnu",
	"x86_64-apple-darwin",
	"aarch64-apple-darwin",
	"x86_64-pc-windows-msvc",
	"aarch64-pc-windows-msvc",
}

func hostTriplet() (string, error) {
	key := runtime.GOOS + "/" + runtime.GOARCH
	switch key {
	case "linux/amd64":
		return "x86_64-unknown-linux-gnu", nil
	case "linux/arm64":
		return "aarch64-unknown-linux-gnu", nil
	case "darwin/amd64":
		return "x86_64-apple-darwin", nil
	case "darwin/arm64":
		return "aarch64-apple-darwin", nil
	case "windows/amd64":
		return "x86_64-pc-windows-msvc", nil
	case "windows/arm64":
		return "aarch64-pc-windows-msvc", nil
	default:
		return "", fmt.Errorf("unsupported platform %s", key)
	}
}

func archiveName(triplet string) string {
	return "cyt-indexer-ffi-" + triplet + ".tar.gz"
}

func staticLibName(triplet string) string {
	if isWindowsMSVC(triplet) {
		return "cyt_indexer.lib"
	}
	return "libcyt_indexer.a"
}

func sharedLibName(triplet string) string {
	switch {
	case isWindowsMSVC(triplet):
		return "cyt_indexer.dll"
	case isDarwin(triplet):
		return "libcyt_indexer.dylib"
	default:
		return "libcyt_indexer.so"
	}
}

func importLibName(triplet string) string {
	if isWindowsMSVC(triplet) {
		return "cyt_indexer.dll.lib"
	}
	return ""
}

func resolveTriplet() (string, error) {
	if env := strings.TrimSpace(os.Getenv("CYT_RUST_TARGET")); env != "" {
		if err := validateTriplet(env); err != nil {
			return "", err
		}
		return env, nil
	}
	return hostTriplet()
}

func isWindowsMSVC(triplet string) bool {
	return strings.HasSuffix(triplet, "pc-windows-msvc")
}

func isDarwin(triplet string) bool {
	return strings.HasSuffix(triplet, "apple-darwin")
}

func validateTriplet(triplet string) error {
	for _, supported := range supportedTriplets {
		if triplet == supported {
			return nil
		}
	}
	return fmt.Errorf("unsupported triplet: %s", triplet)
}
