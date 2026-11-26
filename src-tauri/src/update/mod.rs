//! Обновление приложения через GitHub-релизы.
//!
//! Перенос `core/updater.py`. Контракт манифеста `latest.json` сохранён:
//! подпись Ed25519 над каноническими байтами (манифест без `signature`,
//! отсортированные ключи, разделители без пробелов, UTF-8), затем проверка
//! канала, версии, `sha256` и размера. Порядок доверия: сначала подпись,
//! только потом адрес загрузки и хеш.
//!
//! Установка отложенная: скачанный файл подменяет текущий exe скриптом
//! после выхода приложения, поэтому установщик не требует прав администратора.

use std::path::{Path, PathBuf};
use std::time::Duration;

use base64::Engine as _;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Платформенный ключ в манифесте.
pub const PLATFORM_KEY: &str = "windows-x86_64";
/// Имя ассета с манифестом.
pub const MANIFEST_ASSET_NAME: &str = "latest.json";
/// Открытый ключ подписи (Ed25519, base64). Сгенерирован
/// `scripts/gen_keys.py`; закрытая половина — `keys/private.key` (не в git).
/// Обязан совпадать с `PUBLIC_KEY` в `core/updater.py`.
pub const PUBLIC_KEY: &str = "P8wubYo5/aYU2V6EhrbMfmmDnUdr5wh70ujfa02yapg=";
/// Аварийный обход проверки подписи. В продакшене всегда `false`.
pub const ALLOW_UNSIGNED: bool = false;
/// Репозиторий релизов.
pub const GITHUB_REPO: &str = "SchazyDev/MusicTools";
/// Событие «найдено обновление при старте».
pub const EVENT_UPDATE: &str = "update://available";

const PLACEHOLDER_KEYS: [&str; 2] = ["", "PLACEHOLDER_REPLACE_ME"];
const USER_AGENT: &str = "MusicTools-Updater";
const API_TIMEOUT: Duration = Duration::from_secs(8);
const DOWNLOAD_TIMEOUT: Duration = Duration::from_secs(30);

/// Описание доступного обновления.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateInfo {
    pub version: String,
    pub url: String,
    pub sha256: String,
    pub size: u64,
    pub notes: String,
    pub channel: String,
}

/// Ошибка целостности скачанного файла.
#[derive(Debug, thiserror::Error)]
#[error("SHA-256 скачанного файла не совпадает с манифестом")]
pub struct IntegrityError;

// ---------------------------------------------------------------------------
// Подпись
// ---------------------------------------------------------------------------

/// Канонические байты: манифест без `signature`, ключи отсортированы,
/// разделители `,` и `:`, без ASCII-экранирования (UTF-8).
pub fn canonical_manifest_bytes(manifest: &serde_json::Value) -> Result<Vec<u8>, String> {
    let Some(object) = manifest.as_object() else {
        return Err("манифест: ожидался JSON-объект".to_owned());
    };
    let payload: serde_json::Map<String, serde_json::Value> = object
        .iter()
        .filter(|(key, _)| key.as_str() != "signature")
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();

    // `serde_json::Map` без фичи `preserve_order` — это BTreeMap, поэтому
    // ключи уже отсортированы, как `sort_keys=True` в Python.
    serde_json::to_vec(&serde_json::Value::Object(payload))
        .map_err(|e| format!("манифест: сериализация не удалась: {e}"))
}

/// Ключ задан и не является плейсхолдером.
pub fn has_usable_key(public_key: &str) -> bool {
    !PLACEHOLDER_KEYS.contains(&public_key.trim())
}

/// Проверяет Ed25519-подпись манифеста. Никогда не паникует.
pub fn verify_manifest_signature(
    manifest_bytes: &[u8],
    signature: &serde_json::Value,
    public_key: &str,
) -> bool {
    if !has_usable_key(public_key) {
        return false;
    }

    let Some(object) = signature.as_object() else { return false };
    if object.get("algorithm").and_then(|v| v.as_str()) != Some("ed25519") {
        if object.get("algorithm").and_then(|v| v.as_str()) == Some("minisign") {
            log::error!("подпись minisign не поддерживается, требуется ed25519");
        }
        return false;
    }

    let Some(value) = object.get("value").and_then(|v| v.as_str()) else { return false };
    if value.is_empty() {
        return false;
    }

    let engine = base64::engine::general_purpose::STANDARD;
    let Ok(signature_bytes) = engine.decode(value) else {
        log::error!("манифест: подпись не декодируется из base64");
        return false;
    };
    let Ok(key_bytes) = engine.decode(public_key.trim()) else {
        log::error!("манифест: открытый ключ не декодируется из base64");
        return false;
    };
    if signature_bytes.len() != 64 || key_bytes.len() != 32 {
        return false;
    }

    let Ok(key_array) = <[u8; 32]>::try_from(key_bytes.as_slice()) else { return false };
    let Ok(signature_array) = <[u8; 64]>::try_from(signature_bytes.as_slice()) else {
        return false;
    };

    let key = ed25519_dalek::VerifyingKey::from_bytes(&key_array);
    match key {
        Ok(key) => {
            let signature = ed25519_dalek::Signature::from_bytes(&signature_array);
            key.verify_strict(manifest_bytes, &signature).is_ok()
        }
        Err(e) => {
            log::error!("манифест: некорректный открытый ключ: {e}");
            false
        }
    }
}

// ---------------------------------------------------------------------------
// Версии
// ---------------------------------------------------------------------------

/// Разобранная версия: числа выпуска и предрелиз (`b1`, `rc2`, …).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Version {
    release: Vec<u64>,
    pre: Option<(u8, u64)>,
}

/// Порядок предрелизов: alpha < beta < rc < финал.
fn pre_rank(tag: &str) -> Option<u8> {
    match tag.to_ascii_lowercase().as_str() {
        "a" | "alpha" => Some(0),
        "b" | "beta" => Some(1),
        "rc" | "c" | "pre" | "preview" => Some(2),
        _ => None,
    }
}

/// Разбирает версию: ведущая `v`, числа через точку, необязательный
/// предрелизный суффикс. Возвращает `None` для мусора.
pub fn parse_version(raw: &str) -> Option<Version> {
    let text = raw.trim();
    let text = text.strip_prefix(['v', 'V']).unwrap_or(text);
    if text.is_empty() {
        return None;
    }

    // Отрезаем локальную сборку (`+build`) — она не влияет на порядок.
    let text = text.split('+').next().unwrap_or(text);

    let mut release: Vec<u64> = Vec::new();
    let mut chars = text.char_indices().peekable();
    let mut pre_start: Option<usize> = None;

    while let Some((index, ch)) = chars.next() {
        if ch.is_ascii_digit() || ch == '.' {
            continue;
        }
        if ch.is_ascii_alphabetic() {
            pre_start = Some(index);
            break;
        }
        return None;
    }

    let release_part = match pre_start {
        Some(index) => &text[..index],
        None => text,
    };

    for part in release_part.split('.') {
        if part.is_empty() {
            return None;
        }
        release.push(part.parse::<u64>().ok()?);
    }
    if release.is_empty() {
        return None;
    }

    let pre = match pre_start {
        None => None,
        Some(index) => {
            let suffix = &text[index..];
            let digits_start = suffix.find(|c: char| c.is_ascii_digit());
            let (tag, number) = match digits_start {
                Some(pos) => (&suffix[..pos], suffix[pos..].parse::<u64>().ok()?),
                None => (suffix, 0),
            };
            let rank = pre_rank(tag)?;
            Some((rank, number))
        }
    };

    Some(Version { release, pre })
}

/// `true`, только если `remote` — корректная версия строго больше `local`.
pub fn is_newer(remote: &str, local: &str) -> bool {
    let (Some(remote), Some(local)) = (parse_version(remote), parse_version(local)) else {
        return false;
    };
    remote > local
}

impl Ord for Version {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        let len = self.release.len().max(other.release.len());
        for index in 0..len {
            let left = self.release.get(index).copied().unwrap_or(0);
            let right = other.release.get(index).copied().unwrap_or(0);
            match left.cmp(&right) {
                std::cmp::Ordering::Equal => {}
                other => return other,
            }
        }
        match (self.pre, other.pre) {
            (None, None) => std::cmp::Ordering::Equal,
            (None, Some(_)) => std::cmp::Ordering::Greater,
            (Some(_), None) => std::cmp::Ordering::Less,
            (Some(left), Some(right)) => left.cmp(&right),
        }
    }
}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

// ---------------------------------------------------------------------------
// Проверка обновлений
// ---------------------------------------------------------------------------

fn client(timeout: Duration) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .user_agent(USER_AGENT)
        .timeout(timeout)
        .build()
        .map_err(|e| format!("не удалось создать HTTP-клиент: {e}"))
}

async fn request_json(url: &str, timeout: Duration) -> Result<serde_json::Value, String> {
    let response = client(timeout)?
        .get(url)
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|e| format!("сетевая ошибка: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("сервер вернул {}", response.status()));
    }
    response.json().await.map_err(|e| format!("некорректный JSON: {e}"))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|c| c.is_ascii_hexdigit())
}

/// Загружает и проверяет манифест последнего релиза.
pub async fn fetch_latest_manifest(
    repo: &str,
    channel: &str,
) -> Result<Option<UpdateInfo>, String> {
    let release = request_json(
        &format!("https://api.github.com/repos/{repo}/releases/latest"),
        API_TIMEOUT,
    )
    .await?;

    let manifest_url = release
        .get("assets")
        .and_then(|v| v.as_array())
        .and_then(|assets| {
            assets.iter().find_map(|asset| {
                let name = asset.get("name").and_then(|v| v.as_str())?;
                if name == MANIFEST_ASSET_NAME {
                    asset.get("browser_download_url").and_then(|v| v.as_str())
                } else {
                    None
                }
            })
        });

    let Some(manifest_url) = manifest_url else {
        log::warn!(
            "релиз {} не содержит {} — обновление недоступно",
            release.get("tag_name").and_then(|v| v.as_str()).unwrap_or("?"),
            MANIFEST_ASSET_NAME
        );
        return Ok(None);
    };

    let manifest = request_json(manifest_url, API_TIMEOUT).await?;
    if !manifest.is_object() {
        log::error!("манифест обновления: ожидался JSON-объект");
        return Ok(None);
    }

    // --- Подпись проверяется до доверия url/sha256/size ---
    match manifest.get("signature") {
        Some(signature) if signature.is_object() => {
            let bytes = canonical_manifest_bytes(&manifest)?;
            if !verify_manifest_signature(&bytes, signature, PUBLIC_KEY) {
                log::error!("манифест обновления: подпись недействительна — обновление отклонено");
                return Ok(None);
            }
            log::info!("манифест обновления: подпись проверена");
        }
        _ => {
            if channel == "stable" && !ALLOW_UNSIGNED && has_usable_key(PUBLIC_KEY) {
                log::error!(
                    "манифест без подписи: канал stable работает fail-closed, обновление отклонено"
                );
                return Ok(None);
            }
            if !ALLOW_UNSIGNED {
                log::warn!("манифест обновления без подписи (канал {channel}) — пропуск");
            }
        }
    }

    let Some(version) = manifest.get("version").and_then(|v| v.as_str()) else {
        log::warn!("манифест обновления: нет версии");
        return Ok(None);
    };
    if parse_version(version).is_none() {
        log::warn!("манифест обновления: некорректная версия {version:?}");
        return Ok(None);
    }

    if let Some(manifest_channel) = manifest.get("channel").and_then(|v| v.as_str()) {
        if manifest_channel != channel {
            log::warn!("манифест канала {manifest_channel:?} не совпадает с {channel:?}");
            return Ok(None);
        }
    }

    let Some(platform) = manifest
        .get("platforms")
        .and_then(|v| v.get(PLATFORM_KEY))
        .and_then(|v| v.as_object())
    else {
        log::warn!("манифест обновления: нет записи для {PLATFORM_KEY}");
        return Ok(None);
    };

    let url = platform.get("url").and_then(|v| v.as_str()).unwrap_or("");
    let sha256 = platform.get("sha256").and_then(|v| v.as_str()).unwrap_or("");
    let size = platform.get("size").and_then(|v| v.as_u64()).unwrap_or(0);

    if !url.starts_with("https://") {
        log::warn!("манифест обновления: некорректный url");
        return Ok(None);
    }
    if !is_sha256(sha256) {
        log::warn!("манифест обновления: некорректный sha256");
        return Ok(None);
    }
    if size == 0 {
        log::warn!("манифест обновления: некорректный size");
        return Ok(None);
    }

    Ok(Some(UpdateInfo {
        version: version.trim().trim_start_matches(['v', 'V']).to_owned(),
        url: url.to_owned(),
        sha256: sha256.to_ascii_lowercase(),
        size,
        notes: manifest
            .get("notes")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned(),
        channel: manifest
            .get("channel")
            .and_then(|v| v.as_str())
            .unwrap_or(channel)
            .to_owned(),
    }))
}

/// Возвращает обновление, если оно новее текущей версии.
pub async fn check_for_updates(
    repo: &str,
    channel: &str,
    current_version: &str,
) -> Option<UpdateInfo> {
    match fetch_latest_manifest(repo, channel).await {
        Ok(Some(info)) => {
            if is_newer(&info.version, current_version) {
                log::info!("доступна новая версия: {}", info.version);
                Some(info)
            } else {
                log::info!("обновление не требуется: {} <= {current_version}", info.version);
                None
            }
        }
        Ok(None) => None,
        Err(e) => {
            log::warn!("проверка обновлений не удалась: {e}");
            None
        }
    }
}

// ---------------------------------------------------------------------------
// Загрузка и установка
// ---------------------------------------------------------------------------

/// Скачивает файл обновления и сверяет SHA-256.
pub async fn download_update(
    info: &UpdateInfo,
    destination: &Path,
    mut on_progress: impl FnMut(u8),
) -> Result<(), String> {
    use futures_util::StreamExt as _;
    use tokio::io::AsyncWriteExt as _;

    let response = client(DOWNLOAD_TIMEOUT)?
        .get(&info.url)
        .send()
        .await
        .map_err(|e| format!("не удалось начать загрузку: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("сервер вернул {}", response.status()));
    }

    let mut file = tokio::fs::File::create(destination)
        .await
        .map_err(|e| format!("не удалось создать файл обновления: {e}"))?;

    let mut hasher = Sha256::new();
    let mut downloaded: u64 = 0;
    let mut last: u8 = 0;
    let mut stream = response.bytes_stream();

    while let Some(chunk) = stream.next().await {
        let chunk = match chunk {
            Ok(chunk) => chunk,
            Err(e) => {
                drop(file);
                let _ = tokio::fs::remove_file(destination).await;
                return Err(format!("ошибка загрузки: {e}"));
            }
        };
        hasher.update(&chunk);
        file.write_all(&chunk)
            .await
            .map_err(|e| format!("ошибка записи: {e}"))?;
        downloaded += chunk.len() as u64;

        if info.size > 0 {
            let percent = ((downloaded.saturating_mul(100)) / info.size).min(100) as u8;
            if percent != last {
                last = percent;
                on_progress(percent);
            }
        }
    }

    file.flush().await.map_err(|e| format!("ошибка записи: {e}"))?;
    drop(file);

    let actual = format!("{:x}", hasher.finalize());
    if actual != info.sha256 {
        let _ = tokio::fs::remove_file(destination).await;
        log::error!("SHA-256 не совпал: ожидался {}, получен {actual}", info.sha256);
        return Err(IntegrityError.to_string());
    }

    log::info!("обновление скачано и проверено: {}", destination.display());
    Ok(())
}

/// Скрипт отложенной замены exe с бэкапом и ограниченным числом попыток.
pub fn build_update_script(current_exe: &Path, new_exe: &Path, temp_dir: &Path) -> String {
    let current = current_exe.display();
    let new = new_exe.display();
    let temp = temp_dir.display();
    format!(
        "@echo off\r\n\
setlocal\r\n\
set /a attempts=0\r\n\
\r\n\
:wait\r\n\
set /a attempts+=1\r\n\
if %attempts% GTR 30 goto giveup\r\n\
move /Y \"{current}\" \"{current}.old\" >nul 2>&1\r\n\
if errorlevel 1 (\r\n\
    timeout /t 1 /nobreak >nul\r\n\
    goto wait\r\n\
)\r\n\
\r\n\
move /Y \"{new}\" \"{current}\" >nul 2>&1\r\n\
if errorlevel 1 (\r\n\
    move /Y \"{current}.old\" \"{current}\" >nul 2>&1\r\n\
    goto giveup\r\n\
)\r\n\
\r\n\
if exist \"{current}.old\" del /f /q \"{current}.old\" >nul 2>&1\r\n\
start \"\" \"{current}\"\r\n\
rd /s /q \"{temp}\" >nul 2>&1\r\n\
del \"%~f0\" >nul 2>&1\r\n\
exit /b 0\r\n\
\r\n\
:giveup\r\n\
exit /b 1\r\n"
    )
}

/// Скачивает обновление и запускает отложенную установку.
///
/// Возвращает сообщение для пользователя. В отладочной сборке отказывает,
/// как PyQt-версия при отсутствии `sys.frozen`.
pub async fn perform_update(
    info: &UpdateInfo,
    current_exe: &Path,
    mut on_progress: impl FnMut(u8),
) -> Result<String, String> {
    if cfg!(debug_assertions) {
        log::warn!("perform_update вызван вне собранного приложения — отказ");
        return Err("Обновление доступно только в собранной версии приложения.".to_owned());
    }

    let temp_dir = create_temp_dir().map_err(|e| format!("не удалось создать временный каталог: {e}"))?;
    let new_exe = temp_dir.join(
        current_exe
            .file_name()
            .map(|name| name.to_owned())
            .unwrap_or_else(|| std::ffi::OsString::from("MusicTools_new.exe")),
    );

    if let Err(e) = download_update(info, &new_exe, &mut on_progress).await {
        remove_dir_quietly(&temp_dir);
        return Err(e);
    }

    let script_path = temp_dir.join("apply_update.bat");
    let script = build_update_script(current_exe, &new_exe, &temp_dir);
    if let Err(e) = std::fs::write(&script_path, script) {
        remove_dir_quietly(&temp_dir);
        return Err(format!("не удалось записать скрипт обновления: {e}"));
    }

    let mut command = std::process::Command::new("cmd");
    command
        .arg("/C")
        .arg(&script_path)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        command.creation_flags(0x0800_0000);
    }

    command
        .spawn()
        .map_err(|e| format!("не удалось запустить установку обновления: {e}"))?;

    log::info!("скрипт обновления запущен: {}", script_path.display());
    Ok("Обновление загружено, приложение будет перезапущено.".to_owned())
}

fn create_temp_dir() -> std::io::Result<PathBuf> {
    let base = std::env::temp_dir();
    for attempt in 0..64u32 {
        let candidate = base.join(format!("music_tools_update_{}_{attempt}", std::process::id()));
        match std::fs::create_dir(&candidate) {
            Ok(()) => return Ok(candidate),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::AlreadyExists,
        "не удалось создать временный каталог",
    ))
}

fn remove_dir_quietly(dir: &Path) {
    if dir.exists() {
        if let Err(e) = std::fs::remove_dir_all(dir) {
            log::warn!("не удалось удалить {}: {e}", dir.display());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_bytes_are_sorted_and_compact() {
        let manifest = serde_json::json!({
            "version": "2.0.0",
            "signature": {"algorithm": "ed25519", "value": "zzz"},
            "channel": "stable",
            "notes": "Заметки\nвторая строка",
        });
        let bytes = canonical_manifest_bytes(&manifest).expect("канонизация");
        let text = String::from_utf8(bytes).expect("utf-8");
        assert_eq!(
            text,
            "{\"channel\":\"stable\",\"notes\":\"Заметки\\nвторая строка\",\"version\":\"2.0.0\"}"
        );
    }

    #[test]
    fn signature_round_trip() {
        use ed25519_dalek::{Signer as _, SigningKey};

        let signing = SigningKey::from_bytes(&[7u8; 32]);
        let verifying = signing.verifying_key();
        let public_key = base64::engine::general_purpose::STANDARD.encode(verifying.as_bytes());

        let manifest = serde_json::json!({"version": "2.0.0", "channel": "stable"});
        let bytes = canonical_manifest_bytes(&manifest).expect("канонизация");
        let signature = signing.sign(&bytes);
        let signature_json = serde_json::json!({
            "algorithm": "ed25519",
            "key_id": "test",
            "value": base64::engine::general_purpose::STANDARD.encode(signature.to_bytes()),
        });

        assert!(verify_manifest_signature(&bytes, &signature_json, &public_key));
        assert!(!verify_manifest_signature(b"other", &signature_json, &public_key));
        assert!(!verify_manifest_signature(&bytes, &signature_json, "PLACEHOLDER_REPLACE_ME"));
        assert!(!verify_manifest_signature(
            &bytes,
            &serde_json::json!({"algorithm": "minisign", "value": "x"}),
            &public_key
        ));
    }

    #[test]
    fn real_manifest_signature_verifies_with_shipped_key() {
        // Манифест подписан настоящим scripts/make_manifest.py и ключом из
        // keys/private.key; проверяется закреплённым PUBLIC_KEY. Тест ловит
        // расхождение между ключом в core/updater.py и в Rust.
        let raw = include_str!("../../tests/fixtures/signed_manifest.json");
        let manifest: serde_json::Value = serde_json::from_str(raw).expect("фикстура");
        let signature = manifest.get("signature").expect("подпись");
        let bytes = canonical_manifest_bytes(&manifest).expect("канонизация");

        assert!(has_usable_key(PUBLIC_KEY), "PUBLIC_KEY должен быть реальным ключом");
        assert!(
            verify_manifest_signature(&bytes, signature, PUBLIC_KEY),
            "подпись действительного манифеста не прошла проверку"
        );

        // Подделка хотя бы одного поля ломает подпись.
        let mut tampered = manifest.clone();
        tampered["version"] = serde_json::json!("9.9.9");
        let tampered_bytes = canonical_manifest_bytes(&tampered).expect("канонизация");
        assert!(!verify_manifest_signature(&tampered_bytes, signature, PUBLIC_KEY));
    }

    #[test]
    fn version_ordering_matches_pep440_for_releases() {        assert!(is_newer("2.0.0", "1.1.0"));
        assert!(is_newer("v1.2.0", "1.1.9"));
        assert!(!is_newer("1.1.0", "1.1.0"));
        assert!(!is_newer("1.0.9", "1.1.0"));
        assert!(is_newer("2.0.0", "2.0.0b3"));
        assert!(is_newer("2.0.0rc1", "2.0.0b3"));
        assert!(is_newer("2.0.0b10", "2.0.0b9"));
        assert!(!is_newer("мусор", "1.0.0"));
    }

    #[test]
    fn update_script_replaces_and_restarts() {
        let script = build_update_script(
            Path::new("C:/Apps/Music Tools.exe"),
            Path::new("C:/Temp/new.exe"),
            Path::new("C:/Temp/update"),
        );
        assert!(script.contains("move /Y \"C:/Apps/Music Tools.exe\""));
        assert!(script.contains("start \"\" \"C:/Apps/Music Tools.exe\""));
        assert!(script.contains(":giveup"));
    }

    #[test]
    fn sha256_validation_accepts_only_hex64() {
        assert!(is_sha256(&"a".repeat(64)));
        assert!(!is_sha256(&"g".repeat(64)));
        assert!(!is_sha256("abc"));
    }
}
