//! Трей, автозапуск и закрытие окна в трей.
//!
//! Поведение совпадает с PyQt-версией: левый клик по иконке переключает окно,
//! меню — «Открыть» / «Запускать при старте» / «Выход», закрытие окна прячет
//! его в трей, автозапуск — значение `Music Tools` в `HKCU\...\Run`.

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconEvent};
use tauri::{AppHandle, Manager, Runtime, WebviewWindow};

pub const AUTOSTART_VALUE_NAME: &str = "Music Tools";
const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// Показывает и активирует главное окно.
pub fn show_main_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Переключает видимость главного окна.
pub fn toggle_main_window<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window("main") {
        match window.is_visible() {
            Ok(true) => {
                let _ = window.hide();
            }
            _ => {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
    }
}

#[cfg(windows)]
pub fn autostart_enabled() -> bool {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey(RUN_KEY)
        .and_then(|k| k.get_value::<String, _>(AUTOSTART_VALUE_NAME))
        .map(|v| !v.trim().is_empty())
        .unwrap_or(false)
}

#[cfg(not(windows))]
pub fn autostart_enabled() -> bool {
    false
}

/// Включает или выключает автозапуск; возвращает новое состояние.
#[cfg(windows)]
pub fn set_autostart(enabled: bool) -> Result<bool, String> {
    use winreg::enums::{HKEY_CURRENT_USER, KEY_READ, KEY_WRITE};
    use winreg::RegKey;

    let current = std::env::current_exe().map_err(|e| format!("не удалось определить путь: {e}"))?;
    let command = format!("\"{}\"", current.display());

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let (run, _) = hkcu
        .create_subkey_with_flags(RUN_KEY, KEY_READ | KEY_WRITE)
        .map_err(|e| format!("не удалось открыть раздел автозапуска: {e}"))?;

    if enabled {
        run.set_value(AUTOSTART_VALUE_NAME, &command)
            .map_err(|e| format!("не удалось записать автозапуск: {e}"))?;
    } else {
        match run.delete_value(AUTOSTART_VALUE_NAME) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("не удалось удалить автозапуск: {e}")),
        }
    }
    Ok(autostart_enabled())
}

#[cfg(not(windows))]
pub fn set_autostart(_enabled: bool) -> Result<bool, String> {
    Err("автозапуск поддерживается только в Windows".into())
}

/// Устанавливает меню и обработчики иконки трея `main-tray`.
pub fn install<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Открыть", true, None::<&str>)?;
    let autostart = CheckMenuItem::with_id(
        app,
        "autostart",
        "Запускать при старте",
        true,
        autostart_enabled(),
        None::<&str>,
    )?;
    let sep = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &autostart, &sep, &quit])?;

    let Some(tray) = app.tray_by_id("main-tray") else {
        log::warn!("иконка трея main-tray не найдена в конфигурации");
        return Ok(());
    };

    tray.set_menu(Some(menu))?;
    tray.set_show_menu_on_left_click(false)?;

    let autostart_item = autostart.clone();
    let clicked = app.clone();
    tray.on_menu_event(move |app: &AppHandle<R>, event| match event.id.as_ref() {
        "open" => show_main_window(app),
        "autostart" => {
            let desired = !autostart_enabled();
            match set_autostart(desired) {
                Ok(now) => {
                    let _ = autostart_item.set_checked(now);
                    log::info!("автозапуск переключён: {now}");
                }
                Err(e) => log::error!("автозапуск: {e}"),
            }
        }
        "quit" => {
            log::info!("выход из приложения по запросу из трея");
            clicked.exit(0);
        }
        _ => {}
    });

    let handle = app.clone();
    tray.on_tray_icon_event(move |_tray, event| {
        if let TrayIconEvent::Click {
            button: MouseButton::Left,
            button_state: MouseButtonState::Up,
            ..
        } = event
        {
            toggle_main_window(&handle);
        }
    });

    Ok(())
}

/// Вешает обработчик закрытия окна: скрыть вместо выхода.
pub fn install_close_handler(window: &WebviewWindow) {
    let handle = window.app_handle().clone();
    window.on_window_event(move |event| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            let close_to_tray = handle
                .try_state::<crate::state::AppState>()
                .and_then(|s| s.settings.lock().ok().map(|g| g.close_to_tray))
                .unwrap_or(true);
            if close_to_tray {
                api.prevent_close();
                if let Some(w) = handle.get_webview_window("main") {
                    let _ = w.hide();
                }
            }
        }
    });
}
