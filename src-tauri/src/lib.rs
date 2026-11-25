//! Сборка приложения Music Tools (Tauri 2).
//!
//! Модули повторяют структуру PyQt-версии: `state` — глобальное состояние,
//! `jobs` — единый реестр заданий, `tools_registry` — реестр инструментов,
//! `settings` — `%APPDATA%\MusicTools\settings.json`, `logging` — файл журнала
//! рядом с exe, `tray` — иконка в трее и автозапуск, `commands` — IPC.

pub mod commands;
pub mod jobs;
pub mod logging;
pub mod settings;
pub mod state;
pub mod tools;
pub mod tools_registry;
pub mod tray;

use std::sync::Arc;

use tauri::Manager;

use crate::jobs::JobRegistry;
use crate::state::AppState;

/// Каталог ресурсов: `src-tauri/resources` в dev, каталог установки в релизе.
fn resolve_resources_dir(app: &tauri::App) -> std::path::PathBuf {
    if cfg!(debug_assertions) {
        let dev = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources");
        if dev.exists() {
            return dev;
        }
    }
    app.path().resource_dir().unwrap_or_else(|_| {
        std::env::current_exe()
            .ok()
            .and_then(|exe| exe.parent().map(|p| p.to_owned()))
            .unwrap_or_else(|| std::path::PathBuf::from("."))
    })
}

  #[cfg_attr(mobile, tauri::mobile_entry_point)]
  pub fn run() {
      let log_path = logging::init();
      log::info!("запуск Music Tools, журнал: {}", log_path.display());

    let version = env!("CARGO_PKG_VERSION").to_owned();

    tauri::Builder::default()
        .setup(move |app| {
            let resources_dir = resolve_resources_dir(app);
            log::info!("каталог ресурсов: {}", resources_dir.display());

            let jobs = Arc::new(JobRegistry::new(app.handle().clone()));
            app.manage(AppState::new(resources_dir, log_path.clone(), version.clone(), jobs));

            tray::install(app.handle())?;

            if let Some(window) = app.get_webview_window("main") {
                tray::install_close_handler(&window);
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::app_info,
            commands::get_tools,
            commands::get_settings,
            commands::update_settings,
            commands::start_demo_job,
            commands::cancel_job,
            commands::list_jobs,
            commands::hide_to_tray,
            commands::show_main_window,
            commands::open_log_file,
        ])
        .run(tauri::generate_context!())
        .expect("ошибка запуска приложения Music Tools");
}
