<#
.SYNOPSIS
Run the exact R240 HMS full pytest selection with file-backed terminal evidence.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = 'E:\CAD_CAM_Project\.venv\Scripts\python.exe'
$evidence = [IO.Path]::GetFullPath($EvidenceDirectory)
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Không tìm thấy Python QA đã xác minh: $pythonPath"
}
if (Test-Path -LiteralPath $evidence) {
    throw "R240 full-suite evidence root already exists"
}
[IO.Directory]::CreateDirectory($evidence) | Out-Null
$stdout = Join-Path $evidence 'full-suite.stdout.log'
$stderr = Join-Path $evidence 'full-suite.stderr.log'
$junit = Join-Path $evidence 'full-suite.junit.xml'
$metadata = Join-Path $evidence 'full-suite.meta.json'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$started = [DateTimeOffset]::Now.ToString('o')

Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m pytest -p pytestqt.plugin -o qt_api=pyside6 `
        '--basetemp=.pytest_tmp/r240_full_suite_final' `
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
    format = 'HMS_R240_FILE_BACKED_FULL_SUITE'
    format_version = 1
    command = @($pythonPath, '-m', 'pytest', '-p', 'pytestqt.plugin', '-o', 'qt_api=pyside6', '--basetemp=.pytest_tmp/r240_full_suite_final', '-m', 'not benchmark and not windows_native', "--junitxml=$junit")
    started_at = $started
    ended_at = [DateTimeOffset]::Now.ToString('o')
    exit_code = $exitCode
    files = $files
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $metadata -Encoding utf8

exit $exitCode
