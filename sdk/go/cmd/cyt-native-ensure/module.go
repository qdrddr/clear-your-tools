package main

import (
	"os"
	"path/filepath"
	"strings"
)

const sdkModulePath = "github.com/qdrddr/clear-your-tools/sdk/go"

func sdkModuleRoot() string {
	if exe, err := os.Executable(); err == nil {
		if root := findSDKRootFromDir(filepath.Dir(exe)); root != "" {
			return root
		}
	}
	if wd, err := os.Getwd(); err == nil {
		if root := findSDKRootFromDir(wd); root != "" {
			return root
		}
	}
	return ""
}

func findSDKRootFromDir(start string) string {
	for dir := start; ; dir = filepath.Dir(dir) {
		if isSDKModuleRoot(dir) {
			return dir
		}
		if dir == filepath.Dir(dir) {
			break
		}
	}
	return ""
}

func isSDKModuleRoot(dir string) bool {
	root, err := os.OpenRoot(dir)
	if err != nil {
		return false
	}
	defer root.Close()

	data, err := root.ReadFile("go.mod")
	if err != nil {
		return false
	}
	for line := range strings.SplitSeq(string(data), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "module ") {
			return strings.TrimPrefix(line, "module ") == sdkModulePath
		}
	}
	return false
}
