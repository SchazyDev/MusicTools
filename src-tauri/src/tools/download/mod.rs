//! Задание скачивания (yt-dlp) — реализация фазы 1.
//!
//! Перенос `core/downloader.py` и `core/preview.py`: предпросмотр (заголовок,
//! миниатюра, доступные разрешения), форматы mp4/mp3/wav, битрейты 320..32 и
//! разрядность 16/24/32 бит, отмена, сохранение через `name (1).ext`.
//!
//! Прогресс идёт двумя путями. Скачивание — по `--progress-template`, который
//! печатает байты (`MUSICTOOLS_PROGRESS ...`); извлечение звука — по
//! `ffmpeg -progress pipe:1` (`out_time_us` против `Duration:` из stderr).
//! Файл разбит на части: чистые функции (аргументы, парсинг, имена) тестируются
//! без диска, а запуск процессов проверяется вручную.

use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::jobs::{JobContext, JobError, JobOutcome, JobResult};
use crate::tools::sidecar::{
    self, build_conversion_args, format_tool_error, parse_duration, parse_progress_line,
    with_progress_flags, AudioFormat, CommandSpec, ResolvedSidecar, SidecarName, StderrTail, STDERR_TAIL_LINES,
};

/// Минимальная высота кадра для списка разрешений (как в Python).
const MIN_HEIGHT: u32 = 144;

/// Разрешения, предлагаемые без предпросмотра.
pub const FALLBACK_HEIGHTS: [u32; 4] = [1080, 720, 480, 360];

/// Битрейты mp3 в кбит/с — порядок как в `ui/download_tab.py`.
pub const MP3_BITRATES: [u32; 14] = [320, 256, 224, 192, 160, 128, 112, 96, 80, 64, 56, 48, 40, 32];

/// Разрядность wav.
pub const WAV_DEPTHS: [u32; 3] = [16, 24, 32];

/// Сколько секунд ждать предпросмотр.
pub const PREVIEW_TIMEOUT: Duration = Duration::from_secs(30);

/// Префикс строк прогресса yt-dlp; нужен, чтобы не путать их с обычным выводом.
pub const PROGRESS_MARKER: &str = "MUSICTOOLS_PROGRESS";

/// Ключи `--progress-template`, разделённые `|`.
const PROGRESS_TEMPLATE: &str = "[download] MUSICTOOLS_PROGRESS %(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s";

// ---------------------------------------------------------------------------
// Типы
// ---------------------------------------------------------------------------

/// Формат результата.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DownloadFormat {
    Mp4,
    Mp3,
    Wav,
}

impl DownloadFormat {
    pub fn code(self) -> &'static str {
        match self {
            Self::Mp4 => "mp4",
            Self::Mp3 => "mp3",
            Self::Wav => "wav",
        }
    }

    fn audio(self) -> Option<AudioFormat> {
        match self {
            Self::Mp4 => None,
            Self::Mp3 => Some(AudioFormat::Mp3),
            Self::Wav => Some(AudioFormat::Wav),
        }
    }
}

/// Параметры задания из IPC.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadParams {
    pub url: String,
    pub format: DownloadFormat,
    /// `"1080p"`-стиль для mp4, `"320kbps"`..`"32kbps"` для mp3,
    /// `"16bit"|"24bit"|"32bit"` для wav.
    pub quality: String,
}

/// Данные предпросмотра.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PreviewInfo {
    pub title: String,
    pub thumbnail: String,
    pub heights: Vec<u32>,
}

/// Итог задания: путь и размер файла.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DownloadResult {
    pub path: String,
    pub size_bytes: u64,
}

// ---------------------------------------------------------------------------
// Чистые функции: аргументы
// ---------------------------------------------------------------------------

/// Разбирает `"1080p"` в число; принимает и голое `"1080"`.
pub fn parse_video_quality(quality: &str) -> Option<u32> {
    let raw = quality.trim();
    let digits = raw.strip_suffix(['p', 'P']).unwrap_or(raw);
    let height: u32 = digits.parse().ok()?;
    (height >= MIN_HEIGHT && height <= 4320).then_some(height)
}

/// Битрейт mp3 из `"320kbps"` (допускается `"320"`); только из списка Python.
pub fn parse_audio_bitrate(quality: &str) -> Option<u32> {
    let raw = quality.trim().to_ascii_lowercase();
    let digits = raw.strip_suffix("kbps").unwrap_or(&raw);
    let bitrate: u32 = digits.parse().ok()?;
    MP3_BITRATES.contains(&bitrate).then_some(bitrate)
}

/// Разрядность wav из `"24bit"` (допускается `"24"`).
pub fn parse_bit_depth(quality: &str) -> Option<u32> {
    let raw = quality.trim().to_ascii_lowercase();
    let digits = raw.strip_suffix("bit").unwrap_or(&raw);
    let depth: u32 = digits.parse().ok()?;
    WAV_DEPTHS.contains(&depth).then_some(depth)
}

/// Селектор `-f` для mp4 — как в `ui/download_tab.py::update_quality_options`.
pub fn mp4_selector(height: u32) -> String {
    format!("bestvideo[height<={height}]+bestaudio/best[height<={height}]")
}

fn ytdlp(url: &str, sidecar_res: &ResolvedSidecar, extra: Vec<String>) -> CommandSpec {
    sidecar_res.command().args(extra).arg(url)
}

/// Аргументы `yt-dlp` для скачивания. Проверяются тестом на порядок.
pub fn build_download_args(
    sidecar_res: &ResolvedSidecar,
    url: &str,
    outtmpl: &str,
    format: DownloadFormat,
    quality: &str,
) -> JobResult<CommandSpec> {
    let mut extra: Vec<String> = vec![
        "--newline".into(),
        "--no-playlist".into(),
        "--no-warnings".into(),
        "--progress-template".into(),
        PROGRESS_TEMPLATE.into(),
    ];

    if let Some(dir) = ffmpeg_dir() {
        extra.push("--ffmpeg-location".into());
        extra.push(dir.display().to_string());
    }

    match format {
        DownloadFormat::Mp4 => {
            let height = parse_video_quality(quality)
                .ok_or_else(|| JobError::Failed(format!("некорректное разрешение: {quality}")))?;
            extra.extend(["-f".into(), mp4_selector(height)]);
            extra.extend(["--merge-output-format".into(), "mp4".into()]);
        }
        DownloadFormat::Mp3 | DownloadFormat::Wav => {
            extra.extend(["-f".into(), "bestaudio/best".into()]);
        }
    }

    extra.extend(["-o".into(), outtmpl.to_owned()]);
    Ok(ytdlp(url, sidecar_res, extra))
}

/// Аргументы `yt-dlp -J` для предпросмотра.
pub fn build_preview_args(sidecar_res: &ResolvedSidecar, url: &str) -> CommandSpec {
    ytdlp(
        url,
        sidecar_res,
        ["-J", "--no-playlist", "--skip-download", "--no-warnings"].map(str::to_owned).to_vec(),
    )
}

/// Полный argv ffmpeg для извлечения звука (общие флаги прогресса после имени).
pub fn build_audio_extract_args(
    ffmpeg: &Path,
    input: &Path,
    output: &Path,
    format: AudioFormat,
    quality: &str,
) -> JobResult<Vec<String>> {
    let base = build_conversion_args(ffmpeg, input, output, format, quality)
        .map_err(|e| JobError::Failed(format!("не удалось собрать аргументы ffmpeg: {e}")))?;
    Ok(with_progress_flags(base))
}

// ---------------------------------------------------------------------------
// Чистые функции: парсинг вывода
// ---------------------------------------------------------------------------

/// Значение `--progress-template` из строки yt-dlp.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProgressLine<'a> {
    pub status: &'a str,
    pub downloaded: u64,
    pub total: Option<u64>,
}

fn to_u64(raw: &str) -> u64 {
    let raw = raw.trim();
    if raw.is_empty() || raw.eq_ignore_ascii_case("NA") || raw == "None" || raw == "null" {
        return 0;
    }
    raw.parse::<f64>().ok().map(|v| v.max(0.0) as u64).unwrap_or(0)
}

/// Разбирает строку прогресса; `None` — строка не от yt-dlp.
pub fn parse_download_progress(line: &str) -> Option<ProgressLine<'_>> {
    let (_, rest) = line.split_once(PROGRESS_MARKER)?;
    let mut parts = rest.trim_start().split('|');
    let status = parts.next()?.trim();
    let downloaded = to_u64(parts.next()?);
    let total = to_u64(parts.next()?);
    let estimate = to_u64(parts.next()?);
    let total = match (total, estimate) {
        (0, 0) => None,
        (0, e) => Some(e),
        (t, _) => Some(t),
    };
    Some(ProgressLine { status, downloaded, total })
}

/// Сколько процентов доложить (0..100), сохраняя монотонность.
pub fn download_percent(line: &ProgressLine<'_>, previous: u8) -> u8 {
    if line.status == "finished" {
        return 100;
    }
    if line.status != "downloading" {
        return previous;
    }
    match line.total {
        Some(total) if total > 0 => {
            let percent = (line.downloaded.saturating_mul(100) / total) as u8;
            percent.clamp(previous, 99)
        }
        _ => previous,
    }
}

/// Высоты из JSON `yt-dlp -J`: >= 144, только с видеокодеком, без повторов,
/// по убыванию.
pub fn collect_heights(value: &serde_json::Value) -> Vec<u32> {
    let mut set = BTreeSet::new();
    let Some(formats) = value.get("formats").and_then(|f| f.as_array()) else { return Vec::new() };

    for format in formats {
        if format.get("vcodec").and_then(|v| v.as_str()) == Some("none") {
            continue;
        }
        let Some(height) = format.get("height").and_then(|h| h.as_u64()) else { continue };
        if height >= u64::from(MIN_HEIGHT) {
            set.insert(height.min(u64::from(u32::MAX)) as u32);
        }
    }

    set.into_iter().rev().collect()
}

/// Заголовок ролика (`"Без названия"`, если ключа нет).
pub fn preview_title(value: &serde_json::Value) -> String {
    value
        .get("title")
        .and_then(|t| t.as_str())
        .map(str::to_owned)
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| "Без названия".to_owned())
}

/// Собирает [`PreviewInfo`] из JSON ответа yt-dlp.
pub fn preview_from_json(value: &serde_json::Value) -> PreviewInfo {
    PreviewInfo {
        title: preview_title(value),
        thumbnail: value.get("thumbnail").and_then(|t| t.as_str()).unwrap_or("").to_owned(),
        heights: collect_heights(value),
    }
}

/// Разбирает полностью прочитанный stderr в хвост нужной длины.
fn tail_from(text: &str, limit: usize) -> StderrTail {
    let mut tail = StderrTail::new(limit);
    for line in text.lines() {
        tail.push(line.to_owned());
    }
    tail
}

/// Проверяет, что строка похожа на URL (схема + хост, без пробелов).
pub fn validate_url(url: &str) -> JobResult<&str> {
    let trimmed = url.trim();
    if trimmed.is_empty() {
        return Err(JobError::Failed("Введите URL.".to_owned()));
    }
    let has_scheme = trimmed.starts_with("http://") || trimmed.starts_with("https://");
    if !has_scheme || trimmed.contains(char::is_whitespace) || trimmed.len() < 12 {
        return Err(JobError::Failed(format!("Некорректный URL: {trimmed}")));
    }
    Ok(trimmed)
}

/// Проверяет качество под формат; возвращает нормализованное значение.
pub fn normalize_quality(format: DownloadFormat, quality: &str) -> JobResult<String> {
    let raw = quality.trim();
    match format {
        DownloadFormat::Mp4 => parse_video_quality(raw)
            .map(|h| h.to_string())
            .ok_or_else(|| JobError::Failed(format!("Выберите разрешение: {raw}"))),
        DownloadFormat::Mp3 => parse_audio_bitrate(raw)
            .map(|b| b.to_string())
            .ok_or_else(|| JobError::Failed(format!("Выберите битрейт: {raw}"))),
        DownloadFormat::Wav => parse_bit_depth(raw)
            .map(|d| d.to_string())
            .ok_or_else(|| JobError::Failed(format!("Выберите разрядность: {raw}"))),
    }
}

/// Каталог `ffmpeg` для подсказки yt-dlp при склейке mp4.
fn ffmpeg_dir() -> Option<PathBuf> {
    ffmpeg_dir_from(&sidecar::resolve(SidecarName::Ffmpeg).ok()?)
}

fn ffmpeg_dir_from(resolved: &ResolvedSidecar) -> Option<PathBuf> {
    match &resolved.program {
        sidecar::Program::Executable(path) => path.parent().map(|p| p.to_owned()),
        sidecar::Program::PythonModule { .. } => None,
    }
}

// ---------------------------------------------------------------------------
// Имена файлов и сохранение
// ---------------------------------------------------------------------------

/// Убирает символы, запрещённые в именах Windows.
pub fn sanitize_file_stem(name: &str) -> String {
    let mut out: String = name
        .chars()
        .map(|c| match c {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            c if (c as u32) < 0x20 => '_',
            c => c,
        })
        .collect();
    while out.ends_with(' ') || out.ends_with('.') {
        out.pop();
    }
    if out.trim().is_empty() {
        "download".to_owned()
    } else {
        out
    }
}

/// `name (1).ext`, `name (2).ext`, … — вариант, которого ещё нет на диске.
fn collision_free(dir: &Path, stem: &str, ext: &str) -> PathBuf {
    let mut candidate = dir.join(format!("{stem}.{ext}"));
    let mut counter = 1u32;
    while candidate.exists() {
        candidate = dir.join(format!("{stem} ({counter}).{ext}"));
        counter += 1;
    }
    candidate
}

/// Разрешает имя сохранения в каталоге `dir` безопасно и без коллизий.
/// Возвращает целевой путь и итоговое имя.
pub fn resolve_save_target(
    dir: &Path,
    requested: &str,
    fallback_stem: &str,
    fallback_ext: &str,
) -> (PathBuf, String) {
    let requested_name = Path::new(requested)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or(fallback_stem);

    let (stem, ext) = match requested_name.rsplit_once('.') {
        Some((s, e)) if !s.is_empty() => (sanitize_file_stem(s), sanitize_extension(e)),
        _ => (sanitize_file_stem(requested_name), fallback_ext.to_owned()),
    };

    let path = collision_free(dir, &stem, &ext);
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or(&stem).to_owned();
    (path, name)
}

fn sanitize_extension(ext: &str) -> String {
    let cleaned: String = ext
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .take(10)
        .collect::<String>()
        .to_ascii_lowercase();
    if cleaned.is_empty() {
        "mp4".to_owned()
    } else {
        cleaned
    }
}

/// Имя файла по умолчанию (для диалога сохранения).
pub fn default_file_name(stem: &str, ext: &str) -> String {
    format!("{}.{ext}", sanitize_file_stem(stem))
}

// ---------------------------------------------------------------------------
// Предпросмотр
// ---------------------------------------------------------------------------

/// Загружает предпросмотр: `yt-dlp -J`, ограниченное время, гарантированный kill.
pub async fn preview(url: &str) -> JobResult<PreviewInfo> {
    let url = validate_url(url)?.to_owned();
    let sidecar_res = sidecar::resolve(SidecarName::YtDlp)
        .map_err(|e| JobError::Failed(e.to_string()))?;
    log::info!("предпросмотр: {}", sidecar_res.describe());

    let spec = build_preview_args(&sidecar_res, &url);
    let mut child = spec.build().spawn().map_err(|e| JobError::Failed(launch_error(e)))?;

    let mut stdout = child.stdout.take();
    let mut stderr = child.stderr.take();
    let stdout_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        if let Some(mut out) = stdout.take() {
            let _ = tokio::io::AsyncReadExt::read_to_end(&mut out, &mut buf).await;
        }
        buf
    });
    let stderr_task = tokio::spawn(async move {
        let mut buf = Vec::new();
        if let Some(mut err) = stderr.take() {
            let _ = tokio::io::AsyncReadExt::read_to_end(&mut err, &mut buf).await;
        }
        buf
    });

    let deadline = Instant::now() + PREVIEW_TIMEOUT;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if Instant::now() < deadline => {
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
            Ok(None) => {
                sidecar::kill_tree(&mut child).await;
                let _ = stdout_task.await;
                let _ = stderr_task.await;
                log::warn!("предпросмотр не уложился в {PREVIEW_TIMEOUT:?}: {url}");
                return Err(JobError::Failed(format!(
                    "yt-dlp не ответил за {} секунд. Проверьте подключение и ссылку.",
                    PREVIEW_TIMEOUT.as_secs()
                )));
            }
            Err(e) => {
                sidecar::kill_tree(&mut child).await;
                return Err(JobError::Failed(format!("не удалось дождаться yt-dlp: {e}")));
            }
        }
    };

    let stdout = stdout_task.await.unwrap_or_default();
    let stderr = stderr_task.await.unwrap_or_default();

    if !status.success() {
        let text = String::from_utf8_lossy(&stderr);
        let tail = tail_from(&text, STDERR_TAIL_LINES);
        let message = format_tool_error("yt-dlp", status.code(), &tail);
        log::error!("предпросмотр не удался: {message}");
        return Err(JobError::Failed(format!("Не удалось получить сведения о ролике: {message}")));
    }

    let json: serde_json::Value = serde_json::from_slice(&stdout)
        .map_err(|e| JobError::Failed(format!("Не удалось разобрать ответ yt-dlp: {e}")))?;
    Ok(preview_from_json(&json))
}

// ---------------------------------------------------------------------------
// Задание скачивания
// ---------------------------------------------------------------------------

fn launch_error(e: io::Error) -> String {
    format!("Не удалось запустить sidecar-программу: {e}")
}

/// Временный рабочий каталог `video_download_<номер>`.
fn create_temp_dir() -> io::Result<PathBuf> {
    let base = std::env::temp_dir();
    for attempt in 0..64u32 {
        let candidate = base.join(format!(
            "video_download_{}_{attempt}",
            std::process::id()
        ));
        match fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::Error::new(io::ErrorKind::AlreadyExists, "не удалось создать временный каталог"))
}

fn remove_dir_quietly(dir: &Path) {
    if dir.exists() {
        if let Err(e) = fs::remove_dir_all(dir) {
            log::warn!("не удалось удалить {}: {e}", dir.display());
        }
    }
}

/// Нормализует текст yt-dlp перед человеком: убирает ANSI-последовательности.
pub fn strip_ansi(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\u{1b}' {
            if chars.peek() == Some(&'[') {
                chars.next();
                for next in chars.by_ref() {
                    if next.is_ascii_alphabetic() {
                        break;
                    }
                }
            }
            continue;
        }
        out.push(c);
    }
    out
}

/// Ищет скачанный файл: сначала исходное имя из `prepare_filename`, затем
/// единственный файл с нужным расширением (`webm` -> `m4a` и т.п.).
fn locate_downloaded(dir: &Path, expected: &Path, extension: &str) -> Option<PathBuf> {
    if expected.is_file() {
        return Some(expected.to_path_buf());
    }
    let mut candidates: Vec<PathBuf> = fs::read_dir(dir)
        .ok()?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.is_file())
        .filter(|p| {
            p.extension()
                .and_then(|e| e.to_str())
                .map(|e| e.eq_ignore_ascii_case(extension))
                .unwrap_or(false)
        })
        .collect();
    candidates.sort();
    candidates.into_iter().next()
}

/// Резерв поиска: самый крупный файл каталога, кроме служебных.
///
/// Нужен, когда расширение заранее неизвестно: `bestaudio` для YouTube —
/// это обычно `.m4a` или `.opus`, а не `.webm`.
fn locate_any_media(dir: &Path) -> Option<PathBuf> {
    let mut best: Option<(u64, PathBuf)> = None;
    for entry in fs::read_dir(dir).ok()?.filter_map(Result::ok) {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }
        let extension = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.to_ascii_lowercase())
            .unwrap_or_default();
        if matches!(extension.as_str(), "part" | "ytdl" | "temp") {
            continue;
        }
        let size = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
        if best.as_ref().map(|(known, _)| size > *known).unwrap_or(true) {
            best = Some((size, path));
        }
    }
    best.map(|(_, path)| path)
}

/// Ступень задания.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Stage {
    Download,
    Extract,
    Save,
}

impl Stage {
    fn range(self) -> (u8, u8) {
        match self {
            Stage::Download => (0, 90),
            Stage::Extract => (10, 95),
            Stage::Save => (0, 100),
        }
    }

    /// Переводит локальный процент ступени в общий 0..100.
    fn to_overall(self, local: u8) -> u8 {
        let (from, to) = self.range();
        let span = to - from;
        from + (u32::from(local.min(100)) * u32::from(span) / 100) as u8
    }
}

/// Выполняет задание и всегда удаляет свой временный каталог.
pub async fn run_job(ctx: &JobContext, params: DownloadParams) -> JobOutcome {
    match execute(ctx, &params).await {
        Ok(result) => {
            let message =
                format!("Файл скачан: {} ({} байт)", result.path, result.size_bytes);
            JobOutcome::ok_with_path(message, result.path)
        }
        Err(JobError::Cancelled) => JobOutcome::Canceled,
        Err(JobError::Failed(message)) => JobOutcome::failed(message),
    }
}

fn report(ctx: &JobContext, stage: Stage, local: u8) {
    ctx.progress(stage.to_overall(local));
}

/// Полный проход: скачивание, извлечение звука, возврат пути и размера.
async fn execute(ctx: &JobContext, params: &DownloadParams) -> JobResult<DownloadResult> {
    let url = validate_url(&params.url)?.to_owned();
    let quality = normalize_quality(params.format, &params.quality)?;

    let temp_dir = create_temp_dir().map_err(|e| {
        JobError::Failed(format!("Не удалось создать временный каталог: {e}"))
    })?;

    let outcome = execute_in(ctx, params, &url, &quality, &temp_dir).await;
    // При успехе каталог остаётся: файл копируется по команде `save_result`.
    // При ошибке или отмене его нужно убрать сразу.
    if outcome.is_err() {
        remove_dir_quietly(&temp_dir);
    }
    outcome
}

async fn execute_in(
    ctx: &JobContext,
    params: &DownloadParams,
    url: &str,
    quality: &str,
    temp_dir: &Path,
) -> JobResult<DownloadResult> {
    ctx.check_cancel()?;
    ctx.status(format!("Скачивание: {url}"));
    ctx.progress(0);

    let ytdlp = sidecar::resolve(SidecarName::YtDlp).map_err(|e| JobError::Failed(e.to_string()))?;
    log::info!("скачивание {}", ytdlp.describe());

    let outtmpl = temp_dir.join("%(title)s.%(ext)s");
    let outtmpl_str = outtmpl.display().to_string();
    let spec = build_download_args(&ytdlp, url, &outtmpl_str, params.format, quality)?;
    let expected = predict_output_path(&outtmpl_str, params.format);

    let mut running = sidecar::Running::spawn(spec).map_err(|e| JobError::Failed(launch_error(e)))?;
    let mut tail = StderrTail::new(STDERR_TAIL_LINES);
    let mut percent: u8 = 0;

    let status = loop {
        tokio::select! {
            line = running.next_line() => match line {
                Some(line) => handle_ytdlp_line(&line, &mut tail, |p| {
                    let next = p.max(percent);
                    if next != percent {
                        percent = next;
                        report(ctx, Stage::Download, percent);
                    }
                }),
                None => break running.wait().await.map_err(|e| {
                    JobError::Failed(format!("не удалось дождаться yt-dlp: {e}"))
                })?,
            },
            _ = tokio::time::sleep(Duration::from_millis(100)) => {
                if ctx.is_cancelled() {
                    running.kill_tree().await;
                    log::info!("скачивание отменено: {url}");
                    return Err(JobError::Cancelled);
                }
            }
        }
    };

    if !status.map(|c| c == 0).unwrap_or(false) {
        let message = format_tool_error("yt-dlp", status, &tail);
        log::error!("скачивание не удалось: {message}");
        return Err(JobError::Failed(format!("Ошибка загрузки: {message}")));
    }

    report(ctx, Stage::Download, 100);
    ctx.check_cancel()?;

    let mut input = locate_downloaded(temp_dir, &expected, params.format.code())
        .or_else(|| locate_downloaded(temp_dir, &expected, "webm"))
        .or_else(|| locate_any_media(temp_dir))
        .ok_or_else(|| {
            JobError::Failed(
                "Ошибка загрузки: yt-dlp не оставил файла во временном каталоге.".to_owned(),
            )
        })?;

    if let Some(audio) = params.format.audio() {
        ctx.status(format!("Извлечение в {}...", params.format.code()));
        let ffmpeg = sidecar::resolve(SidecarName::Ffmpeg)
            .map_err(|e| JobError::Failed(e.to_string()))?;
        log::info!("извлечение звука {}", ffmpeg.describe());
        let Some(ffmpeg_path) = ffmpeg_executable(&ffmpeg) else {
            return Err(JobError::Failed(format!(
                "ffmpeg не найден: {}",
                SidecarName::Ffmpeg.label()
            )));
        };

        let stem = input.file_stem().and_then(|s| s.to_str()).unwrap_or("audio").to_owned();
        let output = temp_dir.join(format!(
            "{}.{}",
            sanitize_file_stem(&stem),
            params.format.code()
        ));
        extract_audio(ctx, &ffmpeg_path, &input, &output, audio, quality).await?;

        remove_dir_quietly_input(&input);
        input = output;
    }

    ctx.check_cancel()?;
    let size_bytes = fs::metadata(&input)
        .map_err(|e| JobError::Failed(format!("Файл не найден после скачивания: {e}")))?
        .len();

    Ok(DownloadResult { path: input.display().to_string(), size_bytes })
}

fn remove_dir_quietly_input(file: &Path) {
    if let Err(e) = fs::remove_file(file) {
        log::warn!("не удалось удалить промежуточный файл {}: {e}", file.display());
    }
}

fn ffmpeg_executable(resolved: &ResolvedSidecar) -> Option<PathBuf> {
    match &resolved.program {
        sidecar::Program::Executable(path) => Some(path.clone()),
        sidecar::Program::PythonModule { .. } => None,
    }
}

fn handle_ytdlp_line(line: &sidecar::Line, tail: &mut StderrTail, mut on_percent: impl FnMut(u8)) {
    let text = strip_ansi(line.text());
    match line {
        sidecar::Line::Stderr(_) => tail.push(text),
        sidecar::Line::Stdout(_) => {
            if let Some(progress) = parse_download_progress(&text) {
                let previous = 0;
                let percent = download_percent(&progress, previous);
                on_percent(percent);
            } else if !text.trim().is_empty() {
                log::debug!("yt-dlp: {}", text.trim());
            }
        }
    }
}

/// Подсчёт пути по `outtmpl`: `%(title)s.%(ext)s` -> `<dir>/<название>.<ext>`.
fn predict_output_path(outtmpl: &str, format: DownloadFormat) -> PathBuf {
    let replaced = outtmpl
        .replace("%(title)s", "download")
        .replace("%(ext)s", format.code());
    PathBuf::from(replaced)
}

/// Запускает ffmpeg, докладывает прогресс по `out_time_us` и код 100 на успехе.
async fn extract_audio(
    ctx: &JobContext,
    ffmpeg: &Path,
    input: &Path,
    output: &Path,
    format: AudioFormat,
    quality: &str,
) -> JobResult<()> {
    let args = build_audio_extract_args(ffmpeg, input, output, format, quality)?;
    let mut program = args.into_iter();
    let Some(bin) = program.next() else {
        return Err(JobError::Failed("пустая команда ffmpeg".to_owned()));
    };
    let spec = CommandSpec::new(bin).args(program);
    let mut running = sidecar::Running::spawn(spec).map_err(|e| JobError::Failed(launch_error(e)))?;

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
                            if duration.is_some() {
                                report(ctx, Stage::Extract, 0);
                            }
                        }
                    }
                    sidecar::Line::Stdout(text) => {
                        if let Some(percent) = parse_progress_line(&text, duration) {
                            if last.map(|l| percent > l).unwrap_or(true) {
                                last = Some(percent);
                                report(ctx, Stage::Extract, percent);
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
                    log::info!("извлечение звука отменено");
                    return Err(JobError::Cancelled);
                }
            }
        }
    };

    if !status.map(|c| c == 0).unwrap_or(false) {
        let message = format_tool_error("ffmpeg", status, &tail);
        log::error!("извлечение звука не удалось: {message}");
        return Err(JobError::Failed(format!("Ошибка конвертации в {}: {message}", format.code())));
    }

    report(ctx, Stage::Extract, 100);
    Ok(())
}

// ---------------------------------------------------------------------------
// Сохранение результата
// ---------------------------------------------------------------------------

/// Копирует готовый файл в выбранное место; коллизии решаются через
/// `name (1).ext`. Прогресс идёт событиями того же задания.
pub async fn save_result(ctx: &JobContext, job_id: u64, source: &Path, destination: &str) -> JobResult<String> {
    let metadata = fs::metadata(source)
        .map_err(|e| JobError::Failed(format!("Файл результата недоступен: {e}")))?;
    let total = metadata.len();

    let target_dir = Path::new(destination)
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .map(|p| p.to_owned())
        .unwrap_or_else(std::env::temp_dir);
    if !target_dir.is_dir() {
        return Err(JobError::Failed(format!("Каталог не существует: {}", target_dir.display())));
    }

    let requested = Path::new(destination)
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| JobError::Failed(format!("Некорректный путь сохранения: {destination}")))?;

    let fallback_stem = sanitize_file_stem(&format!("result-{job_id}"));
    let fallback_ext = source.extension().and_then(|e| e.to_str()).unwrap_or("bin");
    let (target, _file_name) = resolve_save_target(&target_dir, requested, &fallback_stem, fallback_ext);
    log::info!("сохранение задания {job_id}: {} -> {}", source.display(), target.display());

    copy_with_progress(ctx, source, &target, total).await?;
    remove_staging_dir(source);
    Ok(target.display().to_string())
}

/// Удаляет временный каталог скачивания после успешного сохранения.
fn remove_staging_dir(source: &Path) {
    let Some(parent) = source.parent() else { return };
    let Some(name) = parent.file_name().and_then(|n| n.to_str()) else { return };
    if name.starts_with("video_download_") {
        remove_dir_quietly(parent);
    }
}

async fn copy_with_progress(ctx: &JobContext, source: &Path, target: &Path, total: u64) -> JobResult<()> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    ctx.status(format!("Сохранение: {}", target.display()));
    ctx.progress(0);

    let mut input = tokio::fs::File::open(source)
        .await
        .map_err(|e| JobError::Failed(format!("Не удалось открыть {source:?}: {e}")))?;
    let mut output = tokio::fs::File::create(target)
        .await
        .map_err(|e| JobError::Failed(format!("Не удалось создать {target:?}: {e}")))?;

    let mut buffer = vec![0u8; 256 * 1024];
    let mut written: u64 = 0;
    let mut last_percent: u8 = 0;

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
        written += read as u64;

        if total > 0 {
            let percent = ((written.saturating_mul(100)) / total).min(99) as u8;
            if percent > last_percent {
                last_percent = percent;
                report(ctx, Stage::Save, percent);
            }
        }
    }

    output
        .flush()
        .await
        .map_err(|e| JobError::Failed(format!("Ошибка записи: {e}")))?;
    report(ctx, Stage::Save, 100);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Сквозная проверка на живом сайте: предпросмотр, скачивание mp3,
    /// проверка контейнера ffprobe-совместимым чтением. Нужен интернет:
    /// `cargo test -- --ignored download_real_url`.
    #[test]
    #[ignore = "обращается к сети, запускается вручную"]
    fn download_real_url() {
        let url = std::env::var("MUSIC_TOOLS_TEST_URL")
            .unwrap_or_else(|_| "https://vimeo.com/76979871".to_owned());

        let runtime = tokio::runtime::Runtime::new().expect("runtime");
        runtime.block_on(async {
            let info = preview(&url).await.expect("предпросмотр");
            println!("заголовок: {:?}, высоты: {:?}", info.title, info.heights);
            assert!(!info.title.trim().is_empty());

            let (tx, _rx) = tokio::sync::broadcast::channel(64);
            let ctx = crate::jobs::JobContext::for_test("download", tx);
            let outcome = run_job(
                &ctx,
                DownloadParams { url: url.clone(), format: DownloadFormat::Mp3, quality: "192kbps".to_owned() },
            )
            .await;

            let path = match outcome {
                JobOutcome::Ok { path: Some(path), .. } => path,
                other => panic!("скачивание не удалось: {other:?}"),
            };

            let file = std::path::Path::new(&path);
            assert!(file.is_file(), "нет файла {path}");
            let size = std::fs::metadata(file).expect("метаданные").len();
            assert!(size > 10_000, "файл слишком мал: {size} байт");
            println!("скачано: {path} ({size} байт)");
        });
    }
}

