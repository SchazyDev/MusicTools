//! Настройки приложения.
//!
//! Формат и путь — те же, что у PyQt-версии (`core/settings.py`):
//! `%APPDATA%\MusicTools\settings.json`, атомарная запись через временный файл
//! и `rename`, при порче файла возвращаются значения по умолчанию. Совместимость
//! позволяет миграции не терять настройки пользователя.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

pub const APP_DIR_NAME: &str = "MusicTools";
pub const SETTINGS_FILE_NAME: &str = "settings.json";
pub const DEFAULT_CHANNEL: &str = "stable";
pub const CHANNELS: [&str; 2] = ["stable", "beta"];

/// Персистентные настройки. Неизвестные ключи JSON отбрасываются.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    pub channel: String,
    pub check_on_startup: bool,
    /// Прятать в трей вместо закрытия окна.
    pub close_to_tray: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self { channel: DEFAULT_CHANNEL.to_owned(), check_on_startup: true, close_to_tray: true }
    }
}

/// Каталог настроек: `%APPDATA%\MusicTools` с откатом на `%USERPROFILE%`.
pub fn settings_dir() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("USERPROFILE")
                .map(|p| PathBuf::from(p).join("AppData").join("Roaming"))
        })
        .unwrap_or_else(|| PathBuf::from("."));
    base.join(APP_DIR_NAME)
}

pub fn settings_path() -> PathBuf {
    settings_dir().join(SETTINGS_FILE_NAME)
}

impl Settings {
    /// Читает настройки. Никогда не падает: при любой ошибке — умолчания.
    pub fn load() -> Self {
        Self::load_from(&settings_path())
    }

    pub fn load_from(path: &Path) -> Self {
        let raw = match fs::read_to_string(path) {
            Ok(raw) => raw,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let defaults = Self::default();
                if let Err(err) = defaults.save_to(path) {
                    log::warn!("не удалось создать файл настроек: {err}");
                }
                return defaults;
            }
            Err(e) => {
                log::warn!("не удалось прочитать настройки {}: {e}", path.display());
                return Self::default();
            }
        };

        let mut value: serde_json::Value = match serde_json::from_str(&raw) {
            Ok(v) => v,
            Err(e) => {
                log::warn!("настройки повреждены: {e}");
                return Self::default();
            }
        };
        if !value.is_object() {
            log::warn!("настройки: ожидался объект JSON, используются значения по умолчанию");
            return Self::default();
        }

        let channel = value.get("channel").and_then(|v| v.as_str()).unwrap_or(DEFAULT_CHANNEL);
        if !CHANNELS.contains(&channel) {
            log::warn!("неизвестный канал обновлений {channel:?}, используется {DEFAULT_CHANNEL}");
            value["channel"] = serde_json::json!(DEFAULT_CHANNEL);
        }
        if !value.get("check_on_startup").map(|v| v.is_boolean()).unwrap_or(false) {
            value["check_on_startup"] = serde_json::json!(true);
        }
        if !value.get("close_to_tray").map(|v| v.is_boolean()).unwrap_or(false) {
            value["close_to_tray"] = serde_json::json!(true);
        }

        serde_json::from_value(value).unwrap_or_default()
    }

    pub fn save(&self) -> std::io::Result<()> {
        self.save_to(&settings_path())
    }

    /// Атомарная запись: временный файл в том же каталоге + `rename`.
    pub fn save_to(&self, path: &Path) -> std::io::Result<()> {
        let dir = path.parent().unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(dir)?;

        let sanitized = Settings {
            channel: if CHANNELS.contains(&self.channel.as_str()) {
                self.channel.clone()
            } else {
                DEFAULT_CHANNEL.to_owned()
            },
            ..self.clone()
        };

        let mut text = serde_json::to_string_pretty(&sanitized)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        text.push('\n');

        let tmp = dir.join(format!(".settings_{}.tmp", std::process::id()));
        {
            let mut file = fs::File::create(&tmp)?;
            file.write_all(text.as_bytes())?;
            file.sync_all()?;
        }
        match fs::rename(&tmp, path) {
            Ok(()) => Ok(()),
            Err(e) => {
                let _ = fs::remove_file(&tmp);
                Err(e)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_channel_falls_back_to_stable() {
        let dir = std::env::temp_dir().join(format!("mt-settings-{}", std::process::id()));
        let path = dir.join("settings.json");
        let s = Settings { channel: "nightly".into(), check_on_startup: true, close_to_tray: false };
        s.save_to(&path).unwrap();
        let loaded = Settings::load_from(&path);
        assert_eq!(loaded.channel, DEFAULT_CHANNEL);
        assert!(!loaded.close_to_tray);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn corrupt_file_returns_defaults() {
        let dir = std::env::temp_dir().join(format!("mt-settings-bad-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("settings.json");
        fs::write(&path, "{ not json").unwrap();
        assert_eq!(Settings::load_from(&path), Settings::default());
        let _ = fs::remove_dir_all(&dir);
    }
}
