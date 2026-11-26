//! Вспомогательные команды для будущих инструментов (metadata, bpm, stems).
//!
//! Модуль пуст на этапе скелета и существует, чтобы зафиксировать точку
//! расширения: новый инструмент добавляет сюда свои команды `start_*` и
//! `preview_*`, а вкладку — одной записью в `tools_registry::builtin_tools`.

pub mod convert;
pub mod download;
pub mod sidecar;
pub mod ytdlp_update;
