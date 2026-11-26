//! Запуск внешних sidecar-программ: yt-dlp, ffmpeg, позже обёртка Demucs.
//!
//! Гарантии (см. docs/TAURI_ARCHITECTURE.md, раздел «Стратегия sidecar»):
//! * поиск бинарника идёт по фиксированному порядку и запоминает, где он найден;
//! * дочерний процесс запускается без окна консоли, с пустыми pipe для stdout
//!   и stderr, stdin не наследуется;
//! * при отмене убивается всё дерево процессов (на Windows — `taskkill /T /F`);
//! * stderr читается построчно, последние строки попадают в текст ошибки.
//!
//! Здесь же живёт построитель аргументов ffmpeg — единый для скачивания и
//! конвертации (перенос `core/ffmpeg_utils.py::build_conversion_args`).

use std::collections::VecDeque;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::mpsc;

/// Сколько последних строк stderr хранится для диагностики.
pub const STDERR_TAIL_LINES: usize = 50;

/// Максимальная длина текста ошибки, показываемого пользователю.
pub const MAX_ERROR_LENGTH: usize = 400;

// ---------------------------------------------------------------------------
// Имена и разрешение программ
// ---------------------------------------------------------------------------

/// Программа, которую умеет разрешать [`resolve`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SidecarName {
    YtDlp,
    Ffmpeg,
}

impl SidecarName {
    pub fn file_stem(self) -> &'static str {
        match self {
            SidecarName::YtDlp => "yt-dlp",
            SidecarName::Ffmpeg => "ffmpeg",
        }
    }

    pub fn file_name(self) -> String {
        if cfg!(windows) {
            format!("{}.exe", self.file_stem())
        } else {
            self.file_stem().to_owned()
        }
    }

    /// Пользовательское имя в сообщениях об ошибке.
    pub fn label(self) -> &'static str {
        match self {
            SidecarName::YtDlp => "yt-dlp",
            SidecarName::Ffmpeg => "ffmpeg",
        }
    }
}

/// Источник, из которого найден исполняемый файл.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SidecarSource {
    /// Каталог ресурсов установки (`app.path().resource_dir()`).
    ResourceDir,
    /// Каталог текущего exe.
    ExeDir,
    /// Переменная окружения `PATH`.
    Path,
    /// Отладочный запуск модуля Python (`python -m yt_dlp`).
    PythonModule,
    /// Отладочный бинарник из корня репозитория (только debug-сборка).
    RepoRoot,
}

impl SidecarSource {
    pub fn describe(self) -> &'static str {
        match self {
            SidecarSource::ResourceDir => "каталог ресурсов",
            SidecarSource::ExeDir => "каталог приложения",
            SidecarSource::Path => "PATH",
            SidecarSource::PythonModule => "python -m yt_dlp (отладочный запуск)",
            SidecarSource::RepoRoot => "корень репозитория (отладочный бинарник)",
        }
    }
}

/// Способ запуска программы.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Program {
    /// Обычный исполняемый файл.
    Executable(PathBuf),
    /// `python -m <module>` — резерв для разработки, когда бинарника нет.
    PythonModule { python: String, module: String },
}

/// Результат разрешения: путь, источник и префикс аргументов.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedSidecar {
    pub name: SidecarName,
    pub source: SidecarSource,
    pub program: Program,
}

impl ResolvedSidecar {
    /// Строка для журнала и диагностики.
    pub fn describe(&self) -> String {
        match &self.program {
            Program::Executable(path) => {
                format!("{}: {} ({})", self.name.label(), path.display(), self.source.describe())
            }
            Program::PythonModule { python, module } => format!(
                "{}: {} -m {} ({})",
                self.name.label(),
                python,
                module,
                self.source.describe()
            ),
        }
    }

    /// Готовит [`CommandSpec`] без аргументов.
    pub fn command(&self) -> CommandSpec {
        match &self.program {
            Program::Executable(path) => CommandSpec::new(path.clone()),
            Program::PythonModule { python, module } => {
                CommandSpec::new(PathBuf::from(python)).args(["-m", module.as_str()])
            }
        }
    }
}

/// Ошибка поиска sidecar-программы.
#[derive(Debug, thiserror::Error)]
pub enum SidecarError {
    #[error(
        "не найдена программа {name}: проверены каталог ресурсов, каталог приложения и PATH{dev_hint}"
    )]
    NotFound { name: &'static str, dev_hint: &'static str },
}

/// Окружение, в котором идёт поиск. В тестах подменяется заглушкой.
pub trait SidecarEnv {
    /// Каталог ресурсов установки, если известен.
    fn resource_dir(&self) -> Option<PathBuf>;
    /// Каталог текущего исполняемого файла.
    fn exe_dir(&self) -> Option<PathBuf>;
    /// Элементы `PATH`.
    fn path_dirs(&self) -> Vec<PathBuf>;
    /// Существует ли файл.
    fn is_file(&self, path: &Path) -> bool;
    /// `python -m yt_dlp` доступен для отладки.
    fn python_module_available(&self) -> bool;
    /// Разрешены отладочные резервы.
    fn dev_fallbacks(&self, name: SidecarName) -> Vec<DevFallback>;
}

/// Отладочный резерв, который может сработать в конце поиска.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DevFallback {
    /// Прямой путь к бинарнику (проверяется на существование).
    Executable(SidecarSource, PathBuf),
    /// `python -m yt_dlp`, если Python установлен.
    PythonModule { python: String, module: String },
}

/// Каталог ресурсов в отладочной сборке: `src-tauri/resources`.
fn dev_resources_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources")
}

/// Отладочный путь к `ffmpeg.exe` в корне репозитория.
pub fn dev_repo_ffmpeg() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("ffmpeg.exe")
}

/// Отладочный резерв для `python -m yt_dlp`.
pub fn dev_python_module() -> String {
    if cfg!(windows) {
        "python".to_owned()
    } else {
        "python3".to_owned()
    }
}

fn not_found_error(name: SidecarName, dev_hint: &'static str) -> SidecarError {
    SidecarError::NotFound { name: name.label(), dev_hint }
}

const DEV_YTDLP_HINT: &str = "; положите yt-dlp.exe в resources или установите yt-dlp для Python 3";
const DEV_FFMPEG_HINT: &str = "; положите ffmpeg.exe в resources или в корень репозитория";
const RELEASE_HINT: &str = "";

/// Порядок поиска: каталог ресурсов, каталог приложения, PATH, затем
/// (только в отладочной сборке) резервы — Python-модуль или корень репозитория.
pub fn resolve_with<E: SidecarEnv>(env: &E, name: SidecarName) -> Result<ResolvedSidecar, SidecarError> {
    let file_name = name.file_name();

    if let Some(dir) = env.resource_dir() {
        let candidate = dir.join(&file_name);
        if env.is_file(&candidate) {
            return Ok(ResolvedSidecar {
                name,
                source: SidecarSource::ResourceDir,
                program: Program::Executable(candidate),
            });
        }
    }

    if let Some(dir) = env.exe_dir() {
        let candidate = dir.join(&file_name);
        if env.is_file(&candidate) {
            return Ok(ResolvedSidecar {
                name,
                source: SidecarSource::ExeDir,
                program: Program::Executable(candidate),
            });
        }
    }

    for dir in env.path_dirs() {
        let candidate = dir.join(&file_name);
        if env.is_file(&candidate) {
            return Ok(ResolvedSidecar {
                name,
                source: SidecarSource::Path,
                program: Program::Executable(candidate),
            });
        }
    }

    for fallback in env.dev_fallbacks(name) {
        match fallback {
            DevFallback::Executable(source, path) => {
                if env.is_file(&path) {
                    return Ok(ResolvedSidecar { name, source, program: Program::Executable(path) });
                }
            }
            DevFallback::PythonModule { python, module } => {
                if env.python_module_available() {
                    return Ok(ResolvedSidecar {
                        name,
                        source: SidecarSource::PythonModule,
                        program: Program::PythonModule { python, module },
                    });
                }
            }
        }
    }

    let hint = match (name, cfg!(debug_assertions)) {
        (SidecarName::YtDlp, true) => DEV_YTDLP_HINT,
        (SidecarName::Ffmpeg, true) => DEV_FFMPEG_HINT,
        _ => RELEASE_HINT,
    };
    Err(not_found_error(name, hint))
}

/// Настоящее окружение процесса.
struct RealEnv;

impl SidecarEnv for RealEnv {
    fn resource_dir(&self) -> Option<PathBuf> {
        // В отладочной сборке каталог ресурсов — `src-tauri/resources`, чтобы
        // поведение совпадало с `resolve_resources_dir` в `lib.rs`.
        if cfg!(debug_assertions) {
            let dev = dev_resources_dir();
            if dev.is_dir() {
                return Some(dev);
            }
        }
        std::env::current_exe().ok().and_then(|exe| exe.parent().map(|p| p.to_owned()))
    }

    fn exe_dir(&self) -> Option<PathBuf> {
        std::env::current_exe().ok().and_then(|exe| exe.parent().map(|p| p.to_owned()))
    }

    fn path_dirs(&self) -> Vec<PathBuf> {
        let Some(path) = std::env::var_os("PATH") else { return Vec::new() };
        std::env::split_paths(&path).collect()
    }

    fn is_file(&self, path: &Path) -> bool {
        path.is_file()
    }

    fn python_module_available(&self) -> bool {
        cfg!(debug_assertions) && python_has_ytdlp(&dev_python_module())
    }

    fn dev_fallbacks(&self, name: SidecarName) -> Vec<DevFallback> {
        if !cfg!(debug_assertions) {
            return Vec::new();
        }
        match name {
            SidecarName::YtDlp => vec![DevFallback::PythonModule {
                python: dev_python_module(),
                module: "yt_dlp".to_owned(),
            }],
            SidecarName::Ffmpeg => vec![DevFallback::Executable(SidecarSource::RepoRoot, dev_repo_ffmpeg())],
        }
    }
}

/// Быстрая проверка `python -m yt_dlp` (не больше секунды).
fn python_has_ytdlp(python: &str) -> bool {
    let started = Instant::now();
    let Ok(mut child) = std::process::Command::new(python)
        .args(["-c", "import yt_dlp"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags_no_window()
        .spawn()
    else {
        return false;
    };

    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) => {}
            Err(_) => return false,
        }
        if started.elapsed() > Duration::from_secs(1) {
            let _ = child.kill();
            return false;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[cfg(windows)]
trait NoWindow {
    fn creation_flags_no_window(&mut self) -> &mut Self;
}

#[cfg(windows)]
impl NoWindow for std::process::Command {
    fn creation_flags_no_window(&mut self) -> &mut Self {
        use std::os::windows::process::CommandExt;
        self.creation_flags(CREATE_NO_WINDOW)
    }
}

#[cfg(not(windows))]
trait NoWindow {
    fn creation_flags_no_window(&mut self) -> &mut Self;
}

#[cfg(not(windows))]
impl NoWindow for std::process::Command {
    fn creation_flags_no_window(&mut self) -> &mut Self {
        self
    }
}

/// Разрешает программу в окружении текущего процесса.
pub fn resolve(name: SidecarName) -> Result<ResolvedSidecar, SidecarError> {
    let resolved = resolve_with(&RealEnv, name)?;
    log::info!("sidecar: {}", resolved.describe());
    Ok(resolved)
}

// ---------------------------------------------------------------------------
// Запуск процессов
// ---------------------------------------------------------------------------

/// `CREATE_NO_WINDOW` из `winbase.h`: консольное окно не появляется.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Описание запуска: программа, аргументы, рабочий каталог.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandSpec {
    program: PathBuf,
    args: Vec<String>,
    cwd: Option<PathBuf>,
}

impl CommandSpec {
    pub fn new(program: impl Into<PathBuf>) -> Self {
        Self { program: program.into(), args: Vec::new(), cwd: None }
    }

    pub fn arg(mut self, arg: impl Into<String>) -> Self {
        self.args.push(arg.into());
        self
    }

    pub fn args<I, S>(mut self, args: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.args.extend(args.into_iter().map(Into::into));
        self
    }

    pub fn cwd(mut self, dir: impl Into<PathBuf>) -> Self {
        self.cwd = Some(dir.into());
        self
    }

    pub fn program(&self) -> &Path {
        &self.program
    }

    pub fn arguments(&self) -> &[String] {
        &self.args
    }

    /// Собирает `tokio::process::Command` с нужными режимами потоков.
    pub fn build(&self) -> Command {
        let mut cmd = Command::new(&self.program);
        cmd.args(&self.args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);

        if let Some(dir) = &self.cwd {
            cmd.current_dir(dir);
        }

        #[cfg(windows)]
        {
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        cmd
    }
}

/// Одна прочитанная строка процесса.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Line {
    Stdout(String),
    Stderr(String),
}

impl Line {
    pub fn text(&self) -> &str {
        match self {
            Line::Stdout(s) | Line::Stderr(s) => s,
        }
    }
}

/// Кольцевой буфер последних строк stderr.
#[derive(Debug, Clone, Default)]
pub struct StderrTail {
    lines: VecDeque<String>,
    limit: usize,
}

impl StderrTail {
    pub fn new(limit: usize) -> Self {
        Self { lines: VecDeque::new(), limit: limit.max(1) }
    }

    pub fn push(&mut self, line: impl Into<String>) {
        if self.lines.len() == self.limit {
            self.lines.pop_front();
        }
        self.lines.push_back(line.into());
    }

    pub fn lines(&self) -> impl Iterator<Item = &String> {
        self.lines.iter()
    }

    pub fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    pub fn len(&self) -> usize {
        self.lines.len()
    }
}

/// Собирает строку ошибки из stderr: пять последних значимых строк.
pub fn format_tool_error(tool: &str, status: Option<i32>, tail: &StderrTail) -> String {
    let meaningful: Vec<&str> = tail.lines().map(|l| l.trim()).filter(|l| !l.is_empty()).collect();
    let code = status.map(|c| c.to_string()).unwrap_or_else(|| "неизвестен".to_owned());

    if meaningful.is_empty() {
        return format!("{tool} завершился с кодом {code}");
    }

    let start = meaningful.len().saturating_sub(5);
    let mut text = meaningful[start..].join(" | ");
    if text.chars().count() > MAX_ERROR_LENGTH {
        let skip = text.chars().count() - MAX_ERROR_LENGTH;
        text = text.chars().skip(skip).collect();
    }
    format!("{tool} завершился с кодом {code}: {text}")
}

async fn pump<R>(reader: R, is_stderr: bool, tx: mpsc::UnboundedSender<Line>)
where
    R: AsyncRead + Unpin,
{
    let mut lines = BufReader::new(reader).lines();
    loop {
        match lines.next_line().await {
            Ok(Some(line)) => {
                let item = if is_stderr { Line::Stderr(line) } else { Line::Stdout(line) };
                if tx.send(item).is_err() {
                    break;
                }
            }
            Ok(None) => break,
            Err(_) => break,
        }
    }
}

/// Запуск процесса с построчной выдачей и отменой.
pub struct Running {
    child: Child,
    rx: mpsc::UnboundedReceiver<Line>,
}

impl Running {
    /// Запускает процесс. Потоки читаются фоновыми задачами tokio.
    pub fn spawn(spec: CommandSpec) -> io::Result<Self> {
        let mut cmd = spec.build();
        let mut child = cmd.spawn()?;
        let (tx, rx) = mpsc::unbounded_channel();

        if let Some(stdout) = child.stdout.take() {
            tokio::spawn(pump(stdout, false, tx.clone()));
        }
        if let Some(stderr) = child.stderr.take() {
            tokio::spawn(pump(stderr, true, tx));
        }

        Ok(Self { child, rx })
    }

    pub fn child_mut(&mut self) -> &mut Child {
        &mut self.child
    }

    /// Следующая строка или `None`, если процесс закрыл оба потока.
    pub async fn next_line(&mut self) -> Option<Line> {
        self.rx.recv().await
    }

    /// Ждёт завершения процесса и возвращает код выхода.
    pub async fn wait(&mut self) -> io::Result<Option<i32>> {
        let status = self.child.wait().await?;
        Ok(status.code())
    }

    /// Убивает всё дерево процессов. Никогда не паникует.
    pub async fn kill_tree(&mut self) {
        kill_tree(&mut self.child).await;
    }
}

/// Убивает процесс вместе с потомками.
///
/// На Windows `taskkill /T /F /PID` снимает всё дерево (yt-dlp запускает
/// ffmpeg); затем вызывается `kill` на случай, если `taskkill` недоступен.
pub async fn kill_tree(child: &mut Child) {
    let Some(pid) = child.id() else { return };

    #[cfg(windows)]
    {
        let mut cmd = Command::new("taskkill");
        cmd.args(["/T", "/F", "/PID", &pid.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        {
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        match cmd.status().await {
            Ok(status) if status.success() => {}
            Ok(status) => log::warn!("taskkill завершился с кодом {:?}", status.code()),
            Err(e) => log::warn!("не удалось выполнить taskkill: {e}"),
        }
    }

    if let Err(e) = child.kill().await {
        log::debug!("повторный kill процесса {pid}: {e}");
    }
    let _ = tokio::time::timeout(Duration::from_secs(5), child.wait()).await;
}

// ---------------------------------------------------------------------------
// Аргументы ffmpeg (перенос core/ffmpeg_utils.py)
// ---------------------------------------------------------------------------

/// Выходной формат аудио для [`build_conversion_args`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AudioFormat {
    Mp3,
    Wav,
    Flac,
    Ogg,
}

impl AudioFormat {
    pub fn from_code(code: &str) -> Option<Self> {
        match code {
            "mp3" => Some(Self::Mp3),
            "wav" => Some(Self::Wav),
            "flac" => Some(Self::Flac),
            "ogg" => Some(Self::Ogg),
            _ => None,
        }
    }

    pub fn code(self) -> &'static str {
        match self {
            Self::Mp3 => "mp3",
            Self::Wav => "wav",
            Self::Flac => "flac",
            Self::Ogg => "ogg",
        }
    }
}

/// Ошибка построения аргументов.
#[derive(Debug, thiserror::Error)]
pub enum FfmpegArgsError {
    #[error("неизвестное качество {quality} для формата {format}")]
    BadQuality { format: &'static str, quality: String },
}

fn wav_codec(quality: &str) -> Result<&'static str, FfmpegArgsError> {
    match quality {
        "16" => Ok("pcm_s16le"),
        "24" => Ok("pcm_s24le"),
        "32" => Ok("pcm_s32le"),
        other => Err(FfmpegArgsError::BadQuality { format: "wav", quality: other.to_owned() }),
    }
}

fn flac_args(quality: &str) -> Result<Vec<String>, FfmpegArgsError> {
    let (depth, comp) = quality.split_once(':').ok_or_else(|| FfmpegArgsError::BadQuality {
        format: "flac",
        quality: quality.to_owned(),
    })?;
    let comp: i32 = comp.trim().parse().map_err(|_| FfmpegArgsError::BadQuality {
        format: "flac",
        quality: quality.to_owned(),
    })?;
    let comp = comp.to_string();

    match depth {
        "16" => Ok(vec![
            "-acodec".into(),
            "flac".into(),
            "-sample_fmt".into(),
            "s16".into(),
            "-compression_level".into(),
            comp,
        ]),
        "24" => Ok(vec![
            "-acodec".into(),
            "flac".into(),
            "-sample_fmt".into(),
            "s32".into(),
            "-bits_per_raw_sample".into(),
            "24".into(),
            "-compression_level".into(),
            comp,
        ]),
        other => Err(FfmpegArgsError::BadQuality { format: "flac", quality: format!("{other}:{comp}") }),
    }
}

/// Аргументы ffmpeg для одной конвертации — точно как в Python-версии:
/// `[ffmpeg, -i, <in>, -vn, <кодек>, ..., -y, <out>]`.
///
/// `quality` — битрейт в кбит/с для `mp3`/`ogg` (`"320"`), разрядность для
/// `wav` (`"16"`, `"24"`, `"32"`) и `"<разрядность>:<уровень>"` для `flac`.
pub fn build_conversion_args(
    ffmpeg: &Path,
    input: &Path,
    output: &Path,
    format: AudioFormat,
    quality: &str,
) -> Result<Vec<String>, FfmpegArgsError> {
    let mut cmd: Vec<String> = vec![
        ffmpeg.display().to_string(),
        "-i".into(),
        input.display().to_string(),
        "-vn".into(),
    ];

    match format {
        AudioFormat::Mp3 => {
            cmd.extend(["-acodec", "libmp3lame", "-b:a", &format!("{quality}k")].map(str::to_owned));
        }
        AudioFormat::Wav => {
            let codec = wav_codec(quality)?;
            cmd.extend(["-acodec".to_owned(), codec.to_owned()]);
        }
        AudioFormat::Flac => cmd.extend(flac_args(quality)?),
        AudioFormat::Ogg => {
            cmd.extend(["-acodec", "libvorbis", "-b:a", &format!("{quality}k")].map(str::to_owned));
        }
    }

    cmd.push("-y".into());
    cmd.push(output.display().to_string());
    Ok(cmd)
}

/// Полный argv: общие флаги прогресса идут сразу после имени программы,
/// иначе (как в Python) `-nostdin`, `-hide_banner`, `-progress`, `-nostats`.
pub fn with_progress_flags(base: Vec<String>) -> Vec<String> {
    let mut out: Vec<String> = Vec::with_capacity(base.len() + 5);
    let mut iter = base.into_iter();
    if let Some(program) = iter.next() {
        out.push(program);
    }
    out.extend(["-nostdin", "-hide_banner", "-progress", "pipe:1", "-nostats"].map(str::to_owned));
    out.extend(iter);
    out
}

/// Строка `out_time_us=<µs>` в проценты 0..99. `None`, если строку не разобрать.
pub fn parse_progress_line(line: &str, duration_seconds: Option<f64>) -> Option<u8> {
    let (key, value) = line.trim().split_once('=')?;
    if key != "out_time_us" && key != "out_time_ms" {
        return None;
    }
    let micros: i128 = value.trim().parse().ok()?;
    let position = micros as f64 / 1_000_000.0;
    let duration = duration_seconds?;
    if duration <= 0.0 {
        return None;
    }
    let percent = (position / duration * 100.0) as i64;
    Some(percent.clamp(0, 99) as u8)
}

/// Длительность из первой строки `Duration: HH:MM:SS.ss` в stderr.
pub fn parse_duration<'a, I>(lines: I) -> Option<f64>
where
    I: IntoIterator<Item = &'a str>,
{
    for line in lines {
        let Some(marker) = line.find("Duration: ") else { continue };
        let raw = line[marker + "Duration: ".len()..].split(',').next().unwrap_or("").trim();
        if raw == "N/A" || raw.is_empty() {
            return None;
        }
        let mut parts = raw.split(':');
        let (Some(h), Some(m), Some(s), None) =
            (parts.next(), parts.next(), parts.next(), parts.next())
        else {
            return None;
        };
        let Ok(h) = h.trim().parse::<i64>() else { return None };
        let Ok(m) = m.trim().parse::<i64>() else { return None };
        let Ok(s) = s.trim().parse::<f64>() else { return None };
        return Some(h as f64 * 3600.0 + m as f64 * 60.0 + s);
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(name: &str) -> PathBuf {
        PathBuf::from(r"C:\tools").join(name)
    }

    #[test]
    fn mp3_args_match_python_matrix() {
        let args = build_conversion_args(
            Path::new(r"C:\tools\ffmpeg.exe"),
            Path::new(r"C:\tmp\a.webm"),
            Path::new(r"C:\tmp\a.mp3"),
            AudioFormat::Mp3,
            "320",
        )
        .expect("аргументы");
        assert_eq!(
            args,
            vec![
                r"C:\tools\ffmpeg.exe",
                "-i",
                r"C:\tmp\a.webm",
                "-vn",
                "-acodec",
                "libmp3lame",
                "-b:a",
                "320k",
                "-y",
                r"C:\tmp\a.mp3",
            ]
        );
    }

    #[test]
    fn wav_args_match_python_matrix() {
        for (depth, codec) in [("16", "pcm_s16le"), ("24", "pcm_s24le"), ("32", "pcm_s32le")] {
            let args = build_conversion_args(
                Path::new("ffmpeg"),
                Path::new("in"),
                Path::new("out.wav"),
                AudioFormat::Wav,
                depth,
            )
            .expect("аргументы");
            assert_eq!(
                args,
                vec!["ffmpeg", "-i", "in", "-vn", "-acodec", codec, "-y", "out.wav"],
                "глубина {depth}"
            );
        }
        assert!(build_conversion_args(
            Path::new("ffmpeg"),
            Path::new("in"),
            Path::new("out.wav"),
            AudioFormat::Wav,
            "8"
        )
        .is_err());
    }

    #[test]
    fn flac_args_match_python_matrix() {
        let args = build_conversion_args(
            Path::new("ffmpeg"),
            Path::new("in"),
            Path::new("out.flac"),
            AudioFormat::Flac,
            "24:8",
        )
        .expect("аргументы");
        assert_eq!(
            args,
            vec![
                "ffmpeg",
                "-i",
                "in",
                "-vn",
                "-acodec",
                "flac",
                "-sample_fmt",
                "s32",
                "-bits_per_raw_sample",
                "24",
                "-compression_level",
                "8",
                "-y",
                "out.flac",
            ]
        );

        let args = build_conversion_args(
            Path::new("ffmpeg"),
            Path::new("in"),
            Path::new("out.flac"),
            AudioFormat::Flac,
            "16:5",
        )
        .expect("аргументы");
        assert_eq!(
            args,
            vec![
                "ffmpeg",
                "-i",
                "in",
                "-vn",
                "-acodec",
                "flac",
                "-sample_fmt",
                "s16",
                "-compression_level",
                "5",
                "-y",
                "out.flac",
            ]
        );
    }

    #[test]
    fn ogg_args_match_python_matrix() {
        let args = build_conversion_args(
            Path::new("ffmpeg"),
            Path::new("in"),
            Path::new("out.ogg"),
            AudioFormat::Ogg,
            "192",
        )
        .expect("аргументы");
        assert_eq!(
            args,
            vec!["ffmpeg", "-i", "in", "-vn", "-acodec", "libvorbis", "-b:a", "192k", "-y", "out.ogg"]
        );
    }

    #[test]
    fn progress_flags_go_right_after_program() {
        let base = vec!["ffmpeg".to_owned(), "-i".to_owned(), "in".to_owned()];
        assert_eq!(
            with_progress_flags(base),
            vec!["ffmpeg", "-nostdin", "-hide_banner", "-progress", "pipe:1", "-nostats", "-i", "in"]
        );
    }

    #[test]
    fn progress_line_parsing_clamps() {
        assert_eq!(parse_progress_line("out_time_us=5000000", Some(10.0)), Some(50));
        assert_eq!(parse_progress_line("out_time_us=10000000", Some(10.0)), Some(99));
        assert_eq!(parse_progress_line("out_time_us=-1000", Some(10.0)), Some(0));
        assert_eq!(parse_progress_line("out_time_us=0", None), None);
        assert_eq!(parse_progress_line("frame=1", Some(10.0)), None);
        assert_eq!(parse_progress_line("garbage", Some(10.0)), None);
    }

    #[test]
    fn duration_parsing() {
        let lines = vec!["Input #0", "  Duration: 00:01:02.50, start: 0.0", "Stream #0:0"];
        assert_eq!(parse_duration(lines.iter().map(|s| *s)), Some(62.5));
        assert_eq!(parse_duration(["Duration: N/A, bitrate: N/A"]), None);
        assert_eq!(parse_duration(std::iter::empty()), None);
    }

    #[test]
    fn error_message_prefers_tail() {
        let mut tail = StderrTail::new(STDERR_TAIL_LINES);
        for i in 0..10 {
            tail.push(format!("line {i}"));
        }
        let msg = format_tool_error("ffmpeg", Some(1), &tail);
        assert!(msg.starts_with("ffmpeg завершился с кодом 1: "));
        assert!(msg.contains("line 9"));
        assert!(!msg.contains("line 4"));
        assert_eq!(tail.len(), 10);

        let empty = StderrTail::new(4);
        assert_eq!(format_tool_error("yt-dlp", Some(2), &empty), "yt-dlp завершился с кодом 2");
    }

    #[test]
    fn stderr_tail_keeps_limit() {
        let mut tail = StderrTail::new(3);
        for i in 0..6 {
            tail.push(format!("{i}"));
        }
        assert_eq!(tail.lines().cloned().collect::<Vec<_>>(), vec!["3", "4", "5"]);
    }

    // --- порядок разрешения на поддельной файловой системе ---

    struct FakeEnv {
        resources: Option<PathBuf>,
        exe: Option<PathBuf>,
        path: Vec<PathBuf>,
        files: Vec<PathBuf>,
        python: bool,
        dev: bool,
    }

    impl FakeEnv {
        fn new() -> Self {
            Self { resources: None, exe: None, path: Vec::new(), files: Vec::new(), python: false, dev: false }
        }
    }

    impl SidecarEnv for FakeEnv {
        fn resource_dir(&self) -> Option<PathBuf> {
            self.resources.clone()
        }
        fn exe_dir(&self) -> Option<PathBuf> {
            self.exe.clone()
        }
        fn path_dirs(&self) -> Vec<PathBuf> {
            self.path.clone()
        }
        fn is_file(&self, path: &Path) -> bool {
            self.files.iter().any(|f| f == path)
        }
        fn python_module_available(&self) -> bool {
            self.python
        }
        fn dev_fallbacks(&self, name: SidecarName) -> Vec<DevFallback> {
            if !self.dev {
                return Vec::new();
            }
            match name {
                SidecarName::YtDlp => vec![DevFallback::PythonModule {
                    python: "python".into(),
                    module: "yt_dlp".into(),
                }],
                SidecarName::Ffmpeg => vec![DevFallback::Executable(SidecarSource::RepoRoot, p("repo-ffmpeg.exe"))],
            }
        }
    }

    #[test]
    fn resolution_prefers_resource_dir() {
        let mut env = FakeEnv::new();
        let resources = PathBuf::from(r"C:\res");
        env.resources = Some(resources.clone());
        env.exe = Some(PathBuf::from(r"C:\app"));
        env.path = vec![PathBuf::from(r"C:\onpath")];
        let name = SidecarName::YtDlp.file_name();
        env.files = vec![
            resources.join(&name),
            PathBuf::from(r"C:\app").join(&name),
            PathBuf::from(r"C:\onpath").join(&name),
        ];
        let got = resolve_with(&env, SidecarName::YtDlp).expect("найдено");
        assert_eq!(got.source, SidecarSource::ResourceDir);
        assert_eq!(got.program, Program::Executable(resources.join(&name)));
    }

    #[test]
    fn resolution_falls_back_in_order() {
        let mut env = FakeEnv::new();
        env.resources = Some(PathBuf::from(r"C:\res"));
        env.exe = Some(PathBuf::from(r"C:\app"));
        env.path = vec![PathBuf::from(r"C:\onpath")];

        let near = PathBuf::from(r"C:\app").join("ffmpeg.exe");
        env.files = vec![near.clone()];
        let got = resolve_with(&env, SidecarName::Ffmpeg).expect("каталог приложения");
        assert_eq!(got.source, SidecarSource::ExeDir);
        assert_eq!(got.program, Program::Executable(near));

        let on_path = PathBuf::from(r"C:\onpath").join("ffmpeg.exe");
        env.files = vec![on_path.clone()];
        let got = resolve_with(&env, SidecarName::Ffmpeg).expect("PATH");
        assert_eq!(got.source, SidecarSource::Path);
        assert_eq!(got.program, Program::Executable(on_path));
    }

    #[test]
    fn resolution_uses_dev_fallbacks_last() {
        let mut env = FakeEnv::new();
        env.exe = Some(PathBuf::from(r"C:\app"));
        env.dev = true;
        env.python = true;

        let got = resolve_with(&env, SidecarName::YtDlp).expect("python-модуль");
        assert_eq!(got.source, SidecarSource::PythonModule);
        let cmd = got.command();
        assert_eq!(cmd.program(), Path::new("python"));
        assert_eq!(cmd.arguments(), ["-m", "yt_dlp"]);

        env.python = false;
        env.files = vec![p("repo-ffmpeg.exe")];
        let got = resolve_with(&env, SidecarName::Ffmpeg).expect("корень репозитория");
        assert_eq!(got.source, SidecarSource::RepoRoot);
    }

    #[test]
    fn resolution_reports_missing_program() {
        let env = FakeEnv::new();
        let err = resolve_with(&env, SidecarName::Ffmpeg).expect_err("пустое окружение");
        let text = err.to_string();
        assert!(text.contains("ffmpeg"));
        assert!(text.contains("не найдена"));
    }

    #[test]
    fn describe_mentions_source() {
        let resolved = ResolvedSidecar {
            name: SidecarName::YtDlp,
            source: SidecarSource::PythonModule,
            program: Program::PythonModule { python: "python".into(), module: "yt_dlp".into() },
        };
        assert!(resolved.describe().contains("python -m yt_dlp"));
    }

    #[test]
    fn command_spec_pipes_and_orders_arguments() {
        let spec = CommandSpec::new("ffmpeg").arg("-i").args(["a", "b"]).arg("-y");
        assert_eq!(spec.arguments(), ["-i", "a", "b", "-y"]);
        let cmd = spec.build();
        let std_cmd = cmd.as_std();
        assert_eq!(std_cmd.get_program(), "ffmpeg");
        assert!(std_cmd.get_args().eq(["-i", "a", "b", "-y"]));
    }

    /// Проверка на живой машине: запускается вручную с `--ignored`.
    /// Требует установленных `yt-dlp` и `ffmpeg.exe` в корне репозитория.
    #[test]
    #[ignore = "проверка реального окружения, запускается вручную"]
    fn resolves_real_environment() {
        let ytdlp = resolve(SidecarName::YtDlp).expect("yt-dlp должен находиться");
        let ffmpeg = resolve(SidecarName::Ffmpeg).expect("ffmpeg должен находиться");
        println!("yt-dlp: {}", ytdlp.describe());
        println!("ffmpeg: {}", ffmpeg.describe());
    }

}
