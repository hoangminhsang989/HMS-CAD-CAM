[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$source = 'E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R233_FANUC_SHL_COMPLETE_CONTEXT_AND_ISOLATED_G40_REMEDIATION\R233_CANDIDATE'
$root = 'E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R240_ACTIVE_POST_NC_REPRODUCTION'
$dest = Join-Path $root 'ACTIVE_WORKZONE_COPY'
$output = Join-Path $dest 'workzone\SHEET\260601---BL-CUM-DAN-DONG--25X226_5-L1_01.fn'
$global = 'C:\ProgramData\WORKNC\2021.0\pospro\FANUC-SHL.dat'
if (Test-Path -LiteralPath $root) { throw "R240 active NC evidence root already exists" }
New-Item -ItemType Directory -Path $root | Out-Null
Copy-Item -LiteralPath $source -Destination $dest -Recurse
if (Test-Path -LiteralPath $output) { Remove-Item -LiteralPath $output -Force }
$before = (Get-FileHash -LiteralPath $global -Algorithm SHA256).Hash.ToLowerInvariant()
$zone = Join-Path $dest 'workzone'; $tmp = Join-Path $dest 'tmp'
Copy-Item -LiteralPath $global -Destination (Join-Path $tmp 'machine.dat') -Force
Copy-Item -LiteralPath (Join-Path $zone '[MACHINE_POSITION_1].mctx') -Destination (Join-Path $tmp 'machine.mctx') -Force
$env:WNCHOME = 'C:\WORKNC2021'
$env:WNCZONE = $zone
$env:TMP = $tmp
$env:WNCPOSPRO = 'C:\ProgramData\WORKNC\2021.0\pospro'
$env:WNCCLIENT_ROOT = 'C:\ProgramData\WORKNC\2021.0'
$env:WNCCLIENT = 'C:\ProgramData\WORKNC\2021.0\client'
$env:WNCCONF = 'C:\ProgramData\WORKNC\2021.0\client\wncconf'
$env:PATH = 'C:\WORKNC2021\procdos;C:\WORKNC2021\exe\i64;C:\WORKNC2021\exe\msw;' + $env:PATH
$stdout = Join-Path $root 'worknc.stdout.log'; $stderr = Join-Path $root 'worknc.stderr.log'
$started = [DateTimeOffset]::Now.ToString('o')
Push-Location -LiteralPath $zone
try {
    & 'C:\WORKNC2021\exe\msw\wncmain.exe' exec 'ppadd' 1 'FANUC-SHL' 1> $stdout 2> $stderr
    $processExitCode = $LASTEXITCODE
}
finally { Pop-Location }
$ended = [DateTimeOffset]::Now.ToString('o')
$after = (Get-FileHash -LiteralPath $global -Algorithm SHA256).Hash.ToLowerInvariant()
$record = [ordered]@{
    format = 'HMS_R240_ACTIVE_POST_WORKNC_INVOCATION'; format_version = 1
    command = @('C:\WORKNC2021\exe\msw\wncmain.exe', 'exec', 'ppadd', '1', 'FANUC-SHL')
    started_at = $started; ended_at = $ended; process_exit_code = $processExitCode
    output_exists = (Test-Path -LiteralPath $output -PathType Leaf)
    output_size = if (Test-Path -LiteralPath $output -PathType Leaf) { (Get-Item -LiteralPath $output).Length } else { $null }
    output_sha256 = if (Test-Path -LiteralPath $output -PathType Leaf) { (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null }
    global_sha256_before = $before; global_sha256_after = $after; global_unchanged = ($before -eq $after)
    stdout_sha256 = (Get-FileHash -LiteralPath $stdout -Algorithm SHA256).Hash.ToLowerInvariant()
    stderr_sha256 = (Get-FileHash -LiteralPath $stderr -Algorithm SHA256).Hash.ToLowerInvariant()
}
$record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $root 'INVOCATION.json') -Encoding utf8
if (-not $record.output_exists -or -not $record.global_unchanged) { exit 1 }
exit 0
