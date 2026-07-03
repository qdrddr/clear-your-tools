//! Exclusive build lock with stale detection.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

const LOCK_NAME: &str = ".build.lock";
const STALE_SECONDS: u64 = 600;

pub struct BuildLock {
    path: PathBuf,
    _file: File,
}

impl BuildLock {
    /// Acquire an exclusive lock, waiting briefly when held.
    ///
    /// # Errors
    ///
    /// Returns an error when the lock cannot be acquired.
    pub fn acquire(dir: &Path) -> Result<Self, String> {
        let path = dir.join(LOCK_NAME);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let deadline = SystemTime::now() + Duration::from_secs(5);
        loop {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(mut file) => {
                    let _ = writeln!(file, "{}", std::process::id());
                    return Ok(Self { path, _file: file });
                }
                Err(_) if SystemTime::now() < deadline => {
                    if lock_stale(&path) {
                        let _ = std::fs::remove_file(&path);
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
                Err(err) => return Err(err.to_string()),
            }
        }
    }
}

impl Drop for BuildLock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

fn lock_stale(path: &Path) -> bool {
    let Ok(meta) = std::fs::metadata(path) else {
        return true;
    };
    let Ok(modified) = meta.modified() else {
        return true;
    };
    modified
        .elapsed()
        .map_or(true, |elapsed| elapsed > Duration::from_secs(STALE_SECONDS))
}
