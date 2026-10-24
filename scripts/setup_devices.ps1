param([switch]$AcceptAndroidLicenses)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Join-Path $taskRoot 'data/tooling'
New-Item -ItemType Directory -Force $toolsRoot | Out-Null

function Get-VerifiedArchive([string]$Url, [string]$Name, [string]$Checksum) {
    $destination = Join-Path $toolsRoot $Name
    if (-not (Test-Path -LiteralPath $destination)) {
        Invoke-WebRequest -Uri $Url -OutFile $destination
    }
    if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Checksum.ToLowerInvariant()) {
        throw "Checksum mismatch: $Name"
    }
    return $destination
}

if (-not $AcceptAndroidLicenses) {
    throw 'Run with -AcceptAndroidLicenses after reviewing https://developer.android.com/studio/terms'
}
$jdk = Invoke-RestMethod 'https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=windows'
$package = $jdk[0].binary.package
$archive = Get-VerifiedArchive $package.link 'jdk17.zip' $package.checksum
$javaRoot = Join-Path $toolsRoot 'java'
if (-not (Test-Path -LiteralPath $javaRoot)) { Expand-Archive -LiteralPath $archive -DestinationPath $javaRoot }
$env:JAVA_HOME = (Get-ChildItem -LiteralPath $javaRoot -Directory | Select-Object -First 1).FullName
$env:ANDROID_HOME = Join-Path $toolsRoot 'android'
$env:ANDROID_USER_HOME = Join-Path $toolsRoot 'android-user'
$env:ANDROID_AVD_HOME = Join-Path $toolsRoot 'avd'
$env:APPIUM_HOME = Join-Path $toolsRoot 'appium-home'
$env:Path = "$env:JAVA_HOME/bin;$env:ANDROID_HOME/platform-tools;$env:Path"
New-Item -ItemType Directory -Force $env:ANDROID_USER_HOME,$env:ANDROID_AVD_HOME | Out-Null
$sdkArchive = Get-VerifiedArchive 'https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip' 'android-commandline.zip' '90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a'
$commandRoot = Join-Path $env:ANDROID_HOME 'cmdline-tools'
$latest = Join-Path $commandRoot 'latest'
if (-not (Test-Path -LiteralPath $latest)) {
    Expand-Archive -LiteralPath $sdkArchive -DestinationPath $commandRoot
    $extracted = [System.IO.Path]::GetFullPath((Join-Path $commandRoot 'cmdline-tools'))
    $destination = [System.IO.Path]::GetFullPath($latest)
    $boundary = [System.IO.Path]::GetFullPath($toolsRoot) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $extracted.StartsWith($boundary) -or -not $destination.StartsWith($boundary)) { throw 'SDK extraction path escapes tools directory' }
    Move-Item -LiteralPath $extracted -Destination $destination
}
$sdkManager = Join-Path $latest 'bin/sdkmanager.bat'
(1..20 | ForEach-Object { 'y' }) | & $sdkManager "--sdk_root=$env:ANDROID_HOME" --licenses *> (Join-Path $toolsRoot 'android-licenses.log')
if ($LASTEXITCODE) { throw 'Android SDK license acceptance failed' }
& $sdkManager "--sdk_root=$env:ANDROID_HOME" 'platform-tools' 'emulator' 'platforms;android-35' 'build-tools;35.0.0' 'system-images;android-35;google_apis;x86_64'
if ($LASTEXITCODE) { throw 'Android SDK installation failed' }
& npm install --prefix (Join-Path $toolsRoot 'appium') 'appium@3.8.0' --no-audit --no-fund
if ($LASTEXITCODE) { throw 'Appium installation failed' }
$appium = Join-Path $toolsRoot 'appium/node_modules/.bin/appium.cmd'
if (-not (Test-Path -LiteralPath (Join-Path $env:APPIUM_HOME 'node_modules/appium-uiautomator2-driver/package.json'))) {
    & $appium driver install uiautomator2@8.7.0
    if ($LASTEXITCODE) { throw 'UiAutomator2 installation failed' }
}
if (-not (Test-Path -LiteralPath (Join-Path $env:APPIUM_HOME 'node_modules/appium-wincore-driver/package.json'))) {
    & $appium driver install --source=npm appium-wincore-driver@3.2.0
    if ($LASTEXITCODE) { throw 'WinCore installation failed' }
}
Write-Host "Tools installed in $toolsRoot. Dot-source scripts/device_environment.ps1 in new terminals."
