# Signs one Windows file (program, installer, uninstaller) with BEYONDERP's code-signing certificate, then checks it.
# Used by .github/workflows/deploy.yml (and by Inno Setup for the installers' uninstallers). Nothing to sign with:
# says so and leaves the file as it is.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging\sign.ps1 <file>
#
# The certificate, from the repository's Actions secrets (Settings -> Secrets and variables -> Actions):
#   A certificate file:      SIGN_PFX_BASE64 (the .pfx as base64) and SIGN_PFX_PASSWORD
#   SSL.com eSigner (cloud): ES_USERNAME, ES_PASSWORD, ES_CREDENTIAL_ID, ES_TOTP_SECRET
# Each signature is timestamped, so files stay valid after the certificate itself expires.
param([Parameter(Mandatory = $true)][string]$File)
$ErrorActionPreference = "Stop"

$File = (Resolve-Path $File).Path
$tmp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$timestamp = "http://timestamp.digicert.com"
$pfx = $env:SIGN_PFX_BASE64
$es = $env:ES_USERNAME -and $env:ES_PASSWORD -and $env:ES_CREDENTIAL_ID -and $env:ES_TOTP_SECRET

function SignTool {
  $found = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
  if (-not $found) { throw "signtool.exe (Windows SDK) was not found on this computer" }
  return $found.FullName
}

if (-not $pfx -and -not $es) {
  Write-Host "::notice::Not signed (no code-signing certificate set up): $File"
  exit 0
}

if ($pfx) {
  $cert = Join-Path $tmp ("beyonderp-sign-" + [guid]::NewGuid().ToString("N") + ".pfx")
  try {
    [IO.File]::WriteAllBytes($cert, [Convert]::FromBase64String($pfx))
    & (SignTool) sign /f $cert /p $env:SIGN_PFX_PASSWORD /fd sha256 /tr $timestamp /td sha256 /d "BeyondERP" /du "https://mybeyonderp.com/" $File
    if ($LASTEXITCODE -ne 0) { throw "signtool could not sign $File" }
  } finally {
    Remove-Item $cert -Force -ErrorAction SilentlyContinue
  }
} else {
  # SSL.com's CodeSignTool (it carries its own Java); downloaded once per build
  $home_ = Join-Path $tmp "codesigntool"
  $bat = Get-ChildItem $home_ -Recurse -Filter CodeSignTool.bat -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $bat) {
    New-Item -ItemType Directory -Force $home_ | Out-Null
    Invoke-WebRequest "https://ssl.com/download/codesigntool-for-windows/" -OutFile (Join-Path $home_ "cst.zip")
    Expand-Archive (Join-Path $home_ "cst.zip") -DestinationPath $home_ -Force
    $bat = Get-ChildItem $home_ -Recurse -Filter CodeSignTool.bat | Select-Object -First 1
    if (-not $bat) { throw "CodeSignTool was not in SSL.com's download" }
  }
  $out = Join-Path $tmp ("signed-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force $out | Out-Null
  Push-Location $bat.DirectoryName
  try {
    & $bat.FullName sign "-credential_id=$($env:ES_CREDENTIAL_ID)" "-username=$($env:ES_USERNAME)" "-password=$($env:ES_PASSWORD)" `
      "-totp_secret=$($env:ES_TOTP_SECRET)" "-input_file_path=$File" "-output_dir_path=$out"
    if ($LASTEXITCODE -ne 0) { throw "CodeSignTool could not sign $File" }
  } finally {
    Pop-Location
  }
  $signed = Join-Path $out (Split-Path $File -Leaf)
  if (-not (Test-Path $signed)) { throw "CodeSignTool did not write the signed $File" }
  Move-Item $signed $File -Force
  Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
}

# the signature must check out with Windows' own rules before the file is published
& (SignTool) verify /pa $File
if ($LASTEXITCODE -ne 0) { throw "the signature on $File does not verify" }
Write-Host "Signed: $File"
