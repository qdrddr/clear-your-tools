// Package tools hosts dev-tool dependencies for the Go SDK module.
// pins.go keeps patched transitive module versions in go.mod/go.sum.
package tools

import (
	_ "github.com/aws/aws-sdk-go-v2"
	_ "github.com/aws/aws-sdk-go-v2/aws/protocol/eventstream"
	_ "github.com/golang-jwt/jwt/v5"
	_ "github.com/modelcontextprotocol/go-sdk/mcp"
	_ "github.com/yuin/goldmark"
	_ "google.golang.org/grpc"
)
