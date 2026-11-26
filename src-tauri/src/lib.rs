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
pub mod update;

use std::sync::Arc;

use tauri::Emitter;
use tauri::Manager;

use crate::jobs::JobRegistry;
use crate::state::AppState;

/// Каталог ресурсов: `src-tauri/resources` в dev, `<каталог установки>\resources`
/// в релизе.
///
/// `resource_dir()` из Tauri здесь не используется: без установщика (запуск exe
/// из произвольного каталога) он возвращает каталог самого exe, а сайдкары
/// всегда лежат в подкаталоге `resources`. Проверяем оба варианта.
fn resolve_resources_dir(app: &tauri::App) -> std::path::PathBuf {
    if cfg!(debug_assertions) {
        let dev = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources");
        if dev.exists() {
            return dev;
        }
    }

    let candidates: Vec<std::path::PathBuf> =
        [app.path().resource_dir().ok(), std::env::current_exe().ok().and_then(|exe| exe.parent().map(|p| p.to_owned()))]
            .into_iter()
            .flatten()
            .collect();

    for dir in &candidates {
        let nested = dir.join("resources");
        if nested.join("yt-dlp.exe").is_file() || nested.join("ffmpeg.exe").is_file() {
            return nested;
        }
        if dir.join("yt-dlp.exe").is_file() || dir.join("ffmpeg.exe").is_file() {
            return dir.clone();
        }
    }

    candidates
        .into_iter()
        .next()
        .map(|dir| dir.join("resources"))
        .unwrap_or_else(|| std::path::PathBuf::from("resources"))
}

  #[cfg_attr(mobile, tauri::mobile_entry_point)]
  pub fn run() {
      let log_path = logging::init();
      log::info!("запуск Music Tools, журнал: {}", log_path.display());

    let version = env!("CARGO_PKG_VERSION").to_owned();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(move |app| {
            let resources_dir = resolve_resources_dir(app);
            log::info!("каталог ресурсов: {}", resources_dir.display());

            let jobs = Arc::new(JobRegistry::new(app.handle().clone()));
            app.manage(AppState::new(resources_dir, log_path.clone(), version.clone(), jobs));

            tray::install(app.handle())?;

            // Диагностика окружения: какие сайдкары найдены. Помогает при
            // разборе жалоб «инструмент не работает» по журналу.
            for status in crate::tools::sidecar::all_statuses() {
                if status.found {
                    log::info!(
                        "программа {}: {} ({})",
                        status.name,
                        status.path.as_deref().unwrap_or("?"),
                        status.source
                    );
                } else {
                    log::warn!("программа {}: {}", status.name, status.source);
                }
            }

            if let Some(window) = app.get_webview_window("main") {
                tray::install_close_handler(&window);
            }

            // Фоновая проверка обновлений при старте: сеть не блокирует UI.
            if app
                .state::<AppState>()
                .settings
                .lock()
                .map(|guard| guard.check_on_startup)
                .unwrap_or(false)
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let (channel, version) = match handle.try_state::<AppState>() {
                        Some(state) => (
                            state
                                .settings
                                .lock()
                                .map(|guard| guard.channel.clone())
                                .unwrap_or_else(|_| crate::settings::DEFAULT_CHANNEL.to_owned()),
                            state.version.clone(),
                        ),
                        None => return,
                    };

                    match crate::update::fetch_latest_manifest(crate::update::GITHUB_REPO, &channel)
                        .await
                    {
                        Ok(Some(info)) => {
                            if crate::update::is_newer(&info.version, &version) {
                                log::info!("доступно обновление до версии {}", info.version);
                                let _ = handle.emit(crate::update::EVENT_UPDATE, &info);
                            }
                        }
                        Ok(None) => {}
                        Err(e) => log::warn!("проверка обновлений при старте: {e}"),
                    }
                });
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
            commands::download_preview,
            commands::download_start,
            commands::save_result,
            commands::convert_start,
            commands::save_convert_result,
            commands::check_updates,
            commands::start_update,
            commands::sidecars_status,
            commands::ytdlp_status,
            commands::ytdlp_update,
            commands::hide_to_tray,
            commands::show_main_window,
            commands::open_log_file,
            commands::open_url,
        ])
        .run(tauri::generate_context!())
        .expect("ошибка запуска приложения Music Tools");
}
