; ============================================================================
; Music Tools - скрипт установщика (Inno Setup)
;
; МОДЕЛЬ УСТАНОВКИ БЕЗ ПРАВ АДМИНИСТРАТОРА
; ----------------------------------------------------------------------------
; Установка выполняется без повышения прав (PrivilegesRequired=lowest).
; Это принципиально важно: Windows UIPI (User Interface Privilege Isolation)
; запрещает процессу с высоким уровнем целостности (High IL, т.е. запущенному
; от администратора) принимать OLE drag-and-drop файлов из обычного Проводника
; (Medium IL). Если приложение стартует из элевированного процесса установки,
; оно наследует High IL, и перетаскивание файлов в приложение блокируется
; на уровне ОС: Qt вообще не получает dragEnterEvent, а Проводник рисует
; курсор "запрещено". Поэтому установщик НЕ повышает права, и приложение
; запускается через [Run] с флагом runasoriginaluser.
;
; Каталог установки задан как {autopf}: в режиме администратора это
; {commonpf} (Program Files), а в обычном режиме без прав - {localappdata}\Programs.
; Одно и то же выражение корректно покрывает оба случая.
; ============================================================================

[Setup]
AppName=Music Tools
AppVersion=1.1.0
AppPublisher=Schazy
AppPublisherURL=https://github.com/SchazyDev/MusicTools
AppSupportURL=https://t.me/schazyprod
AppUpdatesURL=https://github.com/SchazyDev/MusicTools/releases

; Стабильный AppId (никогда не перегенерируется между сборками): благодаря ему
; новая версия обновляет предыдущую установку, а не ставится рядом с ней.
AppId={{7C4F2B1E-9D3A-4A6B-8E5C-2F1A0D6C4B93}}
UsePreviousAppDir=yes
UsePreviousTasks=yes

; {autopf} -> {commonpf} в режиме администратора и {localappdata}\Programs
; в обычном режиме без прав; одно выражение покрывает оба сценария.
DefaultDirName={autopf}\MusicTools
DefaultGroupName=MusicTools
UninstallDisplayIcon={app}\MusicTools.exe
Compression=lzma2
SolidCompression=yes
OutputDir=dist
OutputBaseFilename=MusicTools_v1_1_0_setup
SetupIconFile=icons\icon.ico
WizardStyle=modern
MinVersion=10.0

; Установка без обязательного повышения прав; пользователь при желании
; может запросить установку для всех пользователей (диалог или --admin).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
CloseApplications=yes
RestartApplications=no

ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
russian.TaskGroup=Дополнительные задачи:
english.TaskGroup=Additional tasks:
russian.DesktopIconTask=Создать ярлык на рабочем столе
english.DesktopIconTask=Create a desktop shortcut
russian.StartupTask=Запускать при старте Windows
english.StartupTask=Start Windows automatically
russian.LaunchApp=Запустить Music Tools
english.LaunchApp=Launch Music Tools

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIconTask}"; GroupDescription: "{cm:TaskGroup}"; Flags: unchecked
Name: "startup"; Description: "{cm:StartupTask}"; GroupDescription: "{cm:TaskGroup}"; Flags: unchecked

[Files]
Source: "dist\main.exe"; DestDir: "{app}"; DestName: "MusicTools.exe"; Flags: ignoreversion
Source: "ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "icons\icon.ico"; DestDir: "{app}\icons"; Flags: ignoreversion
Source: "icons\logo.png"; DestDir: "{app}\icons"; Flags: ignoreversion
Source: "icons\checkmark.png"; DestDir: "{app}\icons"; Flags: ignoreversion
Source: "resources\helvetica.otf"; DestDir: "{app}\resources"; Flags: ignoreversion
Source: "resources\style.qss"; DestDir: "{app}\resources"; Flags: ignoreversion

[Icons]
Name: "{group}\Music Tools"; Filename: "{app}\MusicTools.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icons\icon.ico"
Name: "{autodesktop}\Music Tools"; Filename: "{app}\MusicTools.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icons\icon.ico"; Tasks: desktopicon

[Registry]
; ВНИМАНИЕ: ValueName должен совпадать с APP_NAME из utils/constants.py -
; именно это значение использует приложение для переключателя автозапуска.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Music Tools"; ValueData: "{app}\MusicTools.exe"; Flags: uninsdeletevalue; Tasks: startup

[Run]
; runasoriginaluser: приложение запускается от имени исходного пользователя,
; а НЕ от элевированного процесса установки. Это и есть ключевое исправление
; для UIPI (drag-and-drop файлов начинает работать).
Filename: "{app}\MusicTools.exe"; Description: "{cm:LaunchApp}"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallDelete]
Type: filesandordirs; Name: "{app}\fauna_tools.log"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
