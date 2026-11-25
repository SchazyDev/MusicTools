//! Реестр инструментов — серверная часть UI.
//!
//! Прямой перенос `core/tools/base.py::ToolDescriptor` и
//! `core/tools/registry.py::ToolRegistry`: дескриптор описывает вкладку
//! (id, заголовок, порядок, иконка, вид рабочей области), а список описаний
//! сериализуется в JSON и отдаётся фронтенду через команду `get_tools`.
//!
//! Добавление нового инструмента = одна запись в [`builtin_tools`] плюс один
//! модуль Svelte под `src/tools/`. Ни команды, ни события не меняются.

use std::collections::BTreeMap;

use serde::Serialize;

/// Вид рабочей области: фронтенд сопоставляет его с Svelte-компонентом
/// в `src/lib/panes.ts`. В PyQt это был `factory: Callable[[], QWidget]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ToolKind {
    /// Скачивание по ссылке (yt-dlp).
    Download,
    /// Конвертация локальных файлов (ffmpeg).
    Convert,
    /// Сведения о приложении, обновление, ссылки.
    About,
}

/// Описание инструмента, как его видит UI. Имена полей — snake_case,
/// чтобы совпадать с Rust-сериализацией по умолчанию.
#[derive(Debug, Clone, Serialize)]
pub struct ToolDescriptor {
    /// Уникальный слаг `^[a-z0-9-]+$`.
    pub id: String,
    /// Русскоязычный заголовок вкладки.
    pub title: String,
    /// Порядок сортировки; меньше — левее.
    pub order: i32,
    /// Показывать ли вкладку.
    pub enabled: bool,
    /// Имя ресурса иконки (опционально).
    pub icon: Option<String>,
    /// Вид рабочей области.
    pub kind: ToolKind,
    /// Подсказка для вкладки на русском.
    pub description: String,
}

impl ToolDescriptor {
    fn new(id: &str, title: &str, order: i32, kind: ToolKind, description: &str) -> Self {
        Self {
            id: id.to_owned(),
            title: title.to_owned(),
            order,
            enabled: true,
            icon: None,
            kind,
            description: description.to_owned(),
        }
    }
}

/// Ошибки реестра — зеркало проверок `ToolDescriptor.__post_init__`.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum RegistryError {
    #[error("идентификатор инструмента {0:?} не соответствует ^[a-z0-9-]+$")]
    InvalidId(String),
    #[error("заголовок инструмента {0:?} пуст")]
    EmptyTitle(String),
    #[error("инструмент с идентификатором {0:?} уже зарегистрирован")]
    Duplicate(String),
}

/// Коллекция дескрипторов, упорядоченная по (order, title).
#[derive(Debug, Default)]
pub struct ToolRegistry {
    tools: BTreeMap<String, ToolDescriptor>,
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self { tools: BTreeMap::new() }
    }

    fn validate(descriptor: &ToolDescriptor) -> Result<(), RegistryError> {
        let id = &descriptor.id;
        let valid_id = !id.is_empty()
            && id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
        if !valid_id {
            return Err(RegistryError::InvalidId(id.clone()));
        }
        if descriptor.title.trim().is_empty() {
            return Err(RegistryError::EmptyTitle(id.clone()));
        }
        Ok(())
    }

    pub fn register(&mut self, descriptor: ToolDescriptor) -> Result<(), RegistryError> {
        Self::validate(&descriptor)?;
        if self.tools.contains_key(&descriptor.id) {
            return Err(RegistryError::Duplicate(descriptor.id));
        }
        self.tools.insert(descriptor.id.clone(), descriptor);
        Ok(())
    }

    /// Все дескрипторы, отсортированные по (order, title).
    pub fn all(&self) -> Vec<&ToolDescriptor> {
        let mut list: Vec<&ToolDescriptor> = self.tools.values().collect();
        list.sort_by(|a, b| a.order.cmp(&b.order).then_with(|| a.title.cmp(&b.title)));
        list
    }

    /// Только включённые дескрипторы — их видит UI.
    pub fn enabled(&self) -> Vec<&ToolDescriptor> {
        self.all().into_iter().filter(|d| d.enabled).collect()
    }

    pub fn get(&self, id: &str) -> Option<&ToolDescriptor> {
        self.tools.get(id)
    }

    pub fn len(&self) -> usize {
        self.tools.len()
    }

    pub fn is_empty(&self) -> bool {
        self.tools.is_empty()
    }
}

/// Регистрирует штатные инструменты. Здесь и только здесь перечисляются
/// вкладки; UI их не хардкодит.
pub fn builtin_tools() -> Result<ToolRegistry, RegistryError> {
    let mut registry = ToolRegistry::new();
    registry.register(ToolDescriptor::new(
        "download",
        "Скачивание",
        10,
        ToolKind::Download,
        "Загрузка аудио и видео по ссылке",
    ))?;
    registry.register(ToolDescriptor::new(
        "convert",
        "Конвертация",
        20,
        ToolKind::Convert,
        "Перекодирование локальных файлов и папок",
    ))?;
    registry.register(ToolDescriptor::new(
        "about",
        "О программе",
        90,
        ToolKind::About,
        "Сведения о версии, журнал и ссылки",
    ))?;
    // Фаза 3 (metadata, bpm, stems) добавляется сюда же: вкладка появится
    // в UI без изменений во фронтенде.
    Ok(registry)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtins_are_ordered_and_unique() {
        let reg = builtin_tools().expect("встроенные инструменты валидны");
        let ids: Vec<&str> = reg.enabled().iter().map(|d| d.id.as_str()).collect();
        assert_eq!(ids, ["download", "convert", "about"]);
    }

    #[test]
    fn invalid_and_duplicate_ids_are_rejected() {
        let mut reg = ToolRegistry::new();
        assert!(reg.register(ToolDescriptor::new("Bad_ID", "X", 1, ToolKind::About, "")).is_err());
        reg.register(ToolDescriptor::new("x", "X", 1, ToolKind::About, "")).unwrap();
        assert_eq!(
            reg.register(ToolDescriptor::new("x", "X", 2, ToolKind::About, "")).unwrap_err(),
            RegistryError::Duplicate("x".into())
        );
    }
}
