<script lang="ts">
  /** Вкладка «О программе»: версия, пути, журнал. */
  import { commands, describeError, type AppInfo, type ToolDescriptor } from "../lib/ipc";

  let { descriptor, info }: { descriptor: ToolDescriptor; info: AppInfo } = $props();
  let errorText = $state<string | null>(null);

  async function openLog(): Promise<void> {
    errorText = null;
    try {
      await commands.openLogFile();
    } catch (error) {
      errorText = describeError(error);
    }
  }
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

  .actions {
    display: flex;
    gap: 8px;
  }

  .error {
    color: #ffb3b0;
  }
</style>
