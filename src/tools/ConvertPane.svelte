<script lang="ts">
  /**
   * Вкладка «Конвертация»: очередь файлов, формат и качество.
   *
   * Повторяет `ui/convert_tab.py`: файлы добавляются кнопкой или перетаскиванием,
   * качество зависит от формата, прогресс общий по очереди, а готовые файлы
   * сохраняются в выбранную папку.
   */
  import { onMount } from "svelte";
  import { open } from "@tauri-apps/plugin-dialog";
  import { getCurrentWebview } from "@tauri-apps/api/webview";
  import {
    commands,
    describeError,
    onJobEvent,
    type ConvertFormat,
    type JobEvent,
    type ToolDescriptor,
  } from "../lib/ipc";

  let { descriptor }: { descriptor: ToolDescriptor } = $props();

  const ALLOWED = [
    "mp4", "avi", "mkv", "mov", "flv", "wmv", "webm",
    "mp3", "wav", "flac", "aac", "ogg", "m4a",
  ];

  const MP3_BITRATES = [320, 256, 224, 192, 160, 128, 112, 96, 80, 64, 56, 48, 40, 32];
  const OGG_BITRATES = [450, 350, 320, 256, 224, 192, 160, 128, 112, 96, 80, 64, 56, 48];
  const WAV_DEPTHS = [32, 24, 16];
  const FLAC_DEPTHS = [24, 16];
  const FLAC_LEVELS = 13;

  interface Option {
    value: string;
    label: string;
  }

  let files = $state<string[]>([]);
  let format = $state<ConvertFormat>("mp3");
  let quality = $state("320");

  let jobId = $state<number | null>(null);
  let saveJobId = $state<number | null>(null);
  let percent = $state(0);
  let status = $state("");
  let errorText = $state("");
  let successText = $state("");

  let unlistenJobs: (() => void) | null = null;
  let unlistenDrop: (() => void) | null = null;

  const qualityOptions = $derived(optionsFor(format));
  const qualityLabel = $derived(
    format === "mp3" || format === "ogg"
      ? "Битрейт"
      : format === "wav"
        ? "Разрядность"
        : "Разрядность и сжатие",
  );
  const busy = $derived(jobId !== null || saveJobId !== null);

  function optionsFor(next: ConvertFormat): Option[] {
    if (next === "mp3") {
      return MP3_BITRATES.map((rate) => ({ value: String(rate), label: `${rate} кбит/с` }));
    }
    if (next === "ogg") {
      return OGG_BITRATES.map((rate) => ({ value: String(rate), label: `${rate} кбит/с` }));
    }
    if (next === "wav") {
      return WAV_DEPTHS.map((depth) => ({ value: String(depth), label: `${depth} бит` }));
    }
    const options: Option[] = [];
    for (const depth of FLAC_DEPTHS) {
      for (let level = 0; level < FLAC_LEVELS; level += 1) {
        options.push({ value: `${depth}:${level}`, label: `${depth} бит, сжатие ${level}` });
      }
    }
    return options;
  }

  function selectFormat(next: ConvertFormat): void {
    format = next;
    const options = optionsFor(next);
    if (!options.some((option) => option.value === quality)) {
      quality = options[0]?.value ?? "";
    }
  }

  function fileName(path: string): string {
    const parts = path.split(/[\\/]/);
    return parts[parts.length - 1] || path;
  }

  function hasAllowedExtension(path: string): boolean {
    const dot = path.lastIndexOf(".");
    if (dot < 0) return false;
    return ALLOWED.includes(path.slice(dot + 1).toLowerCase());
  }

  function addPaths(paths: string[]): void {
    errorText = "";
    successText = "";
    const rejected: string[] = [];
    const next = [...files];
    for (const path of paths) {
      if (!hasAllowedExtension(path)) {
        rejected.push(fileName(path));
        continue;
      }
      if (!next.includes(path)) next.push(path);
    }
    files = next;
    if (rejected.length > 0) {
      errorText = `Файлы не добавлены (неподдерживаемый формат): ${rejected.join(", ")}`;
    }
  }

  async function addFiles(): Promise<void> {
    try {
      const selected = await open({
        title: "Выберите файлы",
        multiple: true,
        filters: [{ name: "Медиафайлы", extensions: ALLOWED }],
      });
      if (!selected) return;
      addPaths(Array.isArray(selected) ? selected : [selected]);
    } catch (error) {
      errorText = describeError(error);
    }
  }

  function removeFile(path: string): void {
    files = files.filter((item) => item !== path);
  }

  function clearFiles(): void {
    files = [];
    errorText = "";
    successText = "";
    percent = 0;
    status = "";
  }

  async function start(): Promise<void> {
    if (files.length === 0) {
      errorText = "Выберите файлы для конвертации.";
      return;
    }
    errorText = "";
    successText = "";
    percent = 0;
    status = "Подготовка...";
    try {
      jobId = await commands.convertStart({ files, format, quality });
    } catch (error) {
      status = "";
      errorText = describeError(error);
    }
  }

  async function cancel(): Promise<void> {
    const active = jobId ?? saveJobId;
    if (active === null) return;
    try {
      await commands.cancelJob(active);
    } catch (error) {
      errorText = describeError(error);
    }
  }

  async function offerSave(directory: string): Promise<void> {
    try {
      const destination = await open({
        title: "Выберите папку для файлов",
        directory: true,
        multiple: false,
      });
      if (!destination || Array.isArray(destination)) {
        successText = `Файлы готовы: ${directory}`;
        return;
      }
      status = "Сохранение...";
      saveJobId = await commands.saveConvertResult(directory, destination);
    } catch (error) {
      errorText = describeError(error);
    }
  }

  function handleEvent(event: JobEvent): void {
    if (jobId !== null && event.job_id === jobId && event.kind === "convert") {
      switch (event.state) {
        case "progress":
          percent = event.percent;
          break;
        case "status":
          status = event.message;
          break;
        case "ok":
          jobId = null;
          percent = 100;
          status = event.message;
          if (event.path) void offerSave(event.path);
          break;
        case "canceled":
          jobId = null;
          percent = 0;
          status = "Конвертация отменена.";
          break;
        case "failed":
          jobId = null;
          percent = 0;
          status = "";
          errorText = event.message;
          break;
      }
      return;
    }

    if (saveJobId !== null && event.job_id === saveJobId && event.kind === "convert_save") {
      switch (event.state) {
        case "progress":
          percent = event.percent;
          break;
        case "status":
          status = event.message;
          break;
        case "ok":
          saveJobId = null;
          percent = 100;
          status = "";
          successText = event.message;
          break;
        case "canceled":
          saveJobId = null;
          status = "Сохранение отменено.";
          break;
        case "failed":
          saveJobId = null;
          status = "";
          errorText = event.message;
          break;
      }
    }
  }

  onMount(() => {
    let disposed = false;

    void onJobEvent(handleEvent).then((unsubscribe) => {
      if (disposed) unsubscribe();
      else unlistenJobs = unsubscribe;
    });

    void getCurrentWebview()
      .onDragDropEvent((event) => {
        if (event.payload.type === "drop") addPaths(event.payload.paths);
      })
      .then((unsubscribe) => {
        if (disposed) unsubscribe();
        else unlistenDrop = unsubscribe;
      });

    return () => {
      disposed = true;
      unlistenJobs?.();
      unlistenDrop?.();
    };
  });
</script>

<section>
  <h2>{descriptor.title}</h2>
  <p class="muted">{descriptor.description}</p>

  <div class="card">
    <div class="actions">
      <button onclick={() => void addFiles()} disabled={busy}>Добавить файлы</button>
      <button onclick={clearFiles} disabled={busy || files.length === 0}>Очистить</button>
      <span class="muted">Файлов: {files.length}</span>
    </div>

    {#if files.length === 0}
      <p class="muted">
        Перетащите файлы в окно или нажмите «Добавить файлы». Поддерживаются
        mp4, avi, mkv, mov, flv, wmv, webm, mp3, wav, flac, aac, ogg, m4a.
      </p>
    {:else}
      <ul class="files">
        {#each files as path (path)}
          <li>
            <span class="name" title={path}>{fileName(path)}</span>
            <button
              class="remove"
              onclick={() => removeFile(path)}
              disabled={busy}
              title="Убрать из списка"
            >
              Убрать
            </button>
          </li>
        {/each}
      </ul>
    {/if}

    <div class="row">
      <label class="field">
        <span>Формат</span>
        <select
          value={format}
          onchange={(event) =>
            selectFormat((event.currentTarget as HTMLSelectElement).value as ConvertFormat)}
          disabled={busy}
        >
          <option value="mp3">mp3</option>
          <option value="wav">wav</option>
          <option value="flac">flac</option>
          <option value="ogg">ogg</option>
        </select>
      </label>

      <label class="field">
        <span>{qualityLabel}</span>
        <select bind:value={quality} disabled={busy}>
          {#each qualityOptions as option (option.value)}
            <option value={option.value}>{option.label}</option>
          {/each}
        </select>
      </label>
    </div>

    <div class="actions">
      {#if busy}
        <button class="danger" onclick={() => void cancel()}>Отмена</button>
      {:else}
        <button class="primary" disabled={files.length === 0} onclick={() => void start()}>
          Конвертировать
        </button>
      {/if}
    </div>

    {#if busy || percent > 0}
      <div class="progress" aria-label="Ход выполнения">
        <span style:width={`${percent}%`}></span>
      </div>
    {/if}

    {#if status}
      <p class="muted">{status}</p>
    {/if}

    {#if errorText}
      <p class="error">{errorText}</p>
    {/if}

    {#if successText}
      <p class="ok">{successText}</p>
    {/if}
  </div>
</section>

<style>
  section {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .card {
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
  }

  .row {
    display: flex;
    gap: 12px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .files {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 220px;
    overflow: auto;
    border: 1px solid var(--border);
    border-radius: 6px;
  }

  .files li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 10px;
    border-bottom: 1px solid var(--border);
  }

  .files li:last-child {
    border-bottom: none;
  }

  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    user-select: text;
  }

  .remove {
    padding: 2px 8px;
    font-size: 12px;
  }

  p {
    margin: 0;
  }

  .error {
    color: #ffb3b0;
    white-space: pre-line;
    user-select: text;
  }

  .ok {
    color: var(--ok);
    user-select: text;
    word-break: break-all;
  }
</style>
