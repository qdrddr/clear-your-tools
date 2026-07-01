package cytindexer

import "errors"

// DecomposedCatalog is an opaque in-memory decomposed catalog handle.
type DecomposedCatalog struct {
	handle decomposedCatalogHandle
}

// NewDecomposedCatalog creates an empty decomposed catalog.
func NewDecomposedCatalog() (*DecomposedCatalog, error) {
	h, err := cgoDecomposedCatalogNew()
	if err != nil {
		return nil, err
	}
	return &DecomposedCatalog{handle: h}, nil
}

// DecomposedCatalogFromCatalogIndex loads decomposed JSON from a catalog index.
func DecomposedCatalogFromCatalogIndex(indexJSON string) (*DecomposedCatalog, error) {
	h, err := cgoDecomposedCatalogFromCatalogIndex(indexJSON)
	if err != nil {
		return nil, err
	}
	return &DecomposedCatalog{handle: h}, nil
}

// DecomposedCatalogFromCatalogDict loads decomposed entries from a catalog dict JSON.
func DecomposedCatalogFromCatalogDict(dataJSON string) (*DecomposedCatalog, error) {
	h, err := cgoDecomposedCatalogFromCatalogDict(dataJSON)
	if err != nil {
		return nil, err
	}
	return &DecomposedCatalog{handle: h}, nil
}

// Close frees the decomposed catalog handle.
func (c *DecomposedCatalog) Close() {
	if c != nil && c.handle.h != nil {
		cgoDecomposedCatalogFree(c.handle)
		c.handle = decomposedCatalogHandle{}
	}
}

// HasJSON reports whether a decomposed JSON key exists.
func (c *DecomposedCatalog) HasJSON(key string) (bool, error) {
	if c == nil || c.handle.h == nil {
		return false, errors.New("closed decomposed catalog")
	}
	return cgoDecomposedCatalogHasJSON(c.handle, key)
}

// GetJSON returns decomposed JSON content for a key.
func (c *DecomposedCatalog) GetJSON(key string) (string, error) {
	if c == nil || c.handle.h == nil {
		return "", errors.New("closed decomposed catalog")
	}
	return cgoDecomposedCatalogGetJSON(c.handle, key)
}

// LoadCatalog loads a catalog directory and returns catalog dict JSON.
func LoadCatalog(dirPath string) (string, error) {
	return cgoLoadCatalog(dirPath)
}

// RetrieveTools reconstructs merged tool schemas from search/rerank output.
func RetrieveTools(dataJSON string, catalog *DecomposedCatalog, catalogIndexJSON string, applyDecomposedScoreFilter bool, preserveValuesJSON, ctxJSON string) (string, error) {
	if catalog == nil || catalog.handle.h == nil {
		return "", errors.New("closed decomposed catalog")
	}
	return cgoRetrieveTools(dataJSON, catalog.handle, catalogIndexJSON, applyDecomposedScoreFilter, preserveValuesJSON, ctxJSON)
}

// RetrieveCore runs core retrieval with explicit store and survivor JSON.
func RetrieveCore(dataJSON, storeJSON, survivorJSON string, applyDecomposedScoreFilter bool, policyOptionsJSON string) (string, error) {
	return cgoRetrieveCore(dataJSON, storeJSON, survivorJSON, applyDecomposedScoreFilter, policyOptionsJSON)
}

// ChunkSurvivorKey normalizes a survivor chunk file path.
func ChunkSurvivorKey(itemJSON, section string) (string, error) {
	return cgoChunkSurvivorKey(itemJSON, section)
}

// RemovedChunks returns catalog chunks not present in the surviving set.
func RemovedChunks(fullCatalogJSON, survivingJSON string, applyDecomposedScoreFilter bool) (string, error) {
	return cgoRemovedChunks(fullCatalogJSON, survivingJSON, applyDecomposedScoreFilter)
}

// RetrieveCatalogToolCount counts tools in a catalog dict JSON.
func RetrieveCatalogToolCount(dataJSON string) (int64, error) {
	return cgoRetrieveCatalogToolCount(dataJSON)
}

// ResolveBuildCatalog resolves build catalog JSON from catalog and survivor data.
func ResolveBuildCatalog(catalogJSON, survivorJSON string) (string, error) {
	return cgoResolveBuildCatalog(catalogJSON, survivorJSON)
}
