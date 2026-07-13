// Command cyt-native-ensure downloads or copies cyt-indexer C FFI artifacts for the host platform.
package main

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"

	"github.com/qdrddr/clear-your-tools/sdk/go/moduleversion"
)

const (
	dirPerm            = 0o750
	filePerm           = 0o644
	maxExtractFileSize = 512 * 1024 * 1024
	httpTimeout        = 10 * time.Minute
)

var (
	repoPattern  = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)
	assetPattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)
	tagPattern   = regexp.MustCompile(`^v[0-9]+\.[0-9]+\.[0-9]+$`)
)

func main() {
	var (
		version    = flag.String("version", "", "Release semver (default: CYT_RELEASE_VERSION, then module version)")
		repo       = flag.String("repo", defaultRepo, "GitHub owner/repo for release downloads")
		printEnv   = flag.Bool("print-env", false, "Print shell exports for CGO_LDFLAGS/CGO_CFLAGS")
		cacheDir   = flag.String("cache-dir", "", "Override cache root (default: XDG_CACHE_HOME/cyt-indexer)")
		nativeDir  = flag.String("native-dir", "", "Also copy artifacts here (default: <sdk/go>/native/<triplet> when writable)")
		staticOnly = flag.Bool("static-only", false, "Install only static library + header (avoids release dylib rpaths on macOS)")
		force      = flag.Bool("force", false, "Re-download even if cached artifacts exist")
	)
	flag.Parse()

	ver := resolveVersion(*version)
	triplet, err := resolveTriplet()
	if err != nil {
		fatal(err)
	}
	if err := validateTriplet(triplet); err != nil {
		fatal(err)
	}

	dest, nativeInstalled, err := ensureNative(ensureConfig{
		version:    ver,
		repo:       *repo,
		triplet:    triplet,
		cacheRoot:  *cacheDir,
		nativeDir:  *nativeDir,
		staticOnly: *staticOnly,
		force:      *force,
	})
	if err != nil {
		fatal(err)
	}

	if *printEnv {
		printShellEnv(dest, triplet)
		return
	}

	fmt.Printf("cyt-indexer native artifacts ready: %s\n", dest)
	fmt.Printf("triplet: %s\n", triplet)
	fmt.Printf("version: %s\n", ver)
	if !nativeInstalled {
		fmt.Fprintf(os.Stderr, "hint: eval \"$(go tool cyt-native-ensure --print-env)\" before go build\n")
	}
}

type ensureConfig struct {
	version    string
	repo       string
	triplet    string
	cacheRoot  string
	nativeDir  string
	staticOnly bool
	force      bool
}

func resolveVersion(flagVersion string) string {
	if flagVersion != "" {
		return strings.TrimPrefix(flagVersion, "v")
	}
	if env := os.Getenv("CYT_RELEASE_VERSION"); env != "" {
		return strings.TrimPrefix(env, "v")
	}
	return moduleversion.Version
}

func ensureNative(cfg ensureConfig) (cacheDest string, nativeInstalled bool, err error) {
	cacheDest, err = cacheDestDir(cfg.cacheRoot, cfg.version, cfg.triplet)
	if err != nil {
		return "", false, err
	}

	if monorepoDir := monorepoArtifactDir(cfg.triplet); monorepoDir != "" {
		if err := mkdirAll(cacheDest); err != nil {
			return "", false, err
		}
		if err := copyArtifacts(monorepoDir, cacheDest, cfg.triplet, cfg.staticOnly); err != nil {
			return "", false, err
		}
	}

	if cfg.force || !hasNativeLibs(cacheDest, cfg.triplet) {
		if err := mkdirAll(cacheDest); err != nil {
			return "", false, err
		}
		if err := downloadReleaseArtifacts(cfg.repo, cfg.version, cfg.triplet, cacheDest); err != nil {
			return "", false, err
		}
		if cfg.staticOnly {
			removeSharedLib(cacheDest, cfg.triplet)
		}
	}

	if !hasNativeLibs(cacheDest, cfg.triplet) {
		return "", false, fmt.Errorf("native library not found in %s after ensure", cacheDest)
	}

	nativeDest := cfg.nativeDir
	if nativeDest == "" {
		nativeDest = moduleNativePath(cfg.triplet)
	}
	if nativeDest == "" {
		return cacheDest, false, nil
	}

	if err := copyArtifacts(cacheDest, nativeDest, cfg.triplet, cfg.staticOnly); err != nil {
		return "", false, err
	}
	if !hasNativeLibs(nativeDest, cfg.triplet) {
		return "", false, fmt.Errorf("failed to install native library under %s", nativeDest)
	}
	return cacheDest, true, nil
}

func mkdirAll(dir string) error {
	return os.MkdirAll(dir, dirPerm)
}

func cacheDestDir(cacheRoot, version, triplet string) (string, error) {
	if cacheRoot != "" {
		return filepath.Join(cacheRoot, version, triplet), nil
	}
	if env := os.Getenv("CYT_NATIVE_DIR"); env != "" {
		return filepath.Join(env, version, triplet), nil
	}
	base, err := os.UserCacheDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, "cyt-indexer", version, triplet), nil
}

func monorepoArtifactDir(triplet string) string {
	root := repoRootFromCwd()
	if root == "" {
		return ""
	}
	candidate := filepath.Join(root, "target", triplet, "release")
	if hasNativeLibs(candidate, triplet) {
		return candidate
	}
	return ""
}

func repoRootFromCwd() string {
	for _, root := range []string{
		os.Getenv("CYT_REPO_ROOT"),
		os.Getenv("GITHUB_WORKSPACE"),
	} {
		if isRepoRoot(root) {
			return root
		}
	}

	wd, err := os.Getwd()
	if err != nil {
		return ""
	}
	for dir := wd; ; dir = filepath.Dir(dir) {
		if isRepoRoot(dir) {
			return dir
		}
		if dir == filepath.Dir(dir) {
			break
		}
	}
	return ""
}

func isRepoRoot(dir string) bool {
	if dir == "" {
		return false
	}
	root, err := os.OpenRoot(dir)
	if err != nil {
		return false
	}
	defer root.Close()

	if _, err := root.Stat("sdk/go/go.mod"); err != nil {
		return false
	}
	_, err = root.Stat("Cargo.toml")
	return err == nil
}

func hasNativeLibs(dir, triplet string) bool {
	if dir == "" {
		return false
	}
	if _, err := os.Stat(filepath.Join(dir, staticLibName(triplet))); err == nil {
		return true
	}
	if name := importLibName(triplet); name != "" {
		if _, err := os.Stat(filepath.Join(dir, name)); err == nil {
			return true
		}
	}
	_, err := os.Stat(filepath.Join(dir, sharedLibName(triplet)))
	return err == nil
}

func moduleNativePath(triplet string) string {
	root := sdkModuleRoot()
	if root == "" {
		return ""
	}
	dir := filepath.Join(root, "native", triplet)
	if err := mkdirAll(dir); err != nil {
		return ""
	}
	return dir
}

func artifactNames(triplet string, staticOnly bool) []string {
	names := []string{
		staticLibName(triplet),
		"cyt_indexer.h",
	}
	if !staticOnly {
		names = append(names, sharedLibName(triplet))
	}
	if isWindowsMSVC(triplet) {
		names = append(names, importLibName(triplet))
	}
	return names
}

func copyArtifacts(srcDir, destDir, triplet string, staticOnly bool) error {
	if err := mkdirAll(destDir); err != nil {
		return err
	}
	for _, name := range artifactNames(triplet, staticOnly) {
		src := filepath.Join(srcDir, name)
		if _, err := os.Stat(src); err != nil {
			continue
		}
		if err := copyFile(src, filepath.Join(destDir, name)); err != nil {
			return err
		}
	}
	if staticOnly {
		removeSharedLib(destDir, triplet)
	}
	return nil
}

func removeSharedLib(dir, triplet string) {
	_ = os.Remove(filepath.Join(dir, sharedLibName(triplet)))
}

func copyFile(src, dest string) error {
	srcDir, srcName := filepath.Split(src)
	destDir, destName := filepath.Split(dest)

	srcRoot, err := os.OpenRoot(srcDir)
	if err != nil {
		return err
	}
	defer srcRoot.Close()

	destRoot, err := os.OpenRoot(destDir)
	if err != nil {
		return err
	}
	defer destRoot.Close()

	in, err := srcRoot.Open(srcName)
	if err != nil {
		return err
	}
	defer in.Close()

	out, err := destRoot.Create(destName)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	return out.Close()
}

func downloadReleaseArtifacts(repo, version, triplet, destDir string) error {
	tag := "v" + strings.TrimPrefix(version, "v")
	if !tagPattern.MatchString(tag) {
		return fmt.Errorf("invalid release tag: %s", tag)
	}

	sumURL, err := releaseAssetURL(repo, tag, "SHA256SUMS")
	if err != nil {
		return err
	}
	sumData, err := httpGet(sumURL)
	if err != nil {
		return fmt.Errorf("fetch SHA256SUMS: %w", err)
	}

	archive := archiveName(triplet)
	expectedHash, err := lookupChecksum(sumData, archive)
	if err != nil {
		return err
	}

	archiveURL, err := releaseAssetURL(repo, tag, archive)
	if err != nil {
		return err
	}
	data, err := httpGet(archiveURL)
	if err != nil {
		return fmt.Errorf("fetch %s: %w", archive, err)
	}
	if err := verifyDownloadSHA256(data, expectedHash); err != nil {
		return fmt.Errorf("verify %s: %w", archive, err)
	}
	return extractTarGz(data, destDir)
}

func releaseAssetURL(repo, tag, asset string) (string, error) {
	if !repoPattern.MatchString(repo) {
		return "", fmt.Errorf("invalid GitHub repo: %q", repo)
	}
	if !tagPattern.MatchString(tag) {
		return "", fmt.Errorf("invalid release tag: %q", tag)
	}
	if !assetPattern.MatchString(asset) {
		return "", fmt.Errorf("invalid release asset: %q", asset)
	}
	raw := fmt.Sprintf("https://github.com/%s/releases/download/%s/%s", repo, tag, asset)
	parsed, err := url.Parse(raw)
	if err != nil {
		return "", err
	}
	if parsed.Scheme != "https" || parsed.Host != "github.com" {
		return "", fmt.Errorf("untrusted release URL host: %s", parsed.Host)
	}
	return parsed.String(), nil
}

func lookupChecksum(sumData []byte, archive string) (string, error) {
	scanner := bufio.NewScanner(bytes.NewReader(sumData))
	for scanner.Scan() {
		fields := strings.Fields(strings.TrimSpace(scanner.Text()))
		if len(fields) == 2 && fields[1] == archive {
			return fields[0], nil
		}
	}
	return "", fmt.Errorf("SHA256SUMS has no entry for %s", archive)
}

func verifyDownloadSHA256(data []byte, expectedHex string) error {
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if !strings.EqualFold(got, expectedHex) {
		return fmt.Errorf("SHA256 mismatch: expected %s, got %s", expectedHex, got)
	}
	return nil
}

func httpGet(rawURL string) ([]byte, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	if parsed.Scheme != "https" || parsed.Host != "github.com" {
		return nil, fmt.Errorf("untrusted download host: %s", parsed.Host)
	}

	client := &http.Client{Timeout: httpTimeout}
	req, err := http.NewRequest(http.MethodGet, parsed.String(), nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d for %s", resp.StatusCode, rawURL)
	}
	return io.ReadAll(resp.Body)
}

func extractTarGz(data []byte, destDir string) error {
	destRoot, err := os.OpenRoot(destDir)
	if err != nil {
		return err
	}
	defer destRoot.Close()

	gr, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return err
	}
	defer gr.Close()

	tr := tar.NewReader(gr)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		if hdr.Typeflag != tar.TypeReg {
			continue
		}
		name := filepath.Base(hdr.Name)
		if name == "." || name == "" {
			continue
		}
		clean := filepath.Clean(hdr.Name)
		if strings.Contains(clean, "..") || filepath.IsAbs(clean) {
			continue
		}

		out, err := destRoot.Create(name)
		if err != nil {
			return err
		}
		limited := io.LimitReader(tr, maxExtractFileSize+1)
		written, copyErr := io.Copy(out, limited)
		closeErr := out.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
		if written > maxExtractFileSize {
			return fmt.Errorf("archive entry %q exceeds size limit", name)
		}
		if err := destRoot.Chmod(name, filePerm); err != nil {
			return err
		}
	}
	return nil
}

func printShellEnv(dest, triplet string) {
	static := filepath.Join(dest, staticLibName(triplet))
	var ldflags string
	switch runtime.GOOS {
	case "linux":
		ldflags = static + " -lm -ldl -pthread"
	case "darwin":
		ldflags = static + " -framework Security -lpthread"
	default:
		ldflags = static
	}
	fmt.Printf("export CGO_CFLAGS=-I%q\n", dest)
	fmt.Printf("export CGO_LDFLAGS=%q\n", ldflags)
}

func fatal(err error) {
	fmt.Fprintf(os.Stderr, "cyt-native-ensure: %v\n", err)
	os.Exit(1)
}
