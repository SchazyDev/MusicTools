//! Конвертация медиафайлов в mp3/wav/flac/ogg (ffmpeg).
//!
//! Перенос `core/converter.py` и `ui/convert_tab.py`: очередь файлов,
//! формат с матрицей качества, общий прогресс по всем файлам, отмена и
//! сохранение результата в выбранную папку с защитой от перезаписи.
//!
//! Ошибка одного файла не прерывает очередь: остальные конвертируются, а
//! в конце пользователь получает список неудачных файлов с причиной.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::jobs::{JobContext, JobError, JobOutcome, JobResult};
use crate::tools::download::{resolve_save_target, sanitize_file_stem};
use crate::tools::sidecar::{
    self, build_conversion_args, format_tool_error, parse_duration, parse_progress_line,
    with_progress_flags, AudioFormat, CommandSpec, ResolvedSidecar, SidecarName, StderrTail,
    STDERR_TAIL_LINES,
};

/// Расширения, которые принимает вкладка (как `ALLOWED_EXTENSIONS` в Python).
pub const ALLOWED_EXTENSIONS: [&str; 13] = [
    "mp4", "avi", "mkv", "mov", "flv", "wmv", "webm", "mp3", "wav", "flac", "aac", "ogg", "m4a",
];

/// Битрейты mp3 в кбит/с — порядок как в интерфейсе.
pub const MP3_BITRATES: [u32; 14] = [320, 256, 224, 192, 160, 128, 112, 96, 80, 64, 56, 48, 40, 32];

/// Битрейты ogg в кбит/с — порядок как в интерфейсе.
pub const OGG_BITRATES: [u32; 14] = [450, 350, 320, 256, 224, 192, 160, 128, 112, 96, 80, 64, 56, 48];

/// Разрядности wav в порядке интерфейса (сначала максимум).
pub const WAV_DEPTHS: [u32; 3] = [32, 24, 16];

/// Разрядности flac.
pub const FLAC_DEPTHS: [u32; 2] = [24, 16];

/// Уровни сжатия flac (0 — быстро, 12 — максимально).
pub const FLAC_COMPRESSION_LEVELS: u32 = 13;

// ---------------------------------------------------------------------------
// Типы
// ---------------------------------------------------------------------------

/// Выходной формат.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ConvertFormat {
    Mp3,
    Wav,
    Flac,
    Ogg,
}

impl ConvertFormat {
    pub fn code(self) -> &'static str {
        match self {
            Self::Mp3 => "mp3",
            Self::Wav => "wav",
            Self::Flac => "flac",
            Self::Ogg => "ogg",
        }
    }

    fn audio(self) -> AudioFormat {
        match self {
            Self::Mp3 => AudioFormat::Mp3,
            Self::Wav => AudioFormat::Wav,
            Self::Flac => AudioFormat::Flac,
            Self::Ogg => AudioFormat::Ogg,
        }
    }
}

/// Параметры задания из IPC.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConvertParams {
    pub files: Vec<String>,
    pub format: ConvertFormat,
    /// `"320"` для mp3/ogg, `"24"` для wav, `"24:8"` для flac.
    pub quality: String,
}

// ---------------------------------------------------------------------------
// Чистые функции
// ---------------------------------------------------------------------------

/// Поддерживается ли расширение файла.
pub fn is_allowed_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .map(|e| {
            let lower = e.to_ascii_lowercase();
            ALLOWED_EXTENSIONS.contains(&lower.as_str())
        })
        .unwrap_or(false)
}

/// Проверяет качество под формат; возвращает нормализованное значение.
pub fn normalize_quality(format: ConvertFormat, quality: &str) -> JobResult<String> {
    let raw = quality.trim();
    let valid = match format {
        ConvertFormat::Mp3 => parse_u32(raw).map(|v| MP3_BITRATES.contains(&v)).unwrap_or(false),
        ConvertFormat::Ogg => parse_u32(raw).map(|v| OGG_BITRATES.contains(&v)).unwrap_or(false),
        ConvertFormat::Wav => parse_u32(raw).map(|v| WAV_DEPTHS.contains(&v)).unwrap_or(false),
        ConvertFormat::Flac => is_valid_flac_quality(raw),
    };

    if valid {
        Ok(raw.to_owned())
    } else {
        Err(JobError::Failed(format!(
            "Недопустимое качество для {}: {raw}",
            format.code()
        )))
    }
}

fn parse_u32(raw: &str) -> Option<u32> {
    raw.parse::<u32>().ok()
}

fn is_valid_flac_quality(raw: &str) -> bool {
    let Some((depth, level)) = raw.split_once(':') else { return false };
    let Some(depth) = parse_u32(depth) else { return false };
    let Some(level) = parse_u32(level) else { return false };
    FLAC_DEPTHS.contains(&depth) && level < FLAC_COMPRESSION_LEVELS
}

/// Имя результата: исходное имя файла с новым расширением.
pub fn output_name(input: &Path, format: ConvertFormat) -> String {
    let stem = input.file_stem().and_then(|s| s.to_str()).unwrap_or("audio");
    format!("{}.{}", sanitize_file_stem(stem), format.code())
}

/// Отбирает существующие файлы с поддерживаемым расширением.
pub fn validated_files(files: &[String]) -> JobResult<Vec<PathBuf>> {
    if files.is_empty() {
        return Err(JobError::Failed("Выберите файлы для конвертации.".to_owned()));
    }

    let mut accepted = Vec::new();
    let mut rejected = Vec::new();
    for raw in files {
        let path = PathBuf::from(raw);
        if !path.is_file() {
            rejected.push(format!("файл не найден: {raw}"));
        } else if !is_allowed_extension(&path) {
            rejected.push(format!("неподдерживаемый формат: {raw}"));
        } else {
            accepted.push(path);
        }
    }

    if accepted.is_empty() {
        return Err(JobError::Failed(format!(
            "Нет файлов для конвертации:\n{}",
            rejected.join("\n")
        )));
    }
    if !rejected.is_empty() {
        log::warn!("пропущены файлы: {}", rejected.join("; "));
    }
    Ok(accepted)
}

// ---------------------------------------------------------------------------
// Задание
// ---------------------------------------------------------------------------

/// Выполняет конвертацию и возвращает каталог с готовыми файлами.
pub async fn run_job(ctx: &JobContext, params: ConvertParams) -> JobOutcome {
    match execute(ctx, &params).await {
        Ok(dir) => {
            let message = format!("Файлы сконвертированы: {dir}");
            JobOutcome::ok_with_path(message, dir)
        }
        Err(JobError::Cancelled) => JobOutcome::Canceled,
        Err(JobError::Failed(message)) => JobOutcome::failed(message),
    }
}

async fn execute(ctx: &JobContext, params: &ConvertParams) -> JobResult<String> {
    let files = validated_files(&params.files)?;
    let quality = normalize_quality(params.format, &params.quality)?;

    let ffmpeg = sidecar::resolve(SidecarName::Ffmpeg).map_err(|e| JobError::Failed(e.to_string()))?;
    let ffmpeg_path = executable_path(&ffmpeg).ok_or_else(|| {
        JobError::Failed(format!(
            "ffmpeg не найден: нужен {}",
            SidecarName::Ffmpeg.label()
        ))
    })?;
    log::info!("конвертация: {}", ffmpeg.describe());

    let temp_dir = create_temp_dir()
        .map_err(|e| JobError::Failed(format!("Не удалось создать временный каталог: {e}")))?;

    match convert_all(ctx, params.format, &quality, &ffmpeg_path, &files, &temp_dir).await {
        Ok(()) => Ok(temp_dir.display().to_string()),
        Err(error) => {
            remove_dir_quietly(&temp_dir);
            Err(error)
        }
    }
}

async fn convert_all(
    ctx: &JobContext,
    format: ConvertFormat,
    quality: &str,
    ffmpeg: &Path,
    files: &[PathBuf],
    temp_dir: &Path,
) -> JobResult<()> {
    let total = files.len();
    let mut errors: Vec<String> = Vec::new();

    for (index, input) in files.iter().enumerate() {
        ctx.check_cancel()?;
        let name = input
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("файл")
            .to_owned();
        ctx.status(format!("Обработка файла {}/{total}: {name}", index + 1));

        let requested = output_name(input, format);
        let (output, _) = resolve_save_target(temp_dir, &requested, "audio", format.code());

        match convert_one(ctx, ffmpeg, input, &output, format, quality, index, total).await {
            Ok(()) => {
                ctx.progress(((index + 1) * 100 / total) as u8);
                log::info!("готово: {}", output.display());
            }
            Err(JobError::Cancelled) => return Err(JobError::Cancelled),
            Err(JobError::Failed(message)) => {
                log::error!("файл {name} не сконвертирован: {message}");
                errors.push(format!("Не удалось конвертировать {name}: {message}"));
            }
        }
    }

    if errors.is_empty() {
        Ok(())
    } else {
        Err(JobError::Failed(format!(
            "Некоторые файлы не были сконвертированы:\n{}",
            errors.join("\n")
        )))
    }
}

/// Конвертирует один файл; локальный прогресс переводится в общий.
async fn convert_one(
    ctx: &JobContext,
    ffmpeg: &Path,
    input: &Path,
    output: &Path,
    format: ConvertFormat,
    quality: &str,
    index: usize,
    total: usize,
) -> JobResult<()> {
    let base = build_conversion_args(ffmpeg, input, output, format.audio(), quality)
        .map_err(|e| JobError::Failed(format!("не удалось собрать аргументы ffmpeg: {e}")))?;
    let argv = with_progress_flags(base);

    let mut iter = argv.into_iter();
    let Some(bin) = iter.next() else {
        return Err(JobError::Failed("пустая команда ffmpeg".to_owned()));
    };
    let spec = CommandSpec::new(bin).args(iter);
    let mut running = sidecar::Running::spawn(spec)
        .map_err(|e| JobError::Failed(format!("Не удалось запустить ffmpeg: {e}")))?;

    let mut tail = StderrTail::new(STDERR_TAIL_LINES);
    let mut duration: Option<f64> = None;
    let mut last: Option<u8> = None;

    let status = loop {
        tokio::select! {
            line = running.next_line() => match line {
                Some(line) => match line {
                    sidecar::Line::Stderr(text) => {
                        tail.push(text.clone());
                        if duration.is_none() {
                            duration = parse_duration(std::iter::once(text.as_str()));
                        }
                    }
                    sidecar::Line::Stdout(text) => {
                        if let Some(local) = parse_progress_line(&text, duration) {
                            if last.map(|value| local > value).unwrap_or(true) {
                                last = Some(local);
                                ctx.progress(overall_percent(index, total, local));
                            }
                        }
                    }
                },
                None => break running.wait().await.map_err(|e| {
                    JobError::Failed(format!("не удалось дождаться ffmpeg: {e}"))
                })?,
            },
            _ = tokio::time::sleep(Duration::from_millis(100)) => {
                if ctx.is_cancelled() {
                    running.kill_tree().await;
                    log::info!("конвертация отменена");
                    return Err(JobError::Cancelled);
                }
            }
        }
    };

    if !status.map(|code| code == 0).unwrap_or(false) {
        let message = format_tool_error("ffmpeg", status, &tail);
        return Err(JobError::Failed(message));
    }

    ctx.progress(overall_percent(index, total, 100));
    Ok(())
}

/// Локальный процент одного файла -> общий процент очереди.
fn overall_percent(index: usize, total: usize, local: u8) -> u8 {
    if total == 0 {
        return 0;
    }
    let overall = (index * 100 + usize::from(local.min(100))) / total;
    overall.min(100) as u8
}

// ---------------------------------------------------------------------------
// Сохранение результата
// ---------------------------------------------------------------------------

/// Копирует готовые файлы в выбранную папку и удаляет временный каталог.
pub async fn save_results(
    ctx: &JobContext,
    source_dir: &Path,
    destination: &str,
) -> JobResult<String> {
    let target_dir = Path::new(destination);
    if !target_dir.is_dir() {
        return Err(JobError::Failed(format!(
            "Каталог не существует: {}",
            target_dir.display()
        )));
    }

    let mut files: Vec<PathBuf> = fs::read_dir(source_dir)
        .map_err(|e| JobError::Failed(format!("Не удалось прочитать временный каталог: {e}")))?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_file())
        .collect();
    files.sort();

    if files.is_empty() {
        return Err(JobError::Failed("Нет файлов для сохранения.".to_owned()));
    }

    let total = files.len();
    for (index, source) in files.iter().enumerate() {
        ctx.check_cancel()?;
        let name = source
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| JobError::Failed("Некорректное имя файла результата.".to_owned()))?;
        let (target, _) = resolve_save_target(target_dir, name, "audio", "mp3");
        ctx.status(format!("Сохранение: {}", target.display()));

        copy_file(ctx, source, &target).await?;
        ctx.progress(((index + 1) * 100 / total) as u8);
    }

    remove_dir_quietly(source_dir);
    Ok(target_dir.display().to_string())
}

async fn copy_file(ctx: &JobContext, source: &Path, target: &Path) -> JobResult<()> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let mut input = tokio::fs::File::open(source)
        .await
        .map_err(|e| JobError::Failed(format!("Не удалось открыть {}: {e}", source.display())))?;
    let mut output = tokio::fs::File::create(target)
        .await
        .map_err(|e| JobError::Failed(format!("Не удалось создать {}: {e}", target.display())))?;

    let mut buffer = vec![0u8; 256 * 1024];
    loop {
        if ctx.is_cancelled() {
            drop(output);
            let _ = tokio::fs::remove_file(target).await;
            return Err(JobError::Cancelled);
        }
        let read = input
            .read(&mut buffer)
            .await
            .map_err(|e| JobError::Failed(format!("Ошибка чтения: {e}")))?;
        if read == 0 {
            break;
        }
        output
            .write_all(&buffer[..read])
            .await
            .map_err(|e| JobError::Failed(format!("Ошибка записи: {e}")))?;
    }

    output
        .flush()
        .await
        .map_err(|e| JobError::Failed(format!("Ошибка записи: {e}")))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Служебные
// ---------------------------------------------------------------------------

fn executable_path(resolved: &ResolvedSidecar) -> Option<PathBuf> {
    match &resolved.program {
        sidecar::Program::Executable(path) => Some(path.clone()),
        sidecar::Program::PythonModule { .. } => None,
    }
}

/// Временный каталог `audio_convert_<pid>_<номер>`.
fn create_temp_dir() -> io::Result<PathBuf> {
    let base = std::env::temp_dir();
    for attempt in 0..64u32 {
        let candidate = base.join(format!("audio_convert_{}_{attempt}", std::process::id()));
        match fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "не удалось создать временный каталог",
    ))
}

fn remove_dir_quietly(dir: &Path) {
    if dir.exists() {
        if let Err(e) = fs::remove_dir_all(dir) {
            log::warn!("не удалось удалить {}: {e}", dir.display());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_known_extensions_case_insensitively() {
        assert!(is_allowed_extension(Path::new("song.MP3")));
        assert!(is_allowed_extension(Path::new("video.mkv")));
        assert!(!is_allowed_extension(Path::new("document.txt")));
        assert!(!is_allowed_extension(Path::new("no_extension")));
    }

    #[test]
    fn validates_quality_per_format() {
        assert_eq!(normalize_quality(ConvertFormat::Mp3, "320").expect("mp3"), "320");
        assert!(normalize_quality(ConvertFormat::Mp3, "300").is_err());
        assert!(normalize_quality(ConvertFormat::Ogg, "450").is_ok());
        assert!(normalize_quality(ConvertFormat::Ogg, "320").is_ok());
        assert!(normalize_quality(ConvertFormat::Wav, "16").is_ok());
        assert!(normalize_quality(ConvertFormat::Wav, "8").is_err());
        assert!(normalize_quality(ConvertFormat::Flac, "24:12").is_ok());
        assert!(normalize_quality(ConvertFormat::Flac, "24:13").is_err());
        assert!(normalize_quality(ConvertFormat::Flac, "32:0").is_err());
    }

    #[test]
    fn output_name_keeps_stem_and_changes_extension() {
        assert_eq!(
            output_name(Path::new("C:/music/My Song.flac"), ConvertFormat::Mp3),
            "My Song.mp3"
        );
        assert_eq!(
            output_name(Path::new("C:/music/weird:name?.wav"), ConvertFormat::Ogg),
            "weird_name_.ogg"
        );
    }

    #[test]
    fn overall_progress_spans_the_queue() {
        assert_eq!(overall_percent(0, 2, 0), 0);
        assert_eq!(overall_percent(0, 2, 100), 50);
        assert_eq!(overall_percent(1, 2, 0), 50);
        assert_eq!(overall_percent(1, 2, 100), 100);
        assert_eq!(overall_percent(2, 3, 50), 83);
    }

    #[test]
    fn empty_selection_is_rejected() {
        let error = validated_files(&[]).expect_err("пустой список");
        assert!(error.to_string().contains("Выберите файлы"));
    }
}
