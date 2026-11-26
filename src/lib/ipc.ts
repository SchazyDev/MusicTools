/**
 * Типизированная обёртка над IPC Tauri.
 *
 * Единственное место, где фронтенд знает имена команд и событий.
 * Контракт описан в docs/TAURI_ARCHITECTURE.md (раздел «IPC-контракт»).
 */
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// ---------------------------------------------------------------------------
// Типы, зеркалящие Rust-структуры
// ---------------------------------------------------------------------------

export type ToolKind = "download" | "convert" | "about";

/** `ToolDescriptor` из `src-tauri/src/tools_registry.rs` (snake_case). */
export interface ToolDescriptor {
  id: string;
  title: string;
  order: number;
  enabled: boolean;
  icon: string | null;
  kind: ToolKind;
  description: string;
}

/** `AppInfo` из `src-tauri/src/commands.rs`. */
export interface AppInfo {
  name: string;
  version: string;
  identifier: string;
  log_path: string;
  settings_path: string;
  os: string;
  debug: boolean;
}

export type UpdateChannel = "stable" | "beta";

/** `Settings` из `src-tauri/src/settings.rs`. */
export interface Settings {
  channel: UpdateChannel;
  check_on_startup: boolean;
  close_to_tray: boolean;
}

export type JobStatus = "running" | "finished" | "canceled" | "failed";

/** `JobSnapshot` из `src-tauri/src/jobs.rs`. */
export interface JobSnapshot {
  id: number;
  kind: string;
  status: JobStatus;
}

/** Полезная нагрузка события `job://event` (`JobEventEnvelope`). */
export type JobEvent =
  | { job_id: number; kind: string; state: "progress"; percent: number; file?: string }
  | { job_id: number; kind: string; state: "status"; message: string }
  | { job_id: number; kind: string; state: "ok"; message: string; path?: string }
  | { job_id: number; kind: string; state: "canceled" }
  | { job_id: number; kind: string; state: "failed"; message: string };

export type JobTerminalEvent = Extract<JobEvent, { state: "ok" | "canceled" | "failed" }>;

// ---------------------------------------------------------------------------
// Скачивание (`src-tauri/src/tools/download/mod.rs`)
// ---------------------------------------------------------------------------

export type DownloadFormat = "mp4" | "mp3" | "wav";

/** `DownloadParams`: quality — `1080p`, `320kbps` или `24bit`. */
export interface DownloadParams {
  url: string;
  format: DownloadFormat;
  quality: string;
}

/** `PreviewInfo`: заголовок, миниатюра и доступные высоты кадра. */
export interface PreviewInfo {
  title: string;
  thumbnail: string;
  heights: number[];
}

/** `DownloadResult`: путь готового файла и его размер. */
export interface DownloadResult {
  path: string;
  size_bytes: number;
}

// ---------------------------------------------------------------------------
// Конвертация (`src-tauri/src/tools/convert/mod.rs`)
// ---------------------------------------------------------------------------

export type ConvertFormat = "mp3" | "wav" | "flac" | "ogg";

/** `ConvertParams`: quality — `"320"`, `"24"` или `"24:8"` (flac). */
export interface ConvertParams {
  files: string[];
  format: ConvertFormat;
  quality: string;
}

// ---------------------------------------------------------------------------
// Обновления (`src-tauri/src/update/mod.rs`)
// ---------------------------------------------------------------------------

/** `UpdateInfo`: описание доступного обновления из `latest.json`. */
export interface UpdateInfo {
  version: string;
  url: string;
  sha256: string;
  size: number;
  notes: string;
  channel: string;
}

// ---------------------------------------------------------------------------
// Команды (имена совпадают с generate_handler! в lib.rs)
// ---------------------------------------------------------------------------

export const commands = {
  /** Версия из tauri.conf.json, пути, ОС. */
  appInfo: (): Promise<AppInfo> => invoke<AppInfo>("app_info"),
  /** Включённые инструменты, отсортированные по (order, title). */
  getTools: (): Promise<ToolDescriptor[]> => invoke<ToolDescriptor[]>("get_tools"),
  getSettings: (): Promise<Settings> => invoke<Settings>("get_settings"),
  updateSettings: (patch: Partial<Settings>): Promise<Settings> =>
    invoke<Settings>("update_settings", { patch }),
  /** Возвращает job_id немедленно; события приходят в `job://event`. */
  cancelJob: (jobId: number): Promise<boolean> => invoke<boolean>("cancel_job", { jobId }),
  listJobs: (): Promise<JobSnapshot[]> => invoke<JobSnapshot[]>("list_jobs"),
  hideToTray: (): Promise<void> => invoke<void>("hide_to_tray"),
  showMainWindow: (): Promise<void> => invoke<void>("show_main_window"),
  openLogFile: (): Promise<void> => invoke<void>("open_log_file"),
  /** Предпросмотр ссылки; ошибка приходит строкой на русском. */
  downloadPreview: (url: string): Promise<PreviewInfo> =>
    invoke<PreviewInfo>("download_preview", { url }),
  /** Запускает скачивание, возвращает job_id. */
  downloadStart: (params: DownloadParams): Promise<number> =>
    invoke<number>("download_start", { params }),
  /** Копирует готовый файл в выбранное место, возвращает job_id. */
  saveResult: (sourcePath: string, destinationPath: string): Promise<number> =>
    invoke<number>("save_result", { sourcePath, destinationPath }),
  /** Запускает конвертацию очереди файлов, возвращает job_id. */
  convertStart: (params: ConvertParams): Promise<number> =>
    invoke<number>("convert_start", { params }),
  /** Копирует готовые файлы в папку, возвращает job_id. */
  saveConvertResult: (sourceDir: string, destinationDir: string): Promise<number> =>
    invoke<number>("save_convert_result", { sourceDir, destinationDir }),
  /** Проверяет обновления; `null` — новой версии нет или сеть недоступна. */
  checkUpdates: (): Promise<UpdateInfo | null> => invoke<UpdateInfo | null>("check_updates"),
  /** Скачивает обновление и запускает перезапуск, возвращает job_id. */
  startUpdate: (info: UpdateInfo): Promise<number> => invoke<number>("start_update", { info }),
} as const;

// ---------------------------------------------------------------------------
// События
// ---------------------------------------------------------------------------

/** Подписка на все события заданий. Возвращает функцию отписки. */
export function onJobEvent(handler: (event: JobEvent) => void): Promise<UnlistenFn> {
  return listen<JobEvent>("job://event", (e) => handler(e.payload));
}

/** Сужение типа до терминальных состояний задания. */
export function isTerminal(event: JobEvent): event is JobTerminalEvent {
  return event.state === "ok" || event.state === "canceled" || event.state === "failed";
}

/** Текст ошибки IPC: Rust возвращает строку на русском. */
export function describeError(error: unknown): string {
  if (typeof error === "string") return error;
  if (error instanceof Error) return error.message;
  return "Неизвестная ошибка";
}
