; Inno Setup script — builds dist/Aria-Setup-<version>.exe from dist/Aria. Run through packaging/build.py.
; Installs for the current user by default (no admin prompt); the library, settings and downloads
; live in %LOCALAPPDATA%\Aria and are kept when Aria is uninstalled or updated.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Root ".."

[Setup]
AppId={{6E0B7C1A-4D2F-4B8E-9A51-3C7D2E8F1A90}
AppName=Aria
AppVersion={#AppVersion}
AppVerName=Aria {#AppVersion}
AppPublisher=alanwu-9582
AppPublisherURL=https://github.com/alanwu-9582/Aria-MusicPlayer
DefaultDirName={autopf}\Aria
DefaultGroupName=Aria
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\dist
OutputBaseFilename=Aria-Setup-{#AppVersion}
SetupIconFile={#Root}\assets\icon.ico
UninstallDisplayIcon={app}\Aria.exe
UninstallDisplayName=Aria {#AppVersion}
LicenseFile={#Root}\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#Root}\dist\Aria\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; files of the previous version that this one no longer ships
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\Aria"; Filename: "{app}\Aria.exe"; AppUserModelID: "Aria.MusicPlayer"
Name: "{autodesktop}\Aria"; Filename: "{app}\Aria.exe"; Tasks: desktopicon; AppUserModelID: "Aria.MusicPlayer"

[Run]
Filename: "{app}\Aria.exe"; Description: "{cm:LaunchProgram,Aria}"; Flags: nowait postinstall skipifsilent
