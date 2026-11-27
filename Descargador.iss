#define AppName "Corneteros Studio"
#define AppVersion "2.2.0"
#define AppPublisher "EthanBeis"
#define AppExeName "Descargador2.exe"

[Setup]
AppId={{2D489BC2-6E67-4A25-8B9D-5C99F1D4B862}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
SetupIconFile=app.ico
WizardImageFile=installer\wizard.bmp
WizardImageBackColor=#000000
WizardSmallImageFile=installer\wizard_small.bmp
WizardBackColor=#000000
DefaultDirName={autopf}\Descargador
DefaultGroupName={#AppName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=Instalador_Corneteros_Studio_{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern dark
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
Uninstallable=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Files]
Source: "dist\Descargador2\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Corneteros Studio"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\Corneteros Studio"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Iniciar Descargador"; Flags: postinstall nowait skipifsilent
