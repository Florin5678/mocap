; Inno Setup script: a small mocapapp-setup.exe that runs install.ps1 (the bootstrapper) with a visible
; progress window. Per-user install, no admin rights. Build: iscc mocapapp.iss  (Inno Setup 6)
; The ref to install can be overridden at build time: iscc /DRef=mocapapp-v0.2.0rc3 mocapapp.iss

#ifndef Ref
  #define Ref "mocapapp-v0.2.0rc3"
#endif
; CI also builds a test setup with /DPauseArg= so that nothing waits for Enter
#ifndef PauseArg
  #define PauseArg " -Pause"
#endif
#ifndef AppVersion
  #define AppVersion "0.2.0rc3"
#endif

[Setup]
AppId={{6B0E6A55-2F51-4C1B-9C55-6D1C3E8E5A10}
AppName=mocapapp
AppVersion={#AppVersion}
AppPublisher=mocapapp (research use only)
AppPublisherURL=https://github.com/Florin5678/mocap
; The install folder can be chosen (e.g. on a drive with more space): install.ps1 puts everything in it, and
; this setup's own files go into its setup subfolder. The default is the earlier installation's folder.
DefaultDirName={code:DefaultDir}
UsePreviousAppDir=no
DisableDirPage=no
UninstallFilesDir={app}\setup
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=mocapapp-setup
OutputDir=..\..\dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=license.txt
UninstallDisplayName=mocapapp
MinVersion=10.0
; install.ps1 registers the .mocapproj file type (per user); this tells Explorer after setup and uninstall
ChangesAssociations=yes
#if VER >= EncodeVer(6, 7, 0)
; Inno Setup 6.7+ turns on Windows' RedirectionGuard by default and passes it to install.ps1, uv and
; Python. RedirectionGuard refuses junctions made by non-admin users, and uv links its Python with one
; (python\cpython-3.12-... -> cpython-3.12.x-...), so step 3 failed with "untrusted mount point"
; (os error 448). This setup needs no admin rights and writes only into the user's own folder, so
; the mitigation protects nothing here.
RedirectionGuard=no
#endif

[Messages]
FinishedLabel=mocapapp is installed. Start it from the Start menu or the desktop shortcut "mocapapp".

[Files]
Source: "install.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "uninstall.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
Source: "license.txt"; DestDir: "{app}\setup"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup\install.ps1"" -Ref ""{#Ref}"" -Prefix ""{app}""{#PauseArg}"; \
  StatusMsg: "Downloading and installing mocapapp (10-30 minutes; a window shows the progress) ..."; \
  Flags: waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\setup\uninstall.ps1"" -Prefix ""{app}"""; \
  Flags: waituntilterminated runhidden; RunOnceId: "RemoveMocapapp"

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6B0E6A55-2F51-4C1B-9C55-6D1C3E8E5A10}_is1';

// The earlier installation's folder, else %LOCALAPPDATA%\mocapapp. Up to 0.2.0rc2 the folder Windows remembers
// was the setup's own one (<install folder>\setup, with install.ps1 in it), so that one is taken one level up.
function DefaultDir(Param: String): String;
var
  Dir: String;
begin
  Result := ExpandConstant('{localappdata}\mocapapp');
  if RegQueryStringValue(HKCU, UninstallKey, 'Inno Setup: App Path', Dir) and (Dir <> '') then
  begin
    Dir := RemoveBackslashUnlessRoot(Dir);
    if FileExists(AddBackslash(Dir) + 'install.ps1') then
      Dir := ExtractFileDir(Dir);
    Result := Dir;
  end;
end;
