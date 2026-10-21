; Instalator Spedi (Inno Setup 6)
; Instalacja bez uprawnień admina: %LOCALAPPDATA%\Programs\Spedi

#define MyAppName "Spedi"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.1"
#endif
#define MyAppPublisher "Patryk Prośniak (hudzialke)"
#define MyAppExeName "Spedi.exe"
#ifndef MyAppSource
  #define MyAppSource "..\dist\Spedi"
#endif

[Setup]
AppId={{B8D4F0A2-5C3E-4F9B-8D2E-Spedi0000001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/hudzialke/Spedi-Markus
LicenseFile=..\LICENSE
DefaultDirName={localappdata}\Programs\Spedi
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\artifacts
OutputBaseFilename=Spedi-Setup-{#MyAppVersion}
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\spedi\ui\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utwórz skrót na Pulpicie"; GroupDescription: "Dodatkowe skróty:"; Flags: checkedonce

[Files]
Source: "{#MyAppSource}\*"; DestDir: "{app}"; Excludes: "dane\*,raporty\*,scenariusze\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Odinstaluj {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Uruchom {#MyAppName}"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\raporty"
Type: filesandordirs; Name: "{app}\scenariusze"
