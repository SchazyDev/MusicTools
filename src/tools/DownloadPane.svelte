<script lang="ts">
  /**
   * Вкладка «Скачивание»: ссылка, предпросмотр, формат, качество, прогресс.
   *
   * Логика повторяет `ui/download_tab.py`: качество зависит от формата
   * (разрешение / битрейт / разрядность), предпросмотр загружается после
   * паузы ввода, а готовый файл сохраняется через диалог.
   */
  import { onMount } from "svelte";
  import { save } from "@tauri-apps/plugin-dialog";
  import {
    commands,
    describeError,
    onJobEvent,
    type DownloadFormat,
    type JobEvent,
    type PreviewInfo,
    type ToolDescriptor,
  } from "../lib/ipc";

  let { descriptor }: { descriptor: ToolDescriptor } = $props();

  const FALLBACK_HEIGHTS = [1080, 720, 480, 360];
  const MP3_BITRATES = [320, 256, 224, 192, 160, 128, 112, 96, 80, 64, 56, 48, 40, 32];
  const WAV_DEPTHS = [16, 24, 32];

  let url = $state("");
  let format = $state<DownloadFormat>("mp4");
  let quality = $state("1080p");
  let preview = $state<PreviewInfo | null>(null);
  let previewLoading = $state(false);
  let previewError = $state("");

  let downloadJob = $state<number | null>(null);
  let saveJob = $state<number | null>(null);
  let percent = $state(0);
  let status = $state("");
  let errorText = $state("");
  let successText = $state("");

  let unlisten: (() => void) | null = null;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let previewToken = 0;

  const qualityOptions = $derived(
    format === "mp4"
      ? videoHeights().map((height) => `${height}p`)
      : format === "mp3"
        ? MP3_BITRATES.map((rate) => `${rate}kbps`)
        : WAV_DEPTHS.map((depth) => `${depth}bit`),
  );

  const qualityLabel = $derived(
    format === "mp4" ? "Разрешение" : format === "mp3" ? "Битрейт" : "Разрядность",
  );

  const busy = $derived(downloadJob !== null);
  const canStart = $derived(url.trim().length > 0 && !busy);

  function videoHeights(): number[] {
    return preview && preview.heights.length > 0 ? preview.heights : FALLBACK_HEIGHTS;
  }

  function optionsFor(next: DownloadFormat): string[] {
    if (next === "mp4") return videoHeights().map((height) => `${height}p`);
    if (next === "mp3") return MP3_BITRATES.map((rate) => `${rate}kbps`);
    return WAV_DEPTHS.map((depth) => `${depth}bit`);
  }

  function selectFormat(next: DownloadFormat): void {
    format = next;
    const options = optionsFor(next);
    if (!options.includes(quality)) quality = options[0] ?? "";
  }

  function handleUrlInput(): void {
    preview = null;
    previewError = "";
    previewToken += 1;
    if (debounceTimer !== null) clearTimeout(debounceTimer);

    const value = url.trim();
    if (!value) {
      previewLoading = false;
      return;
    }

    const token = previewToken;
    debounceTimer = setTimeout(() => void loadPreview(value, token), 500);
  }

  async function loadPreview(value: string, token: number): Promise<void> {
    previewLoading = true;
    try {
      const info = await commands.downloadPreview(value);
      if (token !== previewToken) return;
      preview = info;
      if (format === "mp4") {
        const options = optionsFor("mp4");
        if (!options.includes(quality)) quality = options[0] ?? quality;
      }
    } catch (error) {
      if (token !== previewToken) return;
      previewError = describeError(error);
    } finally {
      if (token === previewToken) previewLoading = false;
    }
  }

  async function start(): Promise<void> {
    errorText = "";
    successText = "";
    percent = 0;
    status = "Подготовка...";
    try {
      downloadJob = await commands.downloadStart({
        url: url.trim(),
        format,
        quality,
      });
    } catch (error) {
      status = "";
      errorText = describeError(error);
    }
  }

  async function cancel(): Promise<void> {
    if (downloadJob === null) return;
    try {
      await commands.cancelJob(downloadJob);
    } catch (error) {
      errorText = describeError(error);
    }
  }

  async function offerSave(path: string): Promise<void> {
    const name = path.split(/[\\/]/).pop() ?? "audio";
    try {
      const destination = await save({ title: "Сохранить файл", defaultPath: name });
      if (!destination) {
        successText = `Файл готов: ${path}`;
        return;
      }
      status = "Сохранение...";
      saveJob = await commands.saveResult(path, destination);
    } catch (error) {
      errorText = describeError(error);
    }
  }

  function handleEvent(event: JobEvent): void {
    if (downloadJob !== null && event.job_id === downloadJob && event.kind === "download") {
      switch (event.state) {
        case "progress":
          percent = event.percent;
          break;
        case "status":
          status = event.message;
          break;
        case "ok":
          downloadJob = null;
          percent = 100;
          status = event.message;
          if (event.path) void offerSave(event.path);
          break;
        case "canceled":
          downloadJob = null;
          percent = 0;
          status = "Скачивание отменено.";
          break;
        case "failed":
          downloadJob = null;
          percent = 0;
          status = "";
          errorText = event.message;
          break;
      }
      return;
    }

    if (saveJob !== null && event.job_id === saveJob && event.kind === "save") {
      switch (event.state) {
        case "progress":
          percent = event.percent;
          break;
        case "status":
          status = event.message;
          break;
        case "ok":
          saveJob = null;
          percent = 100;
          status = "";
          successText = event.message;
          break;
        case "canceled":
          saveJob = null;
          status = "Сохранение отменено.";
          break;
        case "failed":
          saveJob = null;
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
      else unlisten = unsubscribe;
    });
    return () => {
      disposed = true;
      unlisten?.();
      if (debounceTimer !== null) clearTimeout(debounceTimer);
    };
  });
</script>

<section>
  <h2>{descriptor.title}</h2>
  <p class="muted">{descriptor.description}</p>

  <div class="card">
    <label class="field">
      <span>Ссылка</span>
      <input
        type="url"
        placeholder="https://..."
        bind:value={url}
        oninput={handleUrlInput}
        disabled={busy}
      />
    </label>

    {#if previewLoading}
      <p class="muted">Загрузка сведений о ролике…</p>
    {/if}

    {#if previewError}
      <p class="error">Не удалось получить сведения: {previewError}</p>
    {/if}

    {#if preview}
      <div class="preview">
        {#if preview.thumbnail}
          <img src={preview.thumbnail} alt="Миниатюра ролика" />
        {/if}
        <p class="title">{preview.title}</p>
      </div>
    {/if}

    <div class="row">
      <label class="field">
        <span>Формат</span>
        <select
          value={format}
          onchange={(event) =>
            selectFormat((event.currentTarget as HTMLSelectElement).value as DownloadFormat)}
          disabled={busy}
        >
          <option value="mp4">mp4 — видео</option>
          <option value="mp3">mp3 — аудио</option>
          <option value="wav">wav — без потерь</option>
        </select>
      </label>

      <label class="field">
        <span>{qualityLabel}</span>
        <select bind:value={quality} disabled={busy}>
          {#each qualityOptions as option (option)}
            <option value={option}>{option}</option>
          {/each}
        </select>
      </label>
    </div>

    <div class="actions">
      {#if busy}
        <button class="danger" onclick={() => void cancel()}>Отмена</button>
      {:else}
        <button class="primary" disabled={!canStart} onclick={() => void start()}>
          Скачать
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

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
  }

  .row {
    display: flex;
    gap: 12px;
  }

  .preview {
    display: flex;
    gap: 12px;
    align-items: center;
  }

  .preview img {
    width: 160px;
    height: 90px;
    object-fit: cover;
    border-radius: 6px;
    border: 1px solid var(--border);
  }

  .title {
    margin: 0;
    font-weight: 600;
  }

  .actions {
    display: flex;
    gap: 8px;
  }

  p {
    margin: 0;
  }

  .error {
    color: #ffb3b0;
  }

  .ok {
    color: var(--ok);
    user-select: text;
    word-break: break-all;
  }
</style>
