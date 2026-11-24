# Список изменений

Все заметные изменения проекта Music Tools документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии следуют [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

### Добавлено

- Манифесты зависимостей: `requirements.txt` (runtime) и `requirements-dev.txt`
  (pytest, PyInstaller, необязательные линтеры), закреплённые на проверенных версиях.
- `pyproject.toml` с метаданными проекта и конфигурацией pytest, ruff и black.
- `CHANGELOG.md` для ведения истории версий.
- `docs/BUILD.md` с инструкцией по сборке приложения и установщика.
- `.editorconfig` для единообразного форматирования файлов.
- Тест `tests/test_version_sync.py`, проверяющий согласованность версии между
  `utils/constants.py`, `pyproject.toml` и `installer.iss`.

### Изменено

- `main.spec` переоформлен для сопровождения, UPX отключён ради корректной
  подписи кода и репутации в антивирусах.
- Из `.gitignore` удалено правило `*.spec`, чтобы спек сборки попадал в репозиторий;
  добавлены правила для логов и временных файлов загрузок.

## [1.0.0]

<!-- TODO: уточнить дату релиза -->

### Добавлено

- Скачивание медиа по URL в формате mp4, wav и mp3.
- Конвертация в mp3, wav, flac и ogg.
- Превью перед скачиванием и конвертацией.
- Работа из системного трея.
- Автозапуск вместе с Windows.
- Автообновление через GitHub Releases.

[Unreleased]: https://github.com/SchazyDev/MusicTools/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/SchazyDev/MusicTools/releases/tag/v1.0.0
