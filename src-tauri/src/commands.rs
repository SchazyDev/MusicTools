//! Команды Tauri: единый IPC-контракт.
//!
//! Каждая команда возвращает `Result<T, String>` — UI получает локализованную
//! строку. Длительные операции возвращают `job_id` немедленно; события идут
//! в `job://event`. Контракт описан в `docs/TAURI_ARCHITECTURE.md`.

use serde::Serialize;
use tauri::{AppHandle, State, WebviewWindow};

use crate::jobs::{JobError, JobOutcome, JobSnapshot};
use crate::settings::Settings;
use crate::state::AppState;
use crate::tools_registry::ToolDescriptor;

/// Унифицированный ответ об ошибке для IPC.
pub type IpcResult<T> = Result<T, String>;

// ---------------------------------------------------------------------------
// Служебные команды
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct AppInfo {
    pub name: String,
    pub version: String,
    pub identifier: String,
    pub log_path: String,
    pub settings_path: String,
    pub os: String,
    pub debug: bool,
}

/// Сведения о приложении: версия из `tauri.conf.json`, пути, ОС.
#[tauri::command]
pub async fn app_info(state: State<'_, AppState>, app: AppHandle) -> IpcResult<AppInfo> {
    Ok(AppInfo {
        name: app.package_info().name.clone(),
        version: state.version.clone(),
        identifier: app.config().identifier.clone(),
        log_path: state.log_path.display().to_string(),
        settings_path: crate::settings::settings_path().display().to_string(),
        os: format!("{} {}", std::env::consts::OS, std::env::consts::ARCH),
        debug: cfg!(debug_assertions),
    })
}

// ---------------------------------------------------------------------------
// Реестр инструментов
// ---------------------------------------------------------------------------

/// Возвращает включённые инструменты, отсортированные по (order, title).
#[tauri::command]
pub async fn get_tools(state: State<'_, AppState>) -> IpcResult<Vec<ToolDescriptor>> {
    Ok(state.tools.enabled().into_iter().cloned().collect())
}

// ---------------------------------------------------------------------------
// Настройки
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn get_settings(state: State<'_, AppState>) -> IpcResult<Settings> {
    let guard = state.settings.lock().map_err(|_| "блокировка настроек отравлена".to_string())?;
    Ok(guard.clone())
}

/// Частичное обновление настроек: неизвестные ключи отбрасываются,
/// некорректные каналы откатываются к `stable`, запись атомарна.
#[tauri::command]
pub async fn update_settings(
    state: State<'_, AppState>,
    patch: serde_json::Value,
) -> IpcResult<Settings> {
    let mut guard = state.settings.lock().map_err(|_| "блокировка настроек отравлена".to_string())?;
    let mut merged = serde_json::to_value(&*guard).map_err(|e| format!("сериализация настроек: {e}"))?;
    if let (Some(dst), Some(src)) = (merged.as_object_mut(), patch.as_object()) {
        for (k, v) in src {
            dst.insert(k.clone(), v.clone());
        }
    }
    let updated: Settings =
        serde_json::from_value(merged).map_err(|e| format!("некорректные настройки: {e}"))?;
    updated.save().map_err(|e| format!("не удалось сохранить настройки: {e}"))?;
    *guard = updated.clone();
    Ok(updated)
}

// ---------------------------------------------------------------------------
// Задания
// ---------------------------------------------------------------------------

/// Демонстрация контракта задания: шаги с прогрессом и отменой.
/// Реальные download/convert пишутся так же (см. docs/TAURI_ARCHITECTURE.md).
#[tauri::command]
pub async fn start_demo_job(state: State<'_, AppState>, steps: Option<u8>) -> IpcResult<u64> {
    let steps = steps.unwrap_or(20).max(1);
    let id = state.jobs.spawn("demo", move |ctx| {
        Box::pin(async move {
            for step in 1..=steps {
                if let Err(JobError::Cancelled) = ctx.check_cancel() {
                    return JobOutcome::Canceled;
                }
                ctx.status(format!("Шаг {step} из {steps}"));
                ctx.progress((step as u32 * 100 / steps as u32) as u8);
                if let Err(JobError::Cancelled) = ctx.sleep(50).await {
                    return JobOutcome::Canceled;
                }
            }
            JobOutcome::ok(format!("демонстрация завершена: {steps} шагов"))
        })
    });
    Ok(id)
}

#[tauri::command]
pub async fn cancel_job(state: State<'_, AppState>, job_id: u64) -> IpcResult<bool> {
    Ok(state.jobs.cancel(job_id))
}

#[tauri::command]
pub async fn list_jobs(state: State<'_, AppState>) -> IpcResult<Vec<JobSnapshot>> {
    Ok(state.jobs.snapshot())
}

// ---------------------------------------------------------------------------
// Скачивание
// ---------------------------------------------------------------------------

/// Предпросмотр ссылки: заголовок, миниатюра, доступные разрешения.
///
/// Выполняется синхронно для UI (до 30 секунд), поэтому вызывается только
/// после паузы ввода и с индикатором загрузки на стороне фронтенда.
#[tauri::command]
pub async fn download_preview(url: String) -> IpcResult<crate::tools::download::PreviewInfo> {
    crate::tools::download::preview(&url).await.map_err(|e| e.to_string())
}

/// Запускает скачивание и сразу возвращает `job_id`.
#[tauri::command]
pub async fn download_start(
    state: State<'_, AppState>,
    params: crate::tools::download::DownloadParams,
) -> IpcResult<u64> {
    let id = state.jobs.spawn("download", move |ctx| {
        Box::pin(async move { crate::tools::download::run_job(&ctx, params).await })
    });
    Ok(id)
}

/// Копирует готовый файл в выбранное пользователем место.
///
/// `source_path` берётся из поля `path` события `ok`; коллизии решаются
/// добавлением ` (1)` к имени. Прогресс идёт событиями нового задания.
#[tauri::command]
pub async fn save_result(
    state: State<'_, AppState>,
    source_path: String,
    destination_path: String,
) -> IpcResult<u64> {
    let source = std::path::PathBuf::from(source_path);
    let id = state.jobs.spawn("save", move |ctx| {
        Box::pin(async move {
            let job_id = ctx.job_id();
            match crate::tools::download::save_result(&ctx, job_id, &source, &destination_path).await
            {
                Ok(path) => {
                    JobOutcome::ok_with_path(format!("Файл сохранён: {path}"), path)
                }
                Err(JobError::Cancelled) => JobOutcome::Canceled,
                Err(JobError::Failed(message)) => JobOutcome::failed(message),
            }
        })
    });
    Ok(id)
}

// ---------------------------------------------------------------------------
// Окно и трей
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn hide_to_tray(window: WebviewWindow) -> IpcResult<()> {
    window.hide().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn show_main_window(app: AppHandle) -> IpcResult<()> {
    crate::tray::show_main_window(&app);
    Ok(())
}

/// Открывает файл журнала в проводнике Windows.
#[tauri::command]
pub async fn open_log_file(state: State<'_, AppState>) -> IpcResult<()> {
    let path = state.log_path.clone();
    #[cfg(windows)]
    {
        std::process::Command::new("explorer")
            .arg(&path)
            .spawn()
            .map(|_| ())
            .map_err(|e| format!("не удалось открыть {}: {e}", path.display()))
    }
    #[cfg(not(windows))]
    {
        Err(format!("открытие файла поддерживается только в Windows: {}", path.display()))
    }
}
