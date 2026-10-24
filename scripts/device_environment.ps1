$taskRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Join-Path $taskRoot 'data/tooling'
$env:JAVA_HOME = (Get-ChildItem -LiteralPath (Join-Path $toolsRoot 'java') -Directory | Select-Object -First 1).FullName
$env:ANDROID_HOME = Join-Path $toolsRoot 'android'
$env:ANDROID_USER_HOME = Join-Path $toolsRoot 'android-user'
$env:ANDROID_AVD_HOME = Join-Path $toolsRoot 'avd'
$env:APPIUM_HOME = Join-Path $toolsRoot 'appium-home'
$env:Path = "$env:JAVA_HOME/bin;$env:ANDROID_HOME/platform-tools;$env:Path"
