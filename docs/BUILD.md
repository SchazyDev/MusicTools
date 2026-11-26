# Сборка Music Tools

Документ описывает подготовку окружения, локальный запуск, сборку
исполняемого файла и установщика, а также запуск тестов.

Основное приложение — Tauri 2 (Rust) плюс интерфейс на Svelte. PyQt-версия
заморожена на 1.1.0 и собирается только для критических исправлений
(ветка `legacy/pyqt`).

## Требования

- Windows 10 или Windows 11, x64.
- Node.js 20 или новее и npm.
- Rust через [rustup](https://rustup.rs) (устойчивый канал, edition 2021).
- Microsoft C++ Build Tools: MSVC и Windows SDK.
- WebView2 Runtime — в Windows 11 он есть, в Windows 10 ставится
  автоматически или берётся из установщика (`webviewInstallerMode` в
  `tauri.conf.json`).
- Python 3.13 — только для скриптов подготовки (`prepare_sidecars.py`,
  `make_manifest.py`) и тестов Python-ветки.

Git LFS не нужен: тяжёлые бинарники в репозитории не хранятся.

## Сайдкары

`yt-dlp.exe` и `ffmpeg.exe` в git не попадают (`.gitignore`) и
подготавливаются скриптом:

```powershell
python scripts/prepare_sidecars.py            # скачать yt-dlp, скопировать ffmpeg
python scripts/prepare_sidecars.py --check    # только проверить
python scripts/prepare_sidecars.py --force    # перезаписать
```

`yt-dlp.exe` скачивается с последнего выпуска GitHub. `ffmpeg.exe`
копируется из корня репозитория или из `PATH`; если его там нет, положите
`ffmpeg.exe` в корень проекта или установите ffmpeg через
`winget install Gyan.FFmpeg`.

Файлы кладутся в `src-tauri/resources` и попадают в установщик
(`bundle.resources`). Сборка отказывается собирать установщик без них:
`beforeBuildCommand` запускает `npm run sidecars:check`.

## Окружение

```powershell
npm ci
npm install -g @tauri-apps/cli   # необязательно: CLI уже в devDependencies
```

CLI из `devDependencies` вызывается через `npx tauri`, поэтому глобальная
установка не обязательна.

## Локальный запуск

```powershell
npm run tauri dev
```

В отладочной сборке каталог ресурсов — `src-tauri/resources`, поэтому
сайдкары находятся без установки. Журнал пишется рядом с exe в
`src-tauri/target/debug`, уровень меняется переменной
`MUSIC_TOOLS_LOG_LEVEL` (`debug`, `info`, `warn`).

## Сборка установщика

```powershell
python scripts/prepare_sidecars.py
npm run tauri build
```

Результат:

- `src-tauri/target/release/music-tools.exe` — исполняемый файл;
- `src-tauri/target/release/bundle/nsis/Music Tools_<версия>_x64-setup.exe`;
- `src-tauri/target/release/bundle/msi/Music Tools_<версия>_x64_<язык>.msi`.

Установщик ставит приложение в профиль пользователя (`currentUser`) и не
требует прав администратора.

Сборка без установщика (быстрее, для проверки):

```powershell
npm run tauri build -- --no-bundle
```

## Тесты

```powershell
npm run check                      # svelte-check: типы интерфейса
cargo test --manifest-path src-tauri/Cargo.toml
python -m pytest -q                # Python-ветка (совместимость, скрипты)
```

Часть тестов Rust помечена `#[ignore]` и обращается к настоящему окружению
или сети. Они запускаются вручную:

```powershell
cargo test --manifest-path src-tauri/Cargo.toml -- --ignored
```

- `resolves_real_environment` — находит ли `yt-dlp` и `ffmpeg`;
- `updates_real_binary` — скачивает yt-dlp с GitHub и ставит в
  `%LOCALAPPDATA%\MusicTools\bin`;
- `download_real_url` — предпросмотр и скачивание mp3; адрес задаётся
  переменной `MUSIC_TOOLS_TEST_URL`, по умолчанию Vimeo (нужен интернет,
  некоторые сайты требуют входа);
- `convert_real_files` — конвертация в wav; нужен mp3 по пути из
  `MUSIC_TOOLS_TEST_SAMPLE`.

## Подпись обновлений

Пара ключей Ed25519 создаётся один раз:

```powershell
python scripts/gen_keys.py --out-dir keys
```

- `keys/public.key` — коммитится и вставляется в `PUBLIC_KEY` в
  `core/updater.py` и `src-tauri/src/update/mod.rs` (тест
  `test_python_and_rust_share_the_same_public_key` следит за совпадением);
- `keys/private.key` — **никогда не коммитится**, он в `.gitignore`.
  Храните резервную копию отдельно: без него выпускать обновления нельзя.

Манифест для релиза:

```powershell
python scripts/make_manifest.py --version 2.0.1 `
    --file "src-tauri/target/release/bundle/nsis/Music Tools_2.0.1_x64-setup.exe" `
    --url https://github.com/SchazyDev/MusicTools/releases/download/v2.0.1/Music-Tools_2.0.1_x64-setup.exe `
    --key keys/private.key --channel stable --out latest.json
python scripts/make_manifest.py --verify latest.json --public-key keys/public.key
```

`latest.json` загружается в релиз GitHub как отдельный ассет. Проверка
подписи для канала `stable` работает fail-closed: манифест без подписи или
с неверной подписью отклоняется.

## Python-ветка (заморожена)

Сборка PyQt-версии нужна только для исправлений ветки `legacy/pyqt`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python main.py
pyinstaller main.spec
```

Inno Setup (`iscc`) нужен только для установщика PyQt-версии.
