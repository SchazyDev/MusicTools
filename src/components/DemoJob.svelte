<script lang="ts">
  /**
   * Демонстрация контракта заданий: запуск, прогресс через событие
   * `job://event`, отмена через команду `cancel_job`.
   * Реальные задания download/convert используют тот же шаблон.
   */
  import { onDestroy, onMount } from "svelte";
  import { commands, describeError, onJobEvent, type JobEvent } from "../lib/ipc";

  let jobId: number | null = $state(null);
  let percent = $state(0);
  let status = $state("Готово к запуску");
  let finished = $state(true);
  let errorText = $state<string | null>(null);
  let unlisten: (() => void) | null = null;

  function apply(event: JobEvent): void {
    if (jobId === null || event.job_id !== jobId) return;
    switch (event.state) {
      case "progress":
        percent = event.percent;
        break;
      case "status":
        status = event.message;
        break;
      case "ok":
        percent = 100;
        status = event.message;
        finished = true;
        break;
      case "canceled":
        status = "Задание отменено";
        finished = true;
        break;
      case "failed":
        status = event.message;
        errorText = event.message;
        finished = true;
        break;
    }
  }

  async function start(): Promise<void> {
    errorText = null;
    finished = false;
    percent = 0;
    status = "Запуск…";
    try {
      jobId = await commands.startDemoJob(20);
    } catch (error) {
      errorText = describeError(error);
      status = "Не удалось запустить задание";
      finished = true;
    }
  }

  async function cancel(): Promise<void> {
    if (jobId === null) return;
    try {
      await commands.cancelJob(jobId);
    } catch (error) {
      errorText = describeError(error);
    }
  }

  onMount(async () => {
    unlisten = await onJobEvent(apply);
  });

  onDestroy(() => {
    unlisten?.();
  });
</script>

<div class="demo">
  <span class="label">Проверка заданий:</span>
  <button onclick={() => void start()} disabled={!finished}>Запустить</button>
  <button class="danger" onclick={() => void cancel()} disabled={finished}>Отменить</button>
  <div class="progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
    <span style:width="{percent}%"></span>
  </div>
  <span class="status" class:error={errorText !== null}>{status}</span>
</div>

<style>
  .demo {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
  }

  .label {
    color: var(--muted);
  }

  button {
    padding: 3px 10px;
    font-size: 12px;
  }

  .progress {
    width: 160px;
  }

  .status {
    color: var(--muted);
    min-width: 180px;
  }

  .status.error {
    color: #ffb3b0;
  }
</style>
