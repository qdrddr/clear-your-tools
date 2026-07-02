// Command cyt-native-ensure downloads or copies cyt-indexer C FFI artifacts for the host platform.
package main

import (
	"archive/tar"
	"bufio"
	"bytes"
	"compress/gzip"
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
)

const (
	moduleVersion      = "0.6.1"
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
		version   = flag.String("version", "", "Release semver (default: CYT_RELEASE_VERSION, then embedded module version)")
		repo      = flag.String("repo", defaultRepo, "GitHub owner/repo for release downloads")
		printEnv  = flag.Bool("print-env", false, "Print shell exports for CGO_LDFLAGS/CGO_CFLAGS")
		cacheDir  = flag.String("cache-dir", "", "Override cache root (default: XDG_CACHE_HOME/cyt-indexer)")
		nativeDir = flag.String("native-dir", "", "Also copy artifacts here (default: <module>/native/<triplet> when writable)")
		force     = flag.Bool("force", false, "Re-download even if artifacts exist")
	)
	flag.Parse()

	ver := resolveVersion(*version)
	triplet, err := hostTriplet()
	if err != nil {
		fatal(err)
	}
	if err := validateTriplet(triplet); err != nil {
		fatal(err)
	}

	dest, err := ensureNative(ensureConfig{
		version:   ver,
		repo:      *repo,
		triplet:   triplet,
		cacheRoot: *cacheDir,
		nativeDir: *nativeDir,
		force:     *force,
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
	if moduleNativePath(triplet) == "" {
		fmt.Fprintf(os.Stderr, "hint: eval \"$(go tool cyt-native-ensure --print-env)\" before go build\n")
	}
}

type ensureConfig struct {
	version   string
	repo      string
	triplet   string
	cacheRoot string
	nativeDir string
	force     bool
}

func resolveVersion(flagVersion string) string {
	if flagVersion != "" {
		return strings.TrimPrefix(flagVersion, "v")
	}
	if env := os.Getenv("CYT_RELEASE_VERSION"); env != "" {
		return strings.TrimPrefix(env, "v")
	}
	return moduleVersion
}

func ensureNative(cfg ensureConfig) (string, error) {
	cacheDest, err := cacheDestDir(cfg.cacheRoot, cfg.version, cfg.triplet)
	if err != nil {
		return "", err
	}

	if monorepoDir := monorepoArtifactDir(cfg.triplet); monorepoDir != "" {
		if err := mkdirAll(cacheDest); err != nil {
			return "", err
		}
		if err := copyArtifacts(monorepoDir, cacheDest, cfg.triplet); err != nil {
			return "", err
		}
		maybeCopyToNativeDir(cacheDest, cfg.nativeDir, cfg.triplet)
		return cacheDest, nil
	}

	if !cfg.force && hasNativeLibs(cacheDest, cfg.triplet) {
		maybeCopyToNativeDir(cacheDest, cfg.nativeDir, cfg.triplet)
		return cacheDest, nil
	}

	if err := mkdirAll(cacheDest); err != nil {
		return "", err
	}
	if err := downloadReleaseArtifacts(cfg.repo, cfg.version, cfg.triplet, cacheDest); err != nil {
		return "", err
	}
	maybeCopyToNativeDir(cacheDest, cfg.nativeDir, cfg.triplet)
	return cacheDest, nil
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
	wd, err := os.Getwd()
	if err != nil {
		return ""
	}
	for dir := wd; ; dir = filepath.Dir(dir) {
		if _, err := os.Stat(filepath.Join(dir, "sdk", "go", "go.mod")); err == nil {
			if _, err := os.Stat(filepath.Join(dir, "Cargo.toml")); err == nil {
				return dir
			}
		}
		if dir == filepath.Dir(dir) {
			break
		}
	}
	return ""
}

func hasNativeLibs(dir, triplet string) bool {
	if dir == "" {
		return false
	}
	if _, err := os.Stat(filepath.Join(dir, staticLibName(triplet))); err == nil {
		return true
	}
	_, err := os.Stat(filepath.Join(dir, sharedLibName(triplet)))
	return err != nil
}

func maybeCopyToNativeDir(cacheDest, nativeDir, triplet string) {
	dest := nativeDir
	if dest == "" {
		dest = moduleNativePath(triplet)
	}
	if dest == "" {
		return
	}
	_ = copyArtifacts(cacheDest, dest, triplet)
}

func moduleNativePath(triplet string) string {
	root, err := findModuleRoot()
	if err != nil {
		return ""
	}
	dir := filepath.Join(root, "native", triplet)
	if err := mkdirAll(dir); err != nil {
		return ""
	}
	return dir
}

func findModuleRoot() (string, error) {
	wd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for dir := wd; ; dir = filepath.Dir(dir) {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}
		if dir == filepath.Dir(dir) {
			break
		}
	}
	return "", fmt.Errorf("go.mod not found from %s", wd)
}

func copyArtifacts(srcDir, destDir, triplet string) error {
	if err := mkdirAll(destDir); err != nil {
		return err
	}
	names := []string{
		staticLibName(triplet),
		sharedLibName(triplet),
		"cyt_indexer.h",
	}
	if isWindowsMSVC(triplet) {
		names = append(names, "cyt_indexer.dll.lib")
	}
	for _, name := range names {
		src := filepath.Join(srcDir, name)
		if _, err := os.Stat(src); err != nil {
			continue
		}
		if err := copyFile(src, filepath.Join(destDir, name)); err != nil {
			return err
		}
	}
	return nil
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
	if err := verifyChecksumListed(sumData, archive); err != nil {
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

func verifyChecksumListed(sumData []byte, archive string) error {
	scanner := bufio.NewScanner(bytes.NewReader(sumData))
	for scanner.Scan() {
		fields := strings.Fields(strings.TrimSpace(scanner.Text()))
		if len(fields) == 2 && fields[1] == archive {
			return nil
		}
	}
	return fmt.Errorf("SHA256SUMS has no entry for %s", archive)
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
		ldflags = fmt.Sprintf("%q -lm -ldl -pthread", static)
	case "darwin":
		ldflags = fmt.Sprintf("%q -framework Security -lpthread", static)
	default:
		ldflags = fmt.Sprintf("%q", static)
	}
	fmt.Printf("export CGO_CFLAGS=-I%q\n", dest)
	fmt.Printf("export CGO_LDFLAGS=%s\n", ldflags)
}

func fatal(err error) {
	fmt.Fprintf(os.Stderr, "cyt-native-ensure: %v\n", err)
	os.Exit(1)
}
