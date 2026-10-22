# Shared setup, dot-sourced by the other scripts. Mirrors the path logic in src/config.py.
# Sets $root (repo root), $ServiceName, $StorageDir, $LogsDir, $LocksDir, $StateDir.
$root = Split-Path -Parent $PSScriptRoot

# Same lookup order as src/config.py: TELECLAW_CONFIG -> repo root -> ~/.teleclaw
$configPath = @($env:TELECLAW_CONFIG, (Join-Path $root 'config.yaml'),
                (Join-Path $HOME '.teleclaw\config.yaml')) |
    Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
$configDir = if ($configPath) { Split-Path -Parent (Resolve-Path $configPath) } else { $root }

# Reads a top-level scalar from config.yaml, or returns $default.
function Get-ConfigValue($key, $default) {
    if (-not $configPath) { return $default }
    $m = Select-String -Path $configPath -Pattern "^${key}:\s*[`"']?([^`"'#]*?)[`"']?\s*(#.*)?$" -Encoding UTF8 |
        Select-Object -First 1
    if ($m -and $m.Matches[0].Groups[1].Value) { return $m.Matches[0].Groups[1].Value }
    return $default
}

$ServiceName = Get-ConfigValue 'service_name' 'TeleClaw'
$storage = Get-ConfigValue 'storage_dir' 'storage'
if ($storage.StartsWith('~')) { $storage = $HOME + $storage.Substring(1) }
$StorageDir = if ([IO.Path]::IsPathRooted($storage)) { $storage } else { Join-Path $configDir $storage }
$LogsDir  = Join-Path $StorageDir 'logs'
$LocksDir = Join-Path $StorageDir 'locks'
$StateDir = Join-Path $StorageDir 'state'
