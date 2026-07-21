[CmdletBinding()]
param(
    [ValidateSet('Quick', 'Full', 'Gui', 'Coverage', 'ParallelSafe', 'Benchmark', 'WindowsNative')]
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
$previousPluginAutoload = [Environment]::GetEnvironmentVariable(
    'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
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
$benchmarkTests = @(
    'tests/qa/test_qa_benchmarks.py'
)

function Invoke-ProjectPython {
    param([Parameter(Mandatory)][string[]]$Arguments)

    & $pythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python trả exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Assert-PythonModules {
    param([Parameter(Mandatory)][string[]]$Modules)

    $importStatement = "import $($Modules -join ', ')"
    Invoke-ProjectPython -Arguments @('-c', $importStatement)
}

try {
    if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
        throw "Không tìm thấy interpreter dự án: $pythonPath"
    }
    New-Item -ItemType Directory -Force -Path $baseTemp | Out-Null
    Push-Location -LiteralPath $projectRoot
    $locationPushed = $true
    [Environment]::SetEnvironmentVariable(
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
        '1',
        'Process'
    )

    if ($Mode -in @('Full', 'Gui')) {
        [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM', 'offscreen', 'Process')
    }
    elseif ($Mode -eq 'WindowsNative') {
        [Environment]::SetEnvironmentVariable('QT_QPA_PLATFORM', $null, 'Process')
    }

    Write-Output "HMS QA mode: $Mode"
    switch ($Mode) {
        'Quick' {
            Assert-PythonModules -Modules @('pytest', 'pytest_timeout')
            $quickArguments = @(
                '-m', 'pytest',
                '-p', 'pytest_timeout',
                '--basetemp=.pytest_tmp/current',
                '--timeout=60'
            )
            $quickArguments += $parallelSafeTests
            Invoke-ProjectPython -Arguments $quickArguments
        }
        'Full' {
            Assert-PythonModules -Modules @('pytest', 'pytestqt')
            Invoke-ProjectPython -Arguments @(
                '-m', 'pytest',
                '-p', 'pytestqt.plugin',
                '-o', 'qt_api=pyside6',
                '--basetemp=.pytest_tmp/current',
                '-m', 'not benchmark and not windows_native'
            )
        }
        'Gui' {
            Assert-PythonModules -Modules @('pytest', 'pytestqt', 'pytest_timeout')
            $guiArguments = @(
                '-m', 'pytest',
                '-p', 'pytestqt.plugin',
                '-p', 'pytest_timeout',
                '-o', 'qt_api=pyside6',
                '--basetemp=.pytest_tmp/current',
                '--timeout=180',
                '-m', 'not windows_native and not benchmark'
            )
            $guiArguments += $guiTests
            Invoke-ProjectPython -Arguments $guiArguments
        }
        'Coverage' {
            Assert-PythonModules -Modules @('pytest', 'pytest_cov', 'pytest_timeout')
            Invoke-ProjectPython -Arguments @(
                '-m', 'pytest',
                '-p', 'pytest_cov.plugin',
                '-p', 'pytest_timeout',
                '--basetemp=.pytest_tmp/current',
                '--timeout=120',
                '--cov=hms_cadcam',
                '--cov-branch',
                '--cov-report=term-missing',
                'tests/unit/test_cam_ids.py',
                'tests/unit/test_cam_units.py',
                'tests/unit/test_cam_revision.py'
            )
        }
        'ParallelSafe' {
            Assert-PythonModules -Modules @('pytest', 'xdist', 'pytest_timeout')
            $parallelArguments = @(
                '-m', 'pytest',
                '-p', 'xdist.plugin',
                '-p', 'pytest_timeout',
                '--basetemp=.pytest_tmp/current',
                '--timeout=60',
                '-n', '2'
            )
            $parallelArguments += $parallelSafeTests
            Invoke-ProjectPython -Arguments $parallelArguments

            Write-Output 'Xác nhận lại tuần tự cho nhóm ParallelSafe'
            $serialArguments = @(
                '-m', 'pytest',
                '-p', 'pytest_timeout',
                '--basetemp=.pytest_tmp/current',
                '--timeout=60'
            )
            $serialArguments += $parallelSafeTests
            Invoke-ProjectPython -Arguments $serialArguments
        }
        'Benchmark' {
            Assert-PythonModules -Modules @('pytest', 'pytest_benchmark')
            $benchmarkArguments = @(
                '-m', 'pytest',
                '-p', 'pytest_benchmark.plugin',
                '--basetemp=.pytest_tmp/current',
                '-m', 'benchmark'
            )
            $benchmarkArguments += $benchmarkTests
            Invoke-ProjectPython -Arguments $benchmarkArguments
        }
        'WindowsNative' {
            Assert-PythonModules -Modules @('PySide6', 'psutil', 'pywinauto')
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
    [Environment]::SetEnvironmentVariable(
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD',
        $previousPluginAutoload,
        'Process'
    )
    if ($locationPushed) {
        Pop-Location
    }
}
