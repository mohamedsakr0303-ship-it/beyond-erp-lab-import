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
# not "Stop": Windows PowerShell 5.1 turns a native tool's harmless stderr line (a Java or signtool warning) into a
# fatal error when its output is redirected, as in CI; each step checks its own result instead
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # Windows PowerShell's progress bar slows downloads to a crawl

$File = (Resolve-Path $File -ErrorAction Stop).Path
$tmp = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$timestamp = "http://timestamp.digicert.com"
$pfx = $env:SIGN_PFX_BASE64
$esKeys = "ES_USERNAME", "ES_PASSWORD", "ES_CREDENTIAL_ID", "ES_TOTP_SECRET"
$esMissing = @($esKeys | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
$es = $esMissing.Count -eq 0

function SignTool {
  $found = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
  if (-not $found) { throw "signtool.exe (Windows SDK) was not found on this computer" }
  return $found.FullName
}

if ($env:SIGN_PFX_PASSWORD -and -not $pfx) { throw "SIGN_PFX_PASSWORD is set but SIGN_PFX_BASE64 is not" }
if (-not $pfx -and -not $es) {
  # some of eSigner's secrets but not all: a mistake in the setup, not "no certificate", so the build stops here
  # rather than publishing unsigned files as if they were signed
  if ($esMissing.Count -lt $esKeys.Count) { throw "SSL.com eSigner is only partly set up; missing secrets: $($esMissing -join ', ')" }
  Write-Host "::notice::Not signed (no code-signing certificate set up): $File"
  exit 0
}

if ($pfx) {
  $cert = Join-Path $tmp ("beyonderp-sign-" + [guid]::NewGuid().ToString("N") + ".pfx")
  try {
    [IO.File]::WriteAllBytes($cert, [Convert]::FromBase64String($pfx))   # .NET: throws on its own
    $pass = if ($env:SIGN_PFX_PASSWORD) { @("/p", $env:SIGN_PFX_PASSWORD) } else { @() }   # a .pfx may have no password
    & (SignTool) sign /f $cert @pass /fd sha256 /tr $timestamp /td sha256 /d "BeyondERP" /du "https://mybeyonderp.com/" $File
    if ($LASTEXITCODE -ne 0) { throw "signtool could not sign $File" }
  } finally {
    Remove-Item $cert -Force -ErrorAction SilentlyContinue
  }
} else {
  # SSL.com's CodeSignTool (it carries its own Java); downloaded once per build
  $home_ = Join-Path $tmp "codesigntool"
  $bat = Get-ChildItem $home_ -Recurse -Filter CodeSignTool.bat -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $bat) {
    New-Item -ItemType Directory -Force $home_ -ErrorAction Stop | Out-Null
    Invoke-WebRequest "https://ssl.com/download/codesigntool-for-windows/" -OutFile (Join-Path $home_ "cst.zip") -UseBasicParsing -ErrorAction Stop
    Expand-Archive (Join-Path $home_ "cst.zip") -DestinationPath $home_ -Force -ErrorAction Stop
    $bat = Get-ChildItem $home_ -Recurse -Filter CodeSignTool.bat | Select-Object -First 1
    if (-not $bat) { throw "CodeSignTool was not in SSL.com's download" }
  }
  $out = Join-Path $tmp ("signed-" + [guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force $out -ErrorAction Stop | Out-Null
  # CodeSignTool goes by the file's extension; Inno Setup hands over its uninstaller as a temporary file (uninst.e32.tmp)
  $work = $File
  if ([IO.Path]::GetExtension($File).ToLower() -notin ".exe", ".dll", ".msi") {
    $work = Join-Path $tmp ("unsigned-" + [guid]::NewGuid().ToString("N") + ".exe")
    Copy-Item $File $work -Force -ErrorAction Stop
  }
  # its own Java and jar, called directly: CodeSignTool.bat would pass the secrets through cmd.exe, which reads & | < > ^
  # in a password as commands (the .bat stays the fallback)
  $java = Get-ChildItem $bat.DirectoryName -Recurse -Filter java.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  $jar = Get-ChildItem $bat.DirectoryName -Recurse -Filter "code_sign_tool*.jar" -ErrorAction SilentlyContinue | Select-Object -First 1
  $esArgs = @("sign", "-credential_id=$($env:ES_CREDENTIAL_ID)", "-username=$($env:ES_USERNAME)", "-password=$($env:ES_PASSWORD)",
    "-totp_secret=$($env:ES_TOTP_SECRET)", "-input_file_path=$work", "-output_dir_path=$out")
  Push-Location $bat.DirectoryName   # it reads conf\ from here
  try {
    if ($java -and $jar) { & $java.FullName -jar $jar.FullName @esArgs } else { & $bat.FullName @esArgs }
    if ($LASTEXITCODE -ne 0) { throw "CodeSignTool could not sign $File" }
    $signed = Join-Path $out (Split-Path $work -Leaf)
    if (-not (Test-Path $signed)) { throw "CodeSignTool did not write the signed $File" }
    Move-Item $signed $File -Force -ErrorAction Stop
  } finally {
    Pop-Location
    Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue   # on every path, failures included
    if ($work -ne $File) { Remove-Item $work -Force -ErrorAction SilentlyContinue }
  }
}

# the signature must check out with Windows' own rules before the file is published
& (SignTool) verify /pa $File
if ($LASTEXITCODE -ne 0) { throw "the signature on $File does not verify" }
Write-Host "Signed: $File"
