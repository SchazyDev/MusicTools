//! Состояние приложения, разделяемое командами.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use crate::jobs::JobRegistry;
use crate::settings::Settings;
use crate::tools_registry::ToolRegistry;

pub struct AppState {
    pub tools: ToolRegistry,
    /// Единый реестр заданий; создаётся в `lib.rs` с привязкой к AppHandle.
    pub jobs: Arc<JobRegistry>,
    pub settings: Mutex<Settings>,
    /// Каталог ресурсов: в dev — `src-tauri/resources`, в релизе — каталог установки.
    pub resources_dir: PathBuf,
    pub log_path: PathBuf,
    pub version: String,
    /// Флаг завершения приложения для длительных заданий.
    pub shutdown: Arc<AtomicBool>,
}

impl AppState {
    pub fn new(
        resources_dir: PathBuf,
        log_path: PathBuf,
        version: String,
        jobs: Arc<JobRegistry>,
    ) -> Self {
        Self {
            tools: crate::tools_registry::builtin_tools().expect("встроенные инструменты валидны"),
            jobs,
            settings: Mutex::new(Settings::load()),
            resources_dir,
            log_path,
            version,
            shutdown: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn is_shutting_down(&self) -> bool {
        self.shutdown.load(Ordering::SeqCst)
    }
}
