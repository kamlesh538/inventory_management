; Inno Setup script: wraps dist\JBHonda\ into a single JBHonda-Setup-<version>.exe
; Build:  iscc /DAppVersion=0.1.0 packaging\installer.iss   (build.ps1 does this)
;
; Data safety: the database lives in C:\ProgramData\JaiBajrangBaliHonda, which this installer
; creates but never deletes. Uninstalling, reinstalling or upgrading leaves it untouched.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Jai Bajrang Bali Honda"
#define AppExe "JBHonda.exe"
#define DataDir "{commonappdata}\JaiBajrangBaliHonda"

[Setup]
; Never change AppId: Windows uses it to recognise upgrades of the same app.
AppId={{2C124640-E3C7-45E9-9ECC-E7DB57836E0E}
AppName={#AppName} - Inventory & Billing
AppVersion={#AppVersion}
AppVerName={#AppName} Inventory {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\JaiBajrangBaliHonda
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=JBHonda-Setup-{#AppVersion}
SetupIconFile=..\src\jbhonda\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} - Inventory & Billing
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
; Close the running app (via Windows Restart Manager) before replacing its files on upgrade.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Dirs]
; Shared data folder all Windows users on this PC can write to; never removed by uninstall.
Name: "{#DataDir}"; Permissions: users-modify; Flags: uninsneveruninstall

[InstallDelete]
; Remove the previous version's program files (not data) so no stale libraries are left behind.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\JBHonda\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Data folder (backups, invoices)"; Filename: "{#DataDir}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    MsgBox('The program has been removed.' + #13#10 + #13#10 +
           'Your data, backups and invoices are kept in:' + #13#10 +
           ExpandConstant('{#DataDir}') + #13#10 + #13#10 +
           'Installing the app again will pick them up automatically.', mbInformation, MB_OK);
end;
