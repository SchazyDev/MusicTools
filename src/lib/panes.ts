/**
 * Сопоставление вида инструмента и Svelte-компонента.
 *
 * Добавление инструмента: регистрация в Rust (`tools_registry::builtin_tools`)
 * и запись в этой таблице. Остальное — вкладки, состояние, IPC — уже есть.
 */
import type { Component } from "svelte";
import type { AppInfo, ToolKind, ToolDescriptor } from "../lib/ipc";
import DownloadPane from "../tools/DownloadPane.svelte";
import ConvertPane from "../tools/ConvertPane.svelte";
import AboutPane from "../tools/AboutPane.svelte";

/** Пропсы, которые App передаёт любой панели инструмента. */
export interface ToolPaneProps {
  descriptor: ToolDescriptor;
  info: AppInfo;
}

export const panes: Record<ToolKind, Component<ToolPaneProps>> = {
  download: DownloadPane,
  convert: ConvertPane,
  about: AboutPane,
};
