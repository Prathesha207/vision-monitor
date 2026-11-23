#define MyAppName "Duck Monitor"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Duck-Monitor"
#define MyAppURL "https://github.com/Prathesha207/duck-monitor"
#define MyAppExeName "Duck-Monitor.exe"

[Setup]
AppId={{D3A58E89-B41F-4DC7-817B-67E95C5C29C1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\Duck-Monitor
DisableProgramGroupPage=yes
OutputBaseFilename=Duck-Monitor-windows-x64-cuda-setup
OutputDir=dist_app
Compression=lzma2/fast
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=max
WizardStyle=modern
SetupIconFile=public\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist_app\win-unpacked\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
