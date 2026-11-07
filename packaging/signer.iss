; BeyondERP Signer for Windows (Inno Setup 6), built by .github/workflows/deploy.yml after packaging/signer.spec:
;   iscc /DAppVersion=1.1.0 packaging\signer.iss   -> dist-installer\BeyondERP-Signer-Setup.exe
; Installs for the current user only (no administrator rights): Start-menu shortcut, optional start with Windows
; (in the background, with its icon by the clock), an uninstaller in Settings → Apps. English and Arabic.
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define Name "BeyondERP Signer"
#define Exe "BeyondERP-Signer.exe"
#define Copyright "Copyright (c) 2026 BEYONDERP. All rights reserved."

[Setup]
AppId={{6B7E8F52-2C1D-4A7B-9C55-0B3E0D0E0003}
AppName={#Name}
AppVersion={#AppVersion}
AppVerName={#Name} {#AppVersion}
AppPublisher=BEYONDERP
AppPublisherURL=https://mybeyonderp.com/
AppSupportURL=https://mybeyonderp.com/
AppCopyright={#Copyright}
VersionInfoCompany=BEYONDERP
VersionInfoCopyright={#Copyright}
VersionInfoDescription={#Name} Setup
VersionInfoProductName={#Name}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
LicenseFile=EULA.txt
DefaultDirName={localappdata}\Programs\{#Name}
DefaultGroupName={#Name}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=BeyondERP-Signer-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#Name}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; the running Signer is stopped by the code below, so Windows' "close the applications" page is not needed
CloseApplications=no
#ifdef Sign
; signed with BEYONDERP's code-signing certificate (packaging/sign.ps1, given to ISCC as /Sbeyond=…): the uninstaller too
SignTool=beyond
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[CustomMessages]
english.AutoStart=Start the Signer when I sign in to Windows (recommended)
arabic.AutoStart=تشغيل برنامج التوقيع عند تسجيل الدخول إلى Windows (مستحسن)
english.AutoStartGroup=When Windows starts:
arabic.AutoStartGroup=عند بدء تشغيل Windows:

[Tasks]
Name: "autostart"; Description: "{cm:AutoStart}"; GroupDescription: "{cm:AutoStartGroup}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; the program folder of an older version, so none of its files linger next to the new ones
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist-signer\BeyondERP-Signer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#Name}"; Filename: "{app}\{#Exe}"; Comment: "{#Name}"
Name: "{group}\{cm:UninstallProgram,{#Name}}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#Name}"; Filename: "{app}\{#Exe}"; Tasks: desktopicon

[Registry]
; start with Windows: the same value the Signer's own "Start with Windows" switch sets
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#Name}"; ValueData: """{app}\{#Exe}"" --background"; Flags: uninsdeletevalue; Tasks: autostart
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "{#Name}"; Flags: deletevalue; Tasks: not autostart

[Run]
Filename: "{app}\{#Exe}"; Description: "{cm:LaunchProgram,{#Name}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; the settings window's browser profile and the log on this computer (the Signer's settings and keys are kept)
Type: filesandordirs; Name: "{localappdata}\BeyondERP Signer"

[Code]
procedure StopSigner;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#Exe}"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopSigner;   { an update replaces the running program's files }
  Sleep(400);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopSigner;
    Sleep(400);
  end;
end;
