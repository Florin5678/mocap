; Inno Setup script: a small mocapapp-setup.exe that runs install.ps1 (the bootstrapper) with a visible
; progress window. Per-user install, no admin rights. Build: iscc mocapapp.iss  (Inno Setup 6)
; The ref to install can be overridden at build time: iscc /DRef=mocapapp-v0.1.0rc1 mocapapp.iss

#ifndef Ref
  #define Ref "mocapapp-v0.1.0rc1"
#endif
#ifndef AppVersion
  #define AppVersion "0.1.0rc1"
#endif

[Setup]
AppId={{6B0E6A55-2F51-4C1B-9C55-6D1C3E8E5A10}
AppName=mocapapp
AppVersion={#AppVersion}
AppPublisher=mocapapp (research use only)
AppPublisherURL=https://github.com/Florin5678/mocap
DefaultDirName={localappdata}\mocapapp\setup
DisableDirPage=yes
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

[Messages]
FinishedLabel=mocapapp is installed. Start it from the Start menu or the desktop shortcut "mocapapp".

[Files]
Source: "install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "license.txt"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -Ref ""{#Ref}"" -Pause"; \
  StatusMsg: "Downloading and installing mocapapp (10-30 minutes; a window shows the progress) ..."; \
  Flags: waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall.ps1"""; \
  Flags: waituntilterminated runhidden; RunOnceId: "RemoveMocapapp"
