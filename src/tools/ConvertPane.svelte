<script lang="ts">
  /** Заглушка вкладки «Конвертация» — наполняется на фазе 2. */
  import type { ToolDescriptor } from "../lib/ipc";

  let { descriptor }: { descriptor: ToolDescriptor } = $props();
</script>

<section>
  <h2>{descriptor.title}</h2>
  <p class="muted">{descriptor.description}</p>
  <div class="card">
    <p>Вкладка зарегистрирована в реестре Rust и подключена к фронтенду.</p>
    <p class="muted">
      Здесь появится перекодирование: перетаскивание файлов и папок, список с
      прогрессом по каждому файлу, выбор выходного формата и папки назначения.
    </p>
  </div>
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

  p {
    margin: 0 0 6px;
  }
</style>
