[CmdletBinding()]
param(
    [ValidateSet('Quick', 'Full', 'Gui', 'Coverage', 'ParallelSafe', 'WindowsNative')]
    [string]$Mode = 'Quick'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$baseTemp = Join-Path $projectRoot '.pytest_tmp\current'
$previousQtPlatform = [Environment]::GetEnvironmentVariable(
    'QT_QPA_PLATFORM',
    'Process'
)
$locationPushed = $false

$parallelSafeTests = @(
    'tests/unit/test_cam_ids.py',
    'tests/unit/test_cam_units.py',
    'tests/unit/test_cam_revision.py',
    'tests/unit/test_cam_machine.py',
    'tests/unit/test_cam_tooling.py',
    'tests/unit/test_cam_geometry_reference.py'
)
$guiTests = @(
    'tests/qa/test_pytest_qt_smoke.py',
    'tests/test_smoke.py',
    'tests/unit/test_workspace_shell.py',
    'tests/unit/test_operation_manager_9a3.py',
    'tests/unit/test_function_editor_widgets_9a4.py',
    'tests/unit/test_facing_function_editors_9a51.py',
    'tests/unit/test_contour_function_editor_9a52.py',
    'tests/unit/test_pocket_function_editor_9a53.py',
    'tests/unit/test_milling_editor_visual_consistency_9a54.py'
)

function Invoke-ProjectPython {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & $pythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python trả exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

try {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw "Không tìm thấy interpreter dự án: $pythonPath"
    }
    New-Item -ItemType Directory -Force -Path $baseTemp | Out-Null
    Push-Location -LiteralPath $projectRoot
    $locationPushed = $true

    $dependencyProbe = 'import pytestqt, pytest_cov, pytest_timeout, psutil, xdist, pytest_benchmark'
    if ($Mode -eq 'WindowsNative') {
        $dependencyProbe += ', pywinauto'
    }
    Invoke-ProjectPython -Arguments @('-c', $dependencyProbe)

    if ($Mode -in @('Full', 'Gui')) {
        [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM', 'offscreen', 'Process')
    }
    elseif ($Mode -eq 'WindowsNative') {
        [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM', $null, 'Process')
    }

    Write-Output "HMS QA mode: $Mode"
    switch ($Mode) {
        'Quick' {
            $quickArguments = @(
                '-m', 'pytest',
                '--basetemp=.pytest_tmp/current',
                '-m', 'not benchmark and not windows_native',
                'tests/qa/test_qa_toolchain.py'
            )
            $quickArguments += $parallelSafeTests
            Invoke-ProjectPython -Arguments $quickArguments
        }
        'Full' {
            Invoke-ProjectPython -Arguments @(
                '-m', 'pytest',
                '--basetemp=.pytest_tmp/current'
            )
        }
        'Gui' {
            $guiArguments = @(
                '-m', 'pytest',
                '--basetemp=.pytest_tmp/current',
                '-m', 'not windows_native and not benchmark'
            )
            $guiArguments += $guiTests
            Invoke-ProjectPython -Arguments $guiArguments
        }
        'Coverage' {
            Invoke-ProjectPython -Arguments @(
                '-m', 'pytest',
                '--basetemp=.pytest_tmp/current',
                '--cov=hms_cadcam',
                '--cov-branch',
                '--cov-report=term-missing',
                'tests/unit/test_cam_ids.py',
                'tests/unit/test_cam_units.py',
                'tests/unit/test_cam_revision.py'
            )
        }
        'ParallelSafe' {
            $parallelArguments = @(
                '-m', 'pytest',
                '--basetemp=.pytest_tmp/current',
                '-n', '2'
            )
            $parallelArguments += $parallelSafeTests
            Invoke-ProjectPython -Arguments $parallelArguments
        }
        'WindowsNative' {
            Invoke-ProjectPython -Arguments @('tests/manual_qa_windows_native.py')
        }
    }
}
catch {
    Write-Error "HMS QA thất bại: $($_.Exception.Message)"
    exit 1
}
finally {
    [Environment]::SetEnvironmentVariable(
        'QT_QPA_PLATFORM',
        $previousQtPlatform,
        'Process'
    )
    if ($locationPushed) {
        Pop-Location
    }
}
