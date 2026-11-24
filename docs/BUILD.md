# Сборка Music Tools

Документ описывает подготовку окружения, локальный запуск, сборку исполняемого
файла и установщика, а также запуск тестов.

## Требования

- Windows 10 или Windows 11, x64.
- Python 3.13 (проверено на 3.13.14).
- Git с поддержкой Git LFS.

Файл `ffmpeg.exe` (около 142 МБ) хранится через Git LFS. Перед сборкой обязательно
выполните:

```powershell
git lfs install
git lfs pull
```

Если пропустить эти шаги, вместо настоящего бинарника в рабочей копии окажется
текстовый указатель LFS, и собранное приложение не сможет конвертировать файлы.

Inno Setup (утилита `iscc`) нужен только для сборки установщика.

## Виртуальное окружение

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

`requirements-dev.txt` подключает `requirements.txt` и добавляет pytest,
PyInstaller и необязательные линтеры ruff и black.

## Локальный запуск

```powershell
python main.py
```

## Сборка исполняемого файла

```powershell
pyinstaller main.spec
```

Результат: `dist/main.exe` (onefile, без консольного окна, UPX отключён).
Каталоги `build/` и `dist/` исключены из системы контроля версий.

## Сборка установщика

Установщик использует готовый `dist/main.exe`, поэтому сначала выполните сборку
выше, затем:

```powershell
iscc installer.iss
```

Готовый `MusicTools_v1_0_0_setup.exe` появится в каталоге, указанном в параметре
`OutputDir` файла `installer.iss`. Версия в `installer.iss` должна совпадать с
версией из `utils/constants.py` и `pyproject.toml`.

## Тесты

```powershell
pytest
```

Конфигурация pytest находится в `pyproject.toml`. Тест
`tests/test_version_sync.py` падает, если версия разошлась между
`utils/constants.py`, `pyproject.toml` и `installer.iss`.

## Известное ограничение

`ffmpeg.exe` весит около 142 МБ и хранится в Git LFS. Из-за этого клонирование
репозитория без LFS даёт непригодное окружение, а размер релизного артефакта
заметно больше, чем у приложения без встроенного ffmpeg.
