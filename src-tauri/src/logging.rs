//! Логирование в файл рядом с исполняемым файлом.
//!
//! Совместимо с PyQt-версией: имя `music_tools.log`, ротация при превышении
//! размера, уровень из `MUSIC_TOOLS_LOG_LEVEL` (по умолчанию `info`).

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use log::{LevelFilter, Log, Metadata, Record};

const LOG_FILE_NAME: &str = "music_tools.log";
const MAX_BYTES: u64 = 2 * 1024 * 1024;
const MAX_ROTATED: u32 = 3;

/// Каталог логов: каталог исполняемого файла.
pub fn log_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(Path::to_owned))
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

fn rotate(dir: &Path, file: &Path) {
    let Ok(meta) = fs::metadata(file) else { return };
    if meta.len() < MAX_BYTES {
        return;
    }
    for idx in (1..MAX_ROTATED).rev() {
        let from = dir.join(format!("{LOG_FILE_NAME}.{idx}"));
        let to = dir.join(format!("{LOG_FILE_NAME}.{}", idx + 1));
        let _ = fs::rename(from, to);
    }
    let _ = fs::rename(file, dir.join(format!("{LOG_FILE_NAME}.1")));
}

struct FileLogger {
    file: Mutex<File>,
}

impl Log for FileLogger {
    fn enabled(&self, _: &Metadata<'_>) -> bool {
        true
    }

    fn log(&self, record: &Record<'_>) {
        let ts = chrono::Local::now().format("%Y-%m-%d %H:%M:%S%.3f");
        let line = format!("{ts} {:<5} {}: {}\r\n", record.level(), record.target(), record.args());
        let mut guard = match self.file.lock() {
            Ok(g) => g,
            Err(p) => p.into_inner(),
        };
        let _ = guard.write_all(line.as_bytes());
    }

    fn flush(&self) {
        if let Ok(mut g) = self.file.lock() {
            let _ = g.flush();
        }
    }
}

impl Drop for FileLogger {
    fn drop(&mut self) {
        self.flush();
    }
}

/// Инициализирует глобальный логгер и возвращает путь к файлу журнала.
pub fn init() -> PathBuf {
    let dir = log_dir();
    let _ = fs::create_dir_all(&dir);
    let path = dir.join(LOG_FILE_NAME);
    rotate(&dir, &path);

    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .unwrap_or_else(|_| {
            File::create(std::env::temp_dir().join(LOG_FILE_NAME)).expect("журнал во временном каталоге")
        });

    let level = std::env::var("MUSIC_TOOLS_LOG_LEVEL")
        .ok()
        .and_then(|s| s.parse::<LevelFilter>().ok())
        .unwrap_or(if cfg!(debug_assertions) { LevelFilter::Debug } else { LevelFilter::Info });

    // Логгер живёт весь срок процесса: храним его в OnceLock.
    static LOGGER: std::sync::OnceLock<FileLogger> = std::sync::OnceLock::new();
    let logger = LOGGER.get_or_init(|| FileLogger { file: Mutex::new(file) });
    if log::set_logger(logger).is_ok() {
        log::set_max_level(level);
    }
    path
}
