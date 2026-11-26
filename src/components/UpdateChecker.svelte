<script lang="ts">
  /**
   * Проверка обновлений: ручная кнопка в строке состояния.
   *
   * Автоматическая проверка при запуске выполняется в `App.svelte`; здесь —
   * повторная проверка, загрузка и установка. После установки Rust сам
   * завершает приложение, скрипт подменяет exe и запускает новую версию.
   */
  import { onMount } from "svelte";
  import {
    commands,
    describeError,
    onJobEvent,
    type JobEvent,
    type UpdateInfo,
  } from "../lib/ipc";

  let checking = $state(false);
  let info = $state<UpdateInfo | null>(null);
  let jobId = $state<number | null>(null);
  let percent = $state(0);
  let status = $state("");
  let errorText = $state("");

  let { startupVersion = null }: { startupVersion?: string | null } = $props();

  $effect(() => {
    if (startupVersion && (!info || info.version !== startupVersion)) {
      void check();
    }
  });

  let unlisten: (() => void) | null = null;

  const busy = $derived(checking || jobId !== null);

  function message(update: UpdateInfo): string {
    return `Доступна версия ${update.version}`;
  }

  async function check(): Promise<void> {
    errorText = "";
    status = "";
    checking = true;
    try {
      info = await commands.checkUpdates();
      status = info ? message(info) : "Обновлений нет";
    } catch (error) {
      errorText = describeError(error);
    } finally {
      checking = false;
    }
  }

  async function installExact(update: UpdateInfo): Promise<void> {
    errorText = "";
    percent = 0;
    status = `Загрузка ${update.version}...`;
    try {
      jobId = await commands.startUpdate(update);
    } catch (error) {
      status = "";
      errorText = describeError(error);
    }
  }

  function install(): void {
    if (info) void installExact(info);
  }

  function apply(event: JobEvent): void {
    if (jobId === null || event.job_id !== jobId || event.kind !== "update") return;
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
        break;
      case "canceled":
        jobId = null;
        percent = 0;
        status = "Обновление отменено";
        break;
      case "failed":
        jobId = null;
        percent = 0;
        status = "";
        errorText = event.message;
        break;
    }
  }

  onMount(() => {
    let disposed = false;
    void onJobEvent(apply).then((unsubscribe) => {
      if (disposed) unsubscribe();
      else unlisten = unsubscribe;
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
  });
</script>

<div class="updater">
  {#if info}
    <span class="status ok">Доступна версия {info.version}</span>
    {#if jobId === null}
      <button onclick={() => void install()}>Установить</button>
    {:else}
      <div class="progress" aria-label="Загрузка обновления">
        <span style:width={`${percent}%`}></span>
      </div>
    {/if}
  {:else}
    <button onclick={() => void check()} disabled={busy}>
      {checking ? "Проверка..." : "Проверить обновления"}
    </button>
  {/if}

  {#if status}
    <span class="status">{status}</span>
  {/if}
  {#if errorText}
    <span class="status error">{errorText}</span>
  {/if}
</div>

<style>
  .updater {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  button {
    padding: 3px 10px;
    font-size: 12px;
  }

  .progress {
    width: 140px;
  }

  .status {
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .status.ok {
    color: var(--ok);
  }

  .status.error {
    color: #ffb3b0;
  }
</style>
