<script lang="ts">
  /** Вкладка «О программе»: версия, пути, журнал, программы-сайдкары. */
  import {
    commands,
    describeError,
    isTerminal,
    onJobEvent,
    type AppInfo,
    type SidecarStatus,
    type ToolDescriptor,
    type YtDlpStatus,
  } from "../lib/ipc";

  let { descriptor, info }: { descriptor: ToolDescriptor; info: AppInfo } = $props();
  let errorText = $state<string | null>(null);
  let sidecars = $state<SidecarStatus[]>([]);

  const REPO_URL = "https://github.com/SchazyDev/MusicTools";
  const ISSUES_URL = "https://github.com/SchazyDev/MusicTools/issues";
  const RELEASES_URL = "https://github.com/SchazyDev/MusicTools/releases";

  async function openLink(url: string): Promise<void> {
    errorText = null;
    try {
      await commands.openUrl(url);
    } catch (error) {
      errorText = describeError(error);
    }
  }

  let ytdlp = $state<YtDlpStatus | null>(null);
  let ytdlpBusy = $state(false);
  let ytdlpStatusText = $state<string | null>(null);
  let ytdlpPercent = $state(0);
  let ytdlpJobId: number | null = null;

  async function openLog(): Promise<void> {
    errorText = null;
    try {
      await commands.openLogFile();
    } catch (error) {
      errorText = describeError(error);
    }
  }

  async function refreshYtdlp(): Promise<void> {
    ytdlpStatusText = "проверка версии...";
    try {
      ytdlp = await commands.ytdlpStatus();
      ytdlpStatusText = null;
    } catch (error) {
      ytdlpStatusText = describeError(error);
    }
  }

  async function updateYtdlp(): Promise<void> {
    errorText = null;
    ytdlpBusy = true;
    ytdlpPercent = 0;
    try {
      ytdlpJobId = await commands.ytdlpUpdate();
    } catch (error) {
      ytdlpBusy = false;
      errorText = describeError(error);
    }
  }

  async function refreshSidecars(): Promise<void> {
    try {
      sidecars = await commands.sidecarsStatus();
    } catch (error) {
      errorText = describeError(error);
    }
  }

  $effect(() => {
    void refreshSidecars();
    void refreshYtdlp();

    let unlisten: (() => void) | null = null;
    let disposed = false;

    void onJobEvent((event) => {
      if (event.kind !== "ytdlp_update" || event.job_id !== ytdlpJobId) return;
      if (event.state === "progress") {
        ytdlpPercent = event.percent;
      } else if (event.state === "status") {
        ytdlpStatusText = event.message;
      } else if (isTerminal(event)) {
        ytdlpBusy = false;
        ytdlpJobId = null;
        if (event.state === "ok") {
          ytdlpStatusText = event.message;
          void refreshYtdlp();
        } else if (event.state === "failed") {
          ytdlpStatusText = event.message;
        } else {
          ytdlpStatusText = "обновление отменено";
        }
      }
    }).then((fn) => {
      if (disposed) fn();
      else unlisten = fn;
    });

    return () => {
      disposed = true;
      unlisten?.();
    };
  });
</script>

<section>
  <h2>{descriptor.title}</h2>
  <p class="muted">{descriptor.description}</p>

  <dl class="card grid">
    <dt>Версия</dt>
    <dd>{info.version}</dd>
    <dt>Идентификатор</dt>
    <dd>{info.identifier}</dd>
    <dt>Система</dt>
    <dd>{info.os}</dd>
    <dt>Сборка</dt>
    <dd>{info.debug ? "отладочная" : "релизная"}</dd>
    <dt>Журнал</dt>
    <dd class="path">{info.log_path}</dd>
    <dt>Настройки</dt>
    <dd class="path">{info.settings_path}</dd>
  </dl>

  <div class="actions">
    <button onclick={() => void openLog()}>Открыть журнал</button>
    <button onclick={() => void openLink(REPO_URL)}>Репозиторий</button>
    <button onclick={() => void openLink(RELEASES_URL)}>Выпуски</button>
    <button onclick={() => void openLink(ISSUES_URL)}>Сообщить о проблеме</button>
  </div>

  <div class="card">
    <h3>Программы</h3>
    <dl class="grid">
      {#each sidecars as item (item.name)}
        <dt>{item.name}</dt>
        <dd class:path={item.found}>
          {item.found ? item.path : item.source}
        </dd>
      {/each}
    </dl>
  </div>

  <div class="card">
    <h3>Программа загрузки (yt-dlp)</h3>
    <p class="muted small">
      Сайты меняют разметку, поэтому yt-dlp нужно обновлять. Обновление
      ставится в профиль пользователя и не требует прав администратора.
    </p>
    <dl class="grid">
      <dt>Установлена</dt>
      <dd>{ytdlp?.current ?? "не найдена"}</dd>
      <dt>Доступна</dt>
      <dd>{ytdlp?.latest ?? "не проверена"}</dd>
      <dt>Файл</dt>
      <dd class="path">{ytdlp?.path ?? "—"}</dd>
    </dl>

    <div class="actions">
      <button
        onclick={() => void updateYtdlp()}
        disabled={ytdlpBusy || !ytdlp?.update_available}
      >
        {ytdlpBusy ? "Обновление..." : "Обновить yt-dlp"}
      </button>
      <button onclick={() => void refreshYtdlp()} disabled={ytdlpBusy}>Проверить версию</button>
    </div>

    {#if ytdlpBusy}
      <progress max="100" value={ytdlpPercent}></progress>
    {/if}
    {#if ytdlpStatusText}
      <p class="muted small">{ytdlpStatusText}</p>
    {/if}
  </div>

  {#if errorText}
    <p class="error">{errorText}</p>
  {/if}
</section>

<style>
  section {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  h3 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
  }

  .grid {
    display: grid;
    grid-template-columns: 130px 1fr;
    gap: 6px 14px;
    margin: 0;
  }

  dt {
    color: var(--muted);
  }

  dd {
    margin: 0;
  }

  .path {
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 12px;
    word-break: break-all;
    user-select: text;
  }

  .small {
    font-size: 12px;
    margin: 0;
  }

  .actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  progress {
    width: 100%;
    height: 6px;
  }

  .error {
    color: #ffb3b0;
  }
</style>
