#define AppVersion GetEnv('AI_GLASS_VERSION')
[Setup]
AppId={{750C440B-2667-4314-A24B-C33640DB20A4}
AppName=AI Glass
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\AI Glass
DefaultGroupName=AI Glass
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir=..\dist
OutputBaseFilename=AI-Glass-{#AppVersion}-windows-x64-setup
SetupIconFile=..\build\widget.ico
UninstallDisplayIcon={app}\AI-Glass.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
CloseApplications=yes
[Files]
Source: "..\dist\AI-Glass\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\AI Glass"; Filename: "{app}\AI-Glass.exe"
Name: "{userdesktop}\AI Glass"; Filename: "{app}\AI-Glass.exe"; Tasks: desktopicon
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\AI-Glass.exe"; Description: "Launch AI Glass"; Flags: nowait postinstall skipifsilent
[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "AI Glass"; Flags: uninsdeletevalue
