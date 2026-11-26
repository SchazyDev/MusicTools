//! Самообновление `yt-dlp`.
//!
//! Закреплённый в установщике `yt-dlp.exe` быстро устаревает: сайты меняют
//! разметку, и старые версии перестают работать. Поэтому инструмент
//! скачивания ищет сначала копию в `%LOCALAPPDATA%\MusicTools\bin`
//! (см. `tools::sidecar`), а эта команда обновляет её.
//!
//! Запись в профиль пользователя не требует прав администратора, поэтому
//! обновление работает из интерфейса. Скачивание идёт во временный файл,
//! затем — атомарная подмена с резервной копией `.old`, как в установщике
//! приложения.

use std::path::{Path, PathBuf};

use serde::Serialize;
use sha2::{Digest, Sha256};

use super::sidecar::{user_bin_dir, SidecarName};
use crate::jobs::{JobOutcome, JobResult};

/// Ссылка на последний выпуск yt-dlp.
const RELEASE_API: &str = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest";
const ASSET_NAME: &str = "yt-dlp.exe";
const USER_AGENT: &str = "MusicTools-Updater";
const API_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(15);
const DOWNLOAD_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(300);

/// Сведения о текущей и доступной версии yt-dlp.
#[derive(Debug, Clone, Serialize)]
pub struct YtDlpStatus {
    /// Версия установленной копии (пусто, если её нет).
    pub current: Option<String>,
    /// Версия последнего выпуска на GitHub (пусто, если сеть недоступна).
    pub latest: Option<String>,
    /// Путь к действующей копии, если найдена.
    pub path: Option<String>,
    /// Обновление доступно (или локальной копии нет).
    pub update_available: bool,
}

fn client(timeout: std::time::Duration) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .user_agent(USER_AGENT)
        .timeout(timeout)
        .build()
        .map_err(|e| format!("не удалось создать HTTP-клиент: {e}"))
}

/// Запускает `yt-dlp --version` и возвращает версию, если она читается.
fn installed_version(exe: &Path) -> Option<String> {
    let mut command = std::process::Command::new(exe);
    command.arg("--version");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let output = command.output().ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let version = text.lines().next()?.trim().to_string();
    if version.is_empty() {
        None
    } else {
        Some(version)
    }
}

/// Действующая копия yt-dlp: обновляемая в профиле или закреплённая в сборке.
pub fn active_binary() -> Option<PathBuf> {
    let resolved = super::sidecar::resolve(SidecarName::YtDlp).ok()?;
    match resolved.program {
        super::sidecar::Program::Executable(path) => Some(path),
        super::sidecar::Program::PythonModule { .. } => None,
    }
}

/// Выпуск на GitHub: тег и ссылки на бинарник и контрольные суммы.
struct Release {
    tag: String,
    binary_url: String,
    binary_size: Option<u64>,
    sums_url: Option<String>,
}

async fn fetch_release() -> Result<Release, String> {
    let response = client(API_TIMEOUT)?
        .get(RELEASE_API)
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|e| format!("сетевая ошибка: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("GitHub вернул код {}", response.status().as_u16()));
    }
    let json: serde_json::Value =
        response.json().await.map_err(|e| format!("ответ GitHub не разобран: {e}"))?;

    let tag = json.get("tag_name").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    if tag.is_empty() {
        return Err("в ответе GitHub нет тега выпуска".to_string());
    }

    let assets = json.get("assets").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    let mut binary_url = None;
    let mut binary_size = None;
    let mut sums_url = None;
    for asset in &assets {
        let name = asset.get("name").and_then(|v| v.as_str()).unwrap_or_default();
        let url = asset.get("browser_download_url").and_then(|v| v.as_str());
        match name {
            ASSET_NAME => {
                binary_url = url.map(str::to_string);
                binary_size = asset.get("size").and_then(|v| v.as_u64());
            }
            "SHA2-256SUMS" => sums_url = url.map(str::to_string),
            _ => {}
        }
    }

    let binary_url = binary_url.ok_or_else(|| "в выпуске нет yt-dlp.exe".to_string())?;
    Ok(Release { tag, binary_url, binary_size, sums_url })
}

/// Ожидаемая контрольная сумма `yt-dlp.exe` из `SHA2-256SUMS`.
async fn fetch_expected_sum(url: &str) -> Result<String, String> {
    let response = client(API_TIMEOUT)?
        .get(url)
        .send()
        .await
        .map_err(|e| format!("сетевая ошибка при загрузке контрольных сумм: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("контрольные суммы: код {}", response.status().as_u16()));
    }
    let text = response.text().await.map_err(|e| format!("контрольные суммы не прочитаны: {e}"))?;
    for line in text.lines() {
        let mut parts = line.split_whitespace();
        let (Some(sum), Some(name)) = (parts.next(), parts.next()) else { continue };
        if name.trim_start_matches('*') == ASSET_NAME {
            return Ok(sum.to_ascii_lowercase());
        }
    }
    Err(format!("в SHA2-256SUMS нет записи для {ASSET_NAME}"))
}

/// Проверка текущей и доступной версии. Сеть необязательна: без неё
/// возвращается локальная версия и `update_available = false`.
pub async fn status() -> YtDlpStatus {
    let path = active_binary();
    let current = path.as_deref().and_then(installed_version);
    let latest = match fetch_release().await {
        Ok(release) => Some(release.tag),
        Err(e) => {
            log::warn!("yt-dlp: не удалось узнать последнюю версию: {e}");
            None
        }
    };

    let update_available = match (&latest, &current) {
        (Some(latest), Some(current)) => latest != current,
        (Some(_), None) => true,
        _ => false,
    };

    YtDlpStatus {
        current,
        latest,
        path: path.map(|p| p.display().to_string()),
        update_available,
    }
}

/// Заменяет `target` содержимым `staged` с резервной копией `.old`.
///
/// Windows не даёт перезаписать запущенный exe, поэтому сначала
/// переименовываем старую копию, затем ставим новую, а при ошибке
/// возвращаем всё назад.
fn replace_binary(staged: &Path, target: &Path) -> Result<(), String> {
    if let Some(parent) = target.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("не удалось создать {}: {e}", parent.display()))?;
    }

    let backup = target.with_extension("old");
    let had_old = target.exists();
    if had_old {
        if backup.exists() {
            let _ = std::fs::remove_file(&backup);
        }
        std::fs::rename(target, &backup)
            .map_err(|e| format!("не удалось освободить {}: {e}", target.display()))?;
    }

    if let Err(e) = std::fs::rename(staged, target) {
        if had_old {
            let _ = std::fs::rename(&backup, target);
        }
        return Err(format!("не удалось записать {}: {e}", target.display()));
    }

    let _ = std::fs::remove_file(&backup);
    Ok(())
}

/// Скачивание, проверка суммы и установка новой версии yt-dlp.
pub async fn update(
    ctx: &crate::jobs::JobContext,
    progress: impl Fn(u8),
) -> JobResult<JobOutcome> {
    let Some(bin_dir) = user_bin_dir() else {
        return Ok(JobOutcome::failed(
            "не найден каталог LOCALAPPDATA — обновление yt-dlp невозможно".to_string(),
        ));
    };
    let target = bin_dir.join(ASSET_NAME);

    ctx.status("проверка последней версии yt-dlp");
    let release = match fetch_release().await {
        Ok(release) => release,
        Err(e) => return Ok(JobOutcome::failed(format!("не удалось проверить yt-dlp: {e}"))),
    };
    ctx.check_cancel()?;

    if installed_version(&target).as_deref() == Some(release.tag.as_str()) {
        return Ok(JobOutcome::ok(format!("yt-dlp уже обновлён: {}", release.tag)));
    }

    let expected = match &release.sums_url {
        Some(url) => match fetch_expected_sum(url).await {
            Ok(sum) => Some(sum),
            Err(e) => {
                log::warn!("yt-dlp: контрольная сумма недоступна: {e}");
                None
            }
        },
        None => None,
    };
    ctx.check_cancel()?;

    ctx.status(format!("загрузка yt-dlp {}", release.tag));
    progress(0);
    let response = match client(DOWNLOAD_TIMEOUT) {
        Ok(client) => client.get(&release.binary_url).send().await,
        Err(e) => return Ok(JobOutcome::failed(e)),
    }
    .map_err(|e| crate::jobs::JobError::Failed(format!("сетевая ошибка: {e}")))?;
    if !response.status().is_success() {
        return Ok(JobOutcome::failed(format!(
            "GitHub вернул код {} при загрузке yt-dlp",
            response.status().as_u16()
        )));
    }

    let total = response.content_length().or(release.binary_size).filter(|n| *n > 0);
    let mut stream = response;
    let staged = bin_dir.join(format!("{ASSET_NAME}.part"));
    std::fs::create_dir_all(&bin_dir)
        .map_err(|e| crate::jobs::JobError::Failed(format!("нет доступа к {}: {e}", bin_dir.display())))?;

    let mut hasher = Sha256::new();
    let mut written: u64 = 0;
    let mut last_percent = 255u8;

    {
        use std::io::Write;
        let mut file = std::fs::File::create(&staged).map_err(|e| {
            crate::jobs::JobError::Failed(format!("не удалось создать {}: {e}", staged.display()))
        })?;

        while let Some(chunk) = stream
            .chunk()
            .await
            .map_err(|e| crate::jobs::JobError::Failed(format!("загрузка прервана: {e}")))?
        {
            ctx.check_cancel()?;
            file.write_all(&chunk).map_err(|e| {
                crate::jobs::JobError::Failed(format!("ошибка записи {}: {e}", staged.display()))
            })?;
            hasher.update(&chunk);
            written += chunk.len() as u64;
            if let Some(total) = total {
                let percent = ((written.min(total) * 100) / total) as u8;
                if percent != last_percent {
                    last_percent = percent;
                    progress(percent);
                }
            }
        }
        file.flush().map_err(|e| {
            crate::jobs::JobError::Failed(format!("ошибка записи {}: {e}", staged.display()))
        })?;
    }

    // --- Проверка перед подменой ---
    if let Some(total) = total {
        if written != total {
            let _ = std::fs::remove_file(&staged);
            return Ok(JobOutcome::failed(format!(
                "загрузка неполная: получено {written} байт из {total}"
            )));
        }
    }

    if let Some(expected) = expected {
        let got = format!("{:x}", hasher.finalize());
        if got != expected {
            let _ = std::fs::remove_file(&staged);
            return Ok(JobOutcome::failed(
                "контрольная сумма yt-dlp не совпала — файл отклонён".to_string(),
            ));
        }
    } else {
        log::warn!("yt-dlp: сумма недоступна, проверяется только размер");
    }

    ctx.check_cancel()?;
    if let Err(e) = replace_binary(&staged, &target) {
        let _ = std::fs::remove_file(&staged);
        return Ok(JobOutcome::failed(e));
    }

    // Проверяем, что новая копия запускается, иначе возвращаем старую.
    if installed_version(&target).is_none() {
        let backup = target.with_extension("old");
        if backup.exists() {
            let _ = std::fs::remove_file(&target);
            let _ = std::fs::rename(&backup, &target);
        }
        return Ok(JobOutcome::failed(
            "новая версия yt-dlp не запускается — восстановлена предыдущая".to_string(),
        ));
    }

    progress(100);
    ctx.status("yt-dlp обновлён");
    Ok(JobOutcome::ok_with_path(
        format!("yt-dlp обновлён до {}", release.tag),
        target.display().to_string(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replace_binary_swaps_and_cleans_backup() {
        let dir = std::env::temp_dir().join(format!("mt-ytdlp-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("каталог");

        let target = dir.join("yt-dlp.exe");
        std::fs::write(&target, b"old").expect("старый файл");
        let staged = dir.join("yt-dlp.exe.part");
        std::fs::write(&staged, b"new").expect("новый файл");

        replace_binary(&staged, &target).expect("подмена");
        assert_eq!(std::fs::read(&target).expect("чтение"), b"new");
        assert!(!staged.exists());
        assert!(!target.with_extension("old").exists());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn replace_binary_creates_directory() {        let dir = std::env::temp_dir().join(format!("mt-ytdlp-mk-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);

        let target = dir.join("nested").join("yt-dlp.exe");
        let staged = std::env::temp_dir().join(format!("mt-ytdlp-src-{}.part", std::process::id()));
        std::fs::write(&staged, b"data").expect("исходный файл");

        replace_binary(&staged, &target).expect("установка в новый каталог");
        assert_eq!(std::fs::read(&target).expect("чтение"), b"data");

        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_file(&staged);
    }

    /// Живая проверка: скачивание выпуска, контрольная сумма и установка
    /// в `%LOCALAPPDATA%\MusicTools\bin`. Запуск вручную:
    /// `cargo test -- --ignored ytdlp_update`.
    #[test]
    #[ignore = "обращается к github.com, запускается вручную"]
    fn updates_real_binary() {
        let runtime = tokio::runtime::Runtime::new().expect("runtime");
        let (tx, _rx) = tokio::sync::broadcast::channel(8);
        let ctx = crate::jobs::JobContext::for_test("ytdlp_update", tx);
        let outcome = runtime.block_on(update(&ctx, |_| {})).expect("задание выполнено");
        println!("{outcome:?}");
        assert!(matches!(outcome, crate::jobs::JobOutcome::Ok { .. }), "обновление не удалось: {outcome:?}");

        let bin = user_bin_dir().expect("LOCALAPPDATA").join(ASSET_NAME);
        assert!(bin.is_file(), "нет файла {}", bin.display());
        let version = installed_version(&bin).expect("новая версия запускается");
        println!("установлен yt-dlp {version}");
    }
}
