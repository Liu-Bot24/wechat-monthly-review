; Inno Setup script for 消息月报 (ChatMonthly). tools\build.ps1 compiles it after PyInstaller has filled build\dist\ChatMonthly
; and passes the version: ISCC /DAppVersion=1.0.0 installer.iss
;
; Per-user install, no administrator rights. The user's configuration (%APPDATA%\ChatMonthly) and the reports
; are not part of the installation, so install, upgrade and uninstall never touch them.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=x.y.z installer.iss
#endif
; AppName is what people see (Start Menu, installed apps); files and folders use the English name.
#define AppName "消息月报"
#define AppFiles "ChatMonthly"
#define AppExe "ChatMonthly.exe"

[Setup]
; The same AppId on every version is what makes a new installer upgrade the old installation in place.
AppId={{01A449C2-C3B5-462B-91A5-B8C48DEEDCE5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppFiles}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Held by the window (mr.app) and by a running job (packaging\cli.py): setup and uninstall wait until both are closed.
AppMutex=Local\ChatMonthlyWindow,Local\ChatMonthlyJob
SetupIconFile=..\src\mr\ui\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
ShowLanguageDialog=no
OutputDir=..\dist
OutputBaseFilename={#AppFiles}-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes

[Languages]
Name: "chs"; MessagesFile: "ChineseSimplified.isl"

[Messages]
UninstalledAll=已从这台电脑删除 %1。%n%n设置、接口与模型配置、会话选择和日志仍在 AppData\Roaming\ChatMonthly，月报仍在输出目录，重新安装后可以直接使用。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; The bundle is replaced as a whole, so files a newer version no longer has do not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\build\dist\{#AppFiles}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID matches mr.app, so the pinned entry and the running window share one taskbar button.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "把聊天消息整理成月报"; AppUserModelID: "ChatMonthly.App"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "把聊天消息整理成月报"; AppUserModelID: "ChatMonthly.App"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
