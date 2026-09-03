[Setup]
AppName=Music Tools
AppVersion=1.0.0
AppPublisher=Schazy
AppPublisherURL=https://github.com/SchazyDev/MusicTools
AppSupportURL=https://t.me/schazyprod
AppUpdatesURL=https://github.com/SchazyDev/MusicTools/releases
DefaultDirName={pf}\MusicTools
DefaultGroupName=MusicTools
UninstallDisplayIcon={app}\MusicTools.exe
Compression=lzma2
SolidCompression=yes
OutputDir=dist
OutputBaseFilename=MusicTools_v1_0_0_setup
SetupIconFile=icons\icon.ico
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

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
Name: "{commondesktop}\Music Tools"; Filename: "{app}\MusicTools.exe"; WorkingDir: "{app}"; IconFilename: "{app}\icons\icon.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Music Tools"; ValueData: "{app}\MusicTools.exe"; Flags: uninsdeletevalue; Tasks: startup

[Tasks]
Name: "startup"; Description: "Запускать при старте Windows"; GroupDescription: "Дополнительные задачи:"; Flags: unchecked

[Run]
Filename: "{app}\MusicTools.exe"; Description: "Запустить Music Tools"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\fauna_tools.log"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;