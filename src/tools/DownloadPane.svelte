<script lang="ts">
  /** Заглушка вкладки «Скачивание» — наполняется на фазе 1. */
  import type { ToolDescriptor } from "../lib/ipc";

  let { descriptor }: { descriptor: ToolDescriptor } = $props();
</script>

<section>
  <h2>{descriptor.title}</h2>
  <p class="muted">{descriptor.description}</p>
  <div class="card">
    <p>Вкладка зарегистрирована в реестре Rust и подключена к фронтенду.</p>
    <p class="muted">
      Здесь появится загрузка по ссылке: предварительный просмотр, выбор формата
      (mp4, mp3, wav), качества и папки назначения с прогрессом и отменой.
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
