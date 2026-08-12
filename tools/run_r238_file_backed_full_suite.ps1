<#
.SYNOPSIS
Run the exact HMS full pytest selection with file-backed terminal evidence.

R238 uses this helper because interactive console transport previously failed
before pytest produced a terminal result.  It does not mutate product files;
it only writes explicit QA evidence below the selected evidence directory.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = 'E:\CAD_CAM_Project\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Không tìm thấy Python QA đã xác minh: $pythonPath"
}

$evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
[IO.Directory]::CreateDirectory($evidence) | Out-Null
$stdout = Join-Path $evidence 'full-suite.stdout.log'
$stderr = Join-Path $evidence 'full-suite.stderr.log'
$junit = Join-Path $evidence 'full-suite.junit.xml'
$metadata = Join-Path $evidence 'full-suite.meta.json'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$started = [DateTime]::UtcNow.ToString('o')

Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m pytest -p pytestqt.plugin -o qt_api=pyside6 `
        '--basetemp=.pytest_tmp/r238_full_suite_final/basetemp' `
        '-m' 'not benchmark and not windows_native' `
        "--junitxml=$junit" 1> $stdout 2> $stderr
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

$files = @{}
foreach ($path in @($stdout, $stderr, $junit)) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        $files[[IO.Path]::GetFileName($path)] = [ordered]@{
            path = $path
            size = (Get-Item -LiteralPath $path).Length
            sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
}
[ordered]@{
    format = 'HMS_R238_FILE_BACKED_FULL_SUITE'
    format_version = 1
    command = @($pythonPath, '-m', 'pytest', '-p', 'pytestqt.plugin', '-o', 'qt_api=pyside6', '--basetemp=.pytest_tmp/r238_full_suite_final/basetemp', '-m', 'not benchmark and not windows_native', "--junitxml=$junit")
    started_at_utc = $started
    ended_at_utc = [DateTime]::UtcNow.ToString('o')
    exit_code = $exitCode
    files = $files
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $metadata -Encoding utf8

exit $exitCode
