//! Background disk persistence for cache builds (non-blocking when enabled).

use std::path::PathBuf;
use std::sync::{LazyLock, Mutex, mpsc};
use std::thread;

use crate::build::CatalogIndex;
use crate::pageindex::SkillsIndex;
use crate::skills_io::write_skills_index;

use super::config::memory_cache_config;

enum DiskWriteJob {
    SkillsIndex {
        entry_dir: PathBuf,
        index: SkillsIndex,
    },
    ToolCatalog {
        entry_dir: PathBuf,
        index: CatalogIndex,
    },
}

static DISK_WRITER: LazyLock<DiskWriter> = LazyLock::new(DiskWriter::new);

struct DiskWriter {
    sender: Mutex<mpsc::Sender<DiskWriteJob>>,
}

impl DiskWriter {
    fn new() -> Self {
        let (sender, receiver) = mpsc::channel();
        if thread::Builder::new()
            .name("cyt-cache-disk-writer".into())
            .spawn(move || {
                while let Ok(job) = receiver.recv() {
                    match job {
                        DiskWriteJob::SkillsIndex { entry_dir, index } => {
                            let _ = write_skills_index(&index, &entry_dir);
                        }
                        DiskWriteJob::ToolCatalog { entry_dir, index } => {
                            let _ = super::tools_catalog::write_tool_cache_sync(&entry_dir, &index);
                        }
                    }
                }
            })
            .is_err()
        {
            eprintln!("cyt cache disk writer thread failed to start; using synchronous writes");
        }
        Self {
            sender: Mutex::new(sender),
        }
    }

    fn enqueue(&self, job: DiskWriteJob) {
        if let Ok(sender) = self.sender.lock() {
            let _ = sender.send(job);
        }
    }
}

/// Queue a skills index write when async disk writes are enabled.
pub fn maybe_enqueue_skills_index(entry_dir: PathBuf, index: SkillsIndex) {
    if !memory_cache_config().async_disk_writes {
        let _ = write_skills_index(&index, &entry_dir);
        return;
    }
    DISK_WRITER.enqueue(DiskWriteJob::SkillsIndex { entry_dir, index });
}

/// Queue a tool catalog write when async disk writes are enabled.
pub fn maybe_enqueue_tool_catalog(entry_dir: PathBuf, index: CatalogIndex) {
    if !memory_cache_config().async_disk_writes {
        let _ = super::tools_catalog::write_tool_cache_sync(&entry_dir, &index);
        return;
    }
    DISK_WRITER.enqueue(DiskWriteJob::ToolCatalog { entry_dir, index });
}
