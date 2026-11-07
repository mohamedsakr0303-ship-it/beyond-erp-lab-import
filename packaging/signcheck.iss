; A tiny, empty setup the desktop build (.github/workflows/deploy.yml, "Signing steps check") compiles on every run into
; a throwaway folder, with no certificate: it shows that Inno Setup hands packaging/sign.ps1 the file to sign the same way
; the real installers do (/Sbeyond=… with $q and $f). Never published.
[Setup]
AppName=BeyondERP signing check
AppVersion=1.0
AppPublisher=BEYONDERP
CreateAppDir=no
Uninstallable=no
DisableWelcomePage=yes
OutputBaseFilename=signcheck
SignTool=beyond
