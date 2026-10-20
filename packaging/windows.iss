#ifndef AppVersion
  #error AppVersion must be supplied by packaging/release.py
#endif

[Setup]
AppId={{83C490C3-7ECC-4D20-9764-740CD2FFDBAF}
AppName=Subject to Change
AppVersion={#AppVersion}
AppPublisher=husamemadH
AppPublisherURL=https://github.com/husamemadH/subject-to-change
DefaultDirName={localappdata}\Programs\Subject to Change
DefaultGroupName=Subject to Change
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ReleaseOutput}
OutputBaseFilename=Subject-to-Change-{#AppVersion}-windows-x64-Setup
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
UninstallDisplayIcon={app}\subject-to-change.exe
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#AppSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Subject to Change"; Filename: "{app}\subject-to-change.exe"
Name: "{autodesktop}\Subject to Change"; Filename: "{app}\subject-to-change.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\subject-to-change.exe"; Description: "Launch Subject to Change"; Flags: nowait postinstall skipifsilent
