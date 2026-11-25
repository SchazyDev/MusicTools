//! Точка входа desktop-приложения.

// В Windows релизная сборка не открывает консольное окно.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    music_tools_lib::run();
}
