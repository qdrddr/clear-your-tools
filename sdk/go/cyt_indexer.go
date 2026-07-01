// Package cytindexer provides Go bindings for cyt-indexer via cgo.
//
// Build the shared C library first:
//
//	./sdk/c/scripts/build-c-lib.sh
package cytindexer

// ClearError clears the thread-local error message.
func ClearError() {
	cgoClearError()
}

// LastError returns the thread-local error message, or empty string if none.
func LastError() string {
	return cgoGetLastError()
}

// Version returns the cyt-indexer library version string.
func Version() (string, error) {
	return cgoGetVersion()
}

// CatalogToolCount counts tools in a catalog dict JSON object.
func CatalogToolCount(dataJSON string) (int64, error) {
	return cgoCatalogToolCount(dataJSON)
}
