# Архитектура Music Tools 2.0 (Tauri 2)

Документ описывает перевод приложения с PyQt5 на Tauri 2. Исходная версия
заморожена на ветке `legacy/pyqt` (v1.1.0) и остаётся источником истины по
поведению до достижения паритета. Документ ведётся вместе с кодом: любое
изменение контракта IPC обязано появиться здесь в том же коммите.

Весь пользовательский текст — на русском, без эмодзи.

---

## 1. Почему Tauri, а не Electron и не веб-сервер

Tauri собирает интерфейс в системный WebView2, а логику — в нативный Rust.
Итоговый установщик и потребление памяти заметно меньше, чем у Electron,
а Rust-часть напрямую работает с реестром Windows, файловой системой и
дочерними процессами. Node используется только на этапе сборки (Vite), в
готовом приложении нет ни Node, ни локального HTTP-сервера: страница
загружается из `frontendDist`, а вызовы идут через IPC `invoke`/`event`.

Небольшая цена — привязка к WebView2 на Windows. Для одной целевой
платформы это приемлемо: runtime WebView2 входит в Windows 10/11 и
доставляется бутстраппером (`webviewInstallMode` в `tauri.conf.json`).

---

## 2. Выбор фронтенда

Выбран Vite + TypeScript + Svelte 5 (руны). Причины:

* Svelte 5 компилирует компоненты в обычный JS без виртуального DOM —
  для списка файлов конвертера это дешевле React;
* руны (`$state`, `$props`, `$derived`) дают локальное реактивное
  состояние без библиотеки сторов, что хорошо ложится на принцип
  «одна вкладка — один компонент»;
* TypeScript-модель IPC держится рядом с Rust-структурами и проверяется
  `svelte-check`.

Отвергнутые варианты: чистый JS без сборщика — неудобно с `@tauri-apps/api`
и типами; React — лишний рантайм без выигрыша; отдельный сервер — нарушает
требование не держать Node-сервер в проде.

Конфигурация Svelte — `svelte.config.js` в формате ES-модуля, а не JSON:
в проекте включён `"type": "module"`, и загрузчик Node исполняет `.js` как
модуль. Плагин `@sveltejs/vite-plugin-svelte` подключается в
`vite.config.ts` и сам находит конфиг.

---

## 3. Структура репозитория

```
H:\Development\FaunaTools
├── index.html                 точка входа Vite
├── package.json               фронтенд (Vite, Svelte 5, TypeScript)
├── vite.config.ts
├── svelte.config.js
├── tsconfig.json
├── src/                       исходники интерфейса
│   ├── main.ts
│   ├── App.svelte             вкладки из реестра инструментов
│   ├── lib/ipc.ts             единственное место с именами команд и событий
│   ├── lib/panes.ts           вид инструмента -> Svelte-компонент
│   ├── components/DemoJob.svelte
│   ├── tools/DownloadPane.svelte
│   ├── tools/ConvertPane.svelte
│   ├── tools/AboutPane.svelte
│   └── styles/global.css
├── src-tauri/                 нативная часть
│   ├── Cargo.toml
│   ├── build.rs
│   ├── tauri.conf.json
│   ├── capabilities/default.json
│   ├── icons/                 иконки приложения и трея
│   ├── resources/             sidecar-бинарники (позже)
│   └── src/
│       ├── main.rs            точка входа бинарника
│       ├── lib.rs             сборка tauri::Builder, setup, generate_handler!
│       ├── state.rs           AppState
│       ├── jobs.rs            реестр заданий, JobContext, CancelToken
│       ├── tools_registry.rs  ToolDescriptor / ToolRegistry
│       ├── commands.rs        IPC-команды
│       ├── settings.rs        %APPDATA%\MusicTools\settings.json
│       ├── logging.rs         music_tools.log рядом с exe
│       ├── tray.rs            трей, автозапуск, скрытие при закрытии
│       └── tools/
│           ├── mod.rs
│           ├── download/      фаза 1 (yt-dlp)
│           ├── convert/       фаза 2 (ffmpeg)
│           └── sidecar/       общий запуск и контроль дочерних процессов
├── dist/                      результат сборки фронтенда (артефакт)
└── docs/TAURI_ARCHITECTURE.md этот документ
```

Python-часть (`main.py`, `core/`, `ui/`, `utils/`, `tests/`, скрипты и
конфигурация сборки) не изменяется ни на одном шаге миграции.

---

## 4. Модули `src-tauri/src`

`main.rs` — только вызов `music_tools_lib::run()` и атрибут
`windows_subsystem = "windows"` для релизной сборки без консоли.

`lib.rs` — инициализация журнала, `JobRegistry`, `AppState`, трея и
обработчика закрытия окна, регистрация команд в `generate_handler!`.
Здесь же `resolve_resources_dir`: в отладочной сборке — `src-tauri/resources`,
в релизе — каталог ресурсов установки.

`state.rs` — `AppState`, управляемый через `tauri::State`: реестр
инструментов, `Arc<JobRegistry>`, настройки под `Mutex`, каталог ресурсов,
путь журнала, версия и флаг завершения.

`jobs.rs` — единая модель заданий (раздел 5).

`tools_registry.rs` — реестр инструментов (раздел 6).

`commands.rs` — IPC-команды (раздел 7). Команды тонкие: валидируют
аргументы, обращаются к `AppState` и `JobRegistry`, возвращают результат.
Дольше нескольких миллисекунд они не работают.

`settings.rs` — чтение и атомарная запись `{ "channel", "check_on_startup",
"close_to_tray" }` в `%APPDATA%\MusicTools\settings.json`. Канал — только
`stable` или `beta`; при порче файла возвращаются умолчания; неизвестные
ключи отбрасываются. Формат совместим с PyQt-версией, поэтому переход
не сбрасывает настройки пользователя.

`logging.rs` — файловый логгер `music_tools.log` рядом с exe, ротация по
2 МБ, три архивных файла, уровень из `MUSIC_TOOLS_LOG_LEVEL`.

`tray.rs` — левый клик переключает окно, меню «Открыть», «Запускать при
старте», «Выход». Автозапуск — значение `Music Tools` в
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Закрытие окна
по умолчанию прячет его в трей.

`tools/download`, `tools/convert`, `tools/sidecar` — будущие задания
скачивания и конвертации и общий контроль дочерних процессов.
Соответствуют `core/downloader.py`, `core/converter.py`, `core/ffmpeg_utils.py`.

---

## 5. Единая модель заданий

Все длительные действия — скачивание, конвертация, проверка обновления,
будущие метаданные и разбиение на стемы — подчиняются одному контракту.

Свойства, обязательные для любого задания:

* команда возвращает `job_id` немедленно, работа идёт в tokio-runtime;
* прогресс и статусы публикуются через `JobContext` и доходят до интерфейса
  одним событием `job://event`;
* отмена идемпотентна и проверяется между шагами (`check_cancel`);
* дочерние процессы получают kill-tree при отмене и при выходе приложения;
* терминальное событие приходит ровно один раз: `ok`, `canceled` или `failed`.

Скелет (фактический код `src-tauri/src/jobs.rs`):

```rust
pub struct CancelToken(Arc<AtomicBool>);        // cancel() идемпотентен

pub enum JobOutcome {                            // терминальный результат
    Ok { message: String },
    Canceled,
    Failed { message: String },
}

pub enum JobError {                              // зеркало CancelException
    Cancelled,
    Failed(String),
}

pub enum JobEvent {                              // состояние работы
    Progress { percent: u8, file: Option<String> },
    Status { message: String },
    Ok { message: String },
    Canceled,
    Failed { message: String },
}

pub struct JobContext {                          // передаётся в тело задания
    job_id: u64,
    kind: String,
    tx: broadcast::Sender<JobEventEnvelope>,
    cancel: CancelToken,
}

impl JobContext {
    pub fn progress(&self, percent: u8);
    pub fn progress_file(&self, percent: u8, file: impl Into<String>);
    pub fn status(&self, message: impl Into<String>);
    pub fn check_cancel(&self) -> JobResult<()>;
    pub async fn sleep(&self, ms: u64) -> JobResult<()>;
    pub fn guarded<T: Into<String>>(
        &self,
        work: impl FnOnce(&JobContext) -> JobResult<T>,
    ) -> JobOutcome;
}

impl JobRegistry {
    pub fn new(app: AppHandle) -> Self;
    pub fn spawn<F>(&self, kind: impl Into<String>, body: F) -> u64
    where
        F: FnOnce(JobContext)
            -> Pin<Box<dyn Future<Output = JobOutcome> + Send>>
            + Send + 'static;
    pub fn cancel(&self, id: u64) -> bool;
    pub fn snapshot(&self) -> Vec<JobSnapshot>;
}
```

Типичное задание — замыкание от контекста к `Box::pin(async move { ... })`.
Внутри: `check_cancel`, `status`, затем вызов функции из `tools::*`,
которая регулярно докладывает прогресс. Пример уже работает в команде
`start_demo_job`; на фазе 1 вместо цикла окажется
`yt_dlp::download(ctx, params)`.

События идут через `tokio::sync::broadcast`, и одна подписка в реестре
превращает их в `AppHandle::emit("job://event", ...)`. Такой канал развязывает
регистрацию задания (в команде) и отправку в WebView (в клиенте):
задание публикует события через контекст, а реестр ведёт их учёт и
рассылку.

Снимки состояния защищены `RwLock`; держать их дольше проверки и записи
статуса нельзя. Отдельный `JoinHandle` не хранится: исход задания
фиксируется в его теле, после чего задача завершается.

---

## 6. Реестр инструментов

`ToolDescriptor` — прямой перенос `core/tools/base.py`: идентификатор,
русский заголовок, порядок, включённость, иконка. Вместо фабрики виджета
(`factory: Callable[[], QWidget]`) клиент получает `kind` и находит
компонент в `src/lib/panes.ts`. Это позволяет генерировать вкладки, не
таща в Rust знание о Svelte.

```rust
pub struct ToolDescriptor {
    pub id: String,            // ^[a-z0-9-]+$
    pub title: String,         // русский заголовок вкладки
    pub order: i32,            // сортировка по (order, title)
    pub enabled: bool,
    pub icon: Option<String>,
    pub kind: ToolKind,        // download | convert | about
    pub description: String,   // подсказка вкладки
}
```

`ToolRegistry` повторяет `core/tools/registry.py`: `register` проверяет
слаг и уникальность, `all` отдаёт список по (order, title), `enabled`
фильтрует по `enabled`. Ошибки зеркалят проверки PyQt-версии: недопустимый
слаг, пустой заголовок, дубликат идентификатора.

Три штатных инструмента — `download` (10), `convert` (20), `about` (90).
Вкладки строит `App.svelte` по ответу `get_tools`; фронтенд не знает
их заранее. Добавление инструмента фазы 3 (`metadata`, `bpm`, `stems`) —
одна запись в `builtin_tools`, новый вариант `ToolKind`, новый компонент
в `panes.ts` и новые команды `start_*`/`preview_*`. Существующий код,
команды и события не меняются.

---

## 7. IPC-контракт

Соглашения:

* команды в snake_case; во фронтенде аргументы передаются в camelCase
  (`jobId` для `job_id`), Tauri сам приводит имена к Rust-полям;
* любая ошибка — `Err(String)` с русским текстом; успех — сериализуемая
  структура;
* длительные операции возвращают `u64` (`job_id`), а не прогресс;
* структуры сериализуются в snake_case — типы TS в `src/lib/ipc.ts`
  повторяют имена полей один к одному.

Команды:

| Команда | Аргументы | Результат | Назначение |
|---|---|---|---|
| `app_info` | — | `AppInfo` | версия, пути, ОС, признак отладочной сборки |
| `get_tools` | — | `ToolDescriptor[]` | вкладки из реестра |
| `get_settings` | — | `Settings` | текущие настройки |
| `update_settings` | `patch: object` | `Settings` | частичное обновление, атомарная запись |
| `start_demo_job` | `steps?: number` | `number` | пример задания с прогрессом и отменой |
| `cancel_job` | `jobId: number` | `boolean` | запрос отмены |
| `list_jobs` | — | `JobSnapshot[]` | живые и завершённые задания |
| `hide_to_tray` | — | — | скрыть окно |
| `show_main_window` | — | — | показать окно из трея |
| `open_log_file` | — | — | открыть журнал в проводнике |
| `download_preview` | `url: string` | `PreviewInfo` | заголовок, миниатюра, доступные высоты |
| `download_start` | `params: DownloadParams` | `number` | запуск скачивания, возвращает `job_id` |
| `save_result` | `sourcePath`, `destinationPath` | `number` | копирование результата с защитой от перезаписи |
| `convert_start` | `params: ConvertParams` | `number` | конвертация очереди файлов |
| `save_convert_result` | `sourceDir`, `destinationDir` | `number` | копирование готовых файлов в папку |

Команды фазы 1 и 2 (повторяют Python-код и появятся со своими заданиями):
`pick_files`, `pick_folder`, `list_input_files`. Все они возвращают либо
данные, либо `job_id`, подчиняясь тем же соглашениям.

Событие `job://event` — единственный канал заданий:

```ts
type JobEventEnvelope =
  | { job_id: number; kind: string; state: "progress"; percent: number; file?: string }
  | { job_id: number; kind: string; state: "status"; message: string }
  | { job_id: number; kind: string; state: "ok"; message: string; path?: string }
  | { job_id: number; kind: string; state: "canceled" }
  | { job_id: number; kind: string; state: "failed"; message: string };
```

Поле `kind` — это `id` инструмента (`download`, `convert`), чтобы одна
вкладка слушала только свои задания, не заботясь о числовых идентификаторах.
Поле `file` заполняется только для операций с пакетом файлов и служит для
строки списка (аналог `progress_file`).

Почему отдельный канал, а не событие на каждое задание: подписка и
отписка не нужны при каждом запуске, а порядок событий гарантирован.
При переполнении `broadcast` пишется предупреждение в журнал, а интерфейс
перезапрашивает `list_jobs`. Единственный источник имён — `src/lib/ipc.ts`
для фронтенда и `commands.rs` для Rust; изменение сигнатуры в одном файле
требует изменения в другом.

---

## 8. Стратегия sidecar

Какие бинарники поставляются в `src-tauri/resources`: `yt-dlp.exe`,
`ffmpeg.exe` (и `ffprobe.exe`, если понадобится для точной длительности),
позже — обёртка Demucs и её python-runtime. Порядок поиска реализован в
`tools::sidecar::resolve_with`:

1. каталог ресурсов (dev — `src-tauri/resources`, релиз —
   `app.path().resource_dir()`);
2. каталог исполняемого файла (рядом с `music-tools.exe`);
3. каталоги из `PATH`;
4. только в отладочной сборке: `python -m yt_dlp` и `ffmpeg.exe` в корне
   репозитория.

`ResolvedSidecar::describe()` записывает в журнал, какой источник сработал,
поэтому проблему «не нашлась программа» видно из `fauna_tools.log` без
догадок.

Обёртка `tools/sidecar` гарантирует три вещи. Первое: дочерний процесс
запускается через `tokio::process::Command` без окна консоли
(`CREATE_NO_WINDOW`) и убивается на отмене вместе со всем деревом процессов
(`taskkill /T /F /PID` на Windows). Второе: stderr читается полностью,
последние строки хранятся в кольцевом буфере и попадают в текст ошибки
пользователю. Третье: stdout читается построчно; прогресс yt-dlp берётся из
машиночитаемого `--progress-template`, прогресс ffmpeg — из
`-progress pipe:1` (`out_time_us` против `Duration:` из stderr), значения
ограничены 0..99, сто процентов отправляются только после успешного выхода.

Обновление `yt-dlp` — отдельный вопрос (план). Он ломается чаще всего, когда
сайты меняют разметку, поэтому самообновление через сам бинарник
(`yt-dlp -U`) не подходит: файл лежит в ресурсах установки, а запись
в неё требует прав администратора. Планируемый порядок поиска:

1. `%LOCALAPPDATA%\MusicTools\bin\yt-dlp.exe` — обновляемая копия;
2. `resources\yt-dlp.exe` — закреплённый бинарник из установщика;
3. `PATH` — для разработчика.

Обновление выполняет команда `update_ytdlp`: скачивает официальный
релиз, сверяет sha256 из манифеста GitHub, атомарно заменяет файл
через временный путь и `rename`. Ничего не пишется в `Program Files`.
`ffmpeg` закреплён и обновляется только вместе с приложением.

---

## 9. Обновления: совместимость с `latest.json`

Действующий контракт (`core/updater.py`, `scripts/make_manifest.py`):

```json
{
  "version": "1.2.0",
  "channel": "stable",
  "notes": "...",
  "platforms": {
    "windows-x86_64": { "url": "https://...", "sha256": "...", "size": 12345678 }
  },
  "signature": { "algorithm": "ed25519", "key_id": "...", "value": "base64" }
}
```

Подпись — Ed25519 над каноническими байтами манифеста без ключа
`signature`: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`. Проверка fail-closed для `stable`.

`tauri-plugin-updater` ожидает свой формат: `version`, `pub_date`,
`platforms.<target>` с полями `signature`, `url`, `size`, где `signature`
— строка Ed25519 в формате minisign над самим бинарником, а не над
манифестом. Переписать `latest.json` под плагин нельзя: это сломает
обновление как у PyQt-версии, так и у уже выпущенных сборок. Поэтому
выбран вариант «существующий манифест плюс собственный установщик»:

* проверка манифеста и подписи переносится в Rust один к одному:
  `ed25519-dalek` (verify над каноническими байтами), `sha2` для
  контрольной суммы, сравнение версий через `semver`;
* формат остаётся прежним, поле `signature` остаётся объектом;
* скачивание — задание `update_job` с прогрессом и отменой; после
  проверки `sha256` запускается NSIS-установщик в тихом режиме или
  применяется схема замещения exe с `.old`-копией и откатом, как в
  `_build_batch_script` PyQt-версии;
* откат: если новый exe не запустился (маркер `update-ok` не создан на
  первом старте), при следующем запуске восстанавливается `.old`.

`tauri-plugin-updater` не подключается намеренно: он добавил бы вторую
сигнатуру и вторую семантику, из-за чего клиент проверял бы не то, что
подписывает существующий выпуск. Если позже понадобится его канал
обновлений, ключи и формат выпуска меняются сразу для обеих веток —
это отдельное решение, а не побочный эффект миграции.

Каналы `stable` и `beta` остаются частью манифеста (`channel`), а
выбор канала — в `settings.json`.

---

## 10. Порядок миграции

Фаза 0 — скелет (сделано): каркас Tauri, реестр инструментов, модель
заданий, настройки, журнал, трей, три вкладки-заглушки, сборка и
проверки.

Фаза 1 — скачивание: `tools/sidecar` (запуск, stderr, отмена, kill-tree),
`tools/download` (`probe_url`, форматы mp4/mp3/wav, битрейты, глубины,
безопасное имя файла, сохранение через `name (1).ext`), вкладка
«Скачивание». Обновление `yt-dlp` через `%LOCALAPPDATA%`.

Фаза 2 — конвертация: `tools/convert` (обход папок, матрица качества,
прогресс по файлам и внутри файла, отмена с очисткой временных файлов,
копирование), вкладка «Конвертация». Сюда же переносится `file_drop_list`
на уровне интерфейса.

Фаза 3 — обновление и трей: `update` (проверка манифеста, скачивание,
установка, откат), вкладка «О программе» с рабочими ссылками,
проверка при старте по `check_on_startup`.

Фаза 4 — будущие инструменты: `metadata` (lofty), `bpm` (sidecar или
crate), `stems` (Demucs с загрузкой моделей по запросу). Каждый —
одна запись в реестре, один компонент, общий `JobContext`.

Паритет считается достигнутым, когда: скачивание поддерживает все
форматы и качества PyQt-версии, включая отмену и `name (1).ext`;
конвертация проходит по тем же входным и выходным форматам с той же
матрицей и тем же поведением при ошибке; трей и автозапуск работают
идентично; автообновление проверяет ту же подпись и умеет откатиться;
«О программе» и журнал доступны; настройки читаются из того же файла;
сборка `cargo tauri build` даёт установщик, проходящий ручной чек-лист
из `docs/QA.md` в части, не зависящей от Qt.

---

## 11. Что сознательно не делается в скелете

FFmpeg и yt-dlp не поставляются, скачивание и конвертация не
реализованы — это фаза 1. Плагины Tauri (`dialog`, `opener`, `updater`)
не подключены: они добавляются вместе с первыми командами, которым
нужны, чтобы capabilities оставались минимальными. Очередь заданий
отложена до фазы 2: сейчас каждое задание само распоряжается своими
файлами. Горячие клавиши и расширенное меню трея появятся при разборе
раздела «трей» в фазе 3.
