[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

try {
    $projectRoot = (Resolve-Path -LiteralPath (Join-Path -Path $PSScriptRoot -ChildPath '..')).Path
    $referenceRoot = Join-Path -Path $projectRoot -ChildPath 'reference_private'

    $relativeDirectories = @(
        'INBOX'
        'WORKNC\TRAINING'
        'WORKNC\ONLINE_HELP'
        'WORKNC\CAM_3D'
        'WORKNC\TOOLPATH_PARAMETERS'
        'WORKNC\SIMULATION'
        'WORKNC\POSTPROCESSOR'
        'MASTERCAM\INBOX'
        'MASTERCAM\UI_OPERATION_MANAGER'
        'MASTERCAM\MILL_2D'
        'MASTERCAM\MILL_3D'
        'MASTERCAM\DRILLING'
        'MASTERCAM\LATHE'
        'MASTERCAM\WIRE'
        'MASTERCAM\MULTIAXIS'
        'MASTERCAM\POSTPROCESSOR'
        'MASTERCAM\PRACTICE_FILES'
        'NX\INBOX'
        'NX\CAD_3D'
        'NX\SOLID_MODELING'
        'NX\SURFACE_MODELING'
        'NX\ASSEMBLY'
        'NX\DATUM_COORDINATES'
        'NX\CAM_REFERENCE'
    )

    Write-Output "Project root: $projectRoot"
    Write-Output "Reference root: $referenceRoot"

    foreach ($relativeDirectory in $relativeDirectories) {
        $directoryPath = Join-Path -Path $referenceRoot -ChildPath $relativeDirectory
        New-Item -ItemType Directory -Path $directoryPath -Force | Out-Null
        Write-Output "Ensured: $directoryPath"
    }
}
catch {
    Write-Error "Không thể tạo cấu trúc reference_private: $($_.Exception.Message)"
    exit 1
}
