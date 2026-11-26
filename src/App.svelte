<script lang="ts">
  /**
   * Корневой компонент: вкладки строятся из реестра инструментов Rust.
   * Порядок и заголовки приходят из `get_tools`, тела вкладок — из `panes.ts`.
   */
  import { onMount } from "svelte";
  import { listen } from "@tauri-apps/api/event";
  import { commands, describeError, type AppInfo, type ToolDescriptor, type UpdateInfo } from "./lib/ipc";
  import { panes } from "./lib/panes";
  import UpdateChecker from "./components/UpdateChecker.svelte";

  let tools: ToolDescriptor[] | null = $state(null);
  let info: AppInfo | null = $state(null);
  let errorMessage: string | null = $state(null);
  let unlistenUpdates: (() => void) | null = null;
  let startupUpdate: { version: string } | null = $state(null);
  let activeId: string | null = $state(null);

  async function load(): Promise<void> {
    tools = null;
    info = null;
    errorMessage = null;
    try {
      const [loadedTools, loadedInfo] = await Promise.all([commands.getTools(), commands.appInfo()]);
      tools = loadedTools;
      info = loadedInfo;
      activeId = loadedTools[0]?.id ?? null;
    } catch (error) {
      errorMessage = describeError(error);
    }
  }

  onMount(() => {
    void load();

    let disposed = false;
    void listen<UpdateInfo>("update://available", (event) => {
      startupUpdate = { version: event.payload.version };
    }).then((unsubscribe) => {
      if (disposed) unsubscribe();
      else unlistenUpdates = unsubscribe;
    });

    return () => {
      disposed = true;
      unlistenUpdates?.();
    };
  });
</script>

<main class="shell">
  <header class="titlebar">
    <div class="brand">
      <h1>Music Tools</h1>
      {#if info}
        <span class="version">версия {info.version}</span>
      {/if}
    </div>
  </header>

  {#if errorMessage}
    <section class="placeholder error">
      <p>Не удалось связаться с приложением: {errorMessage}</p>
      <button onclick={() => void load()}>Повторить</button>
    </section>
  {:else if tools === null || info === null}
    <section class="placeholder">Загрузка реестра инструментов…</section>
  {:else}
    <nav class="tabs" aria-label="Разделы">
      {#each tools as tool (tool.id)}
        <button
          class="tab"
          class:active={activeId === tool.id}
          title={tool.description}
          onclick={() => (activeId = tool.id)}
        >
          {tool.title}
        </button>
      {/each}
    </nav>

    <section class="content">
      {#each tools as tool (tool.id)}
        {#if activeId === tool.id}
          {@const Pane = panes[tool.kind]}
          <Pane descriptor={tool} info={info} />
        {/if}
      {/each}
    </section>

    <footer class="statusbar">
      <UpdateChecker startupVersion={startupUpdate?.version ?? null} />
      <span class="muted">{info.os}</span>
    </footer>
  {/if}
</main>

<style>
  .shell {
    display: flex;
    flex-direction: column;
    height: 100%;
  }

  .titlebar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }

  .brand {
    display: flex;
    align-items: baseline;
    gap: 10px;
  }

  .brand h1 {
    font-size: 16px;
  }

  .version {
    color: var(--muted);
    font-size: 12px;
  }

  .tabs {
    display: flex;
    gap: 4px;
    padding: 8px 12px 0;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
  }

  .tab {
    border: 1px solid transparent;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    background: transparent;
    padding: 8px 18px;
    color: var(--muted);
  }

  .tab.active {
    background: var(--bg);
    border-color: var(--border);
    color: var(--text);
    margin-bottom: -1px;
  }

  .content {
    flex: 1;
    overflow: auto;
    padding: 16px;
  }

  .placeholder {
    display: flex;
    flex-direction: column;
    gap: 12px;
    align-items: center;
    justify-content: center;
    flex: 1;
    color: var(--muted);
  }

  .placeholder.error {
    color: #ffb3b0;
  }

  .statusbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 6px 16px;
    border-top: 1px solid var(--border);
    background: var(--panel);
    font-size: 12px;
  }
</style>
