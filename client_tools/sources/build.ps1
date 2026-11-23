param([string]$DotNet = 'dotnet', [string]$PackageSource)
$ErrorActionPreference = 'Stop'
$kit = (Get-Item -LiteralPath (Join-Path $PSScriptRoot '../..')).FullName
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('of-launch-build-' + [Guid]::NewGuid().ToString('N'))
$publish = Join-Path $scratch 'publish'
try {
    $options = @('--artifacts-path', $scratch, '--output', $publish, '-c', 'Release', '--nologo')
    if ($PackageSource) { $options += @('--source', $PackageSource) }
    & $DotNet publish (Join-Path $PSScriptRoot 'OverField.Launcher.csproj') @options
    if ($LASTEXITCODE -ne 0) { throw 'Launcher publish failed' }
    Copy-Item -LiteralPath (Join-Path $publish 'OverField.Launcher.exe') -Destination (Join-Path $kit '开放空间私服.exe') -Force
} finally {
    $parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ((Split-Path $scratch -Parent) -eq $parent -and (Test-Path -LiteralPath $scratch)) {
        Remove-Item -LiteralPath $scratch -Force -Recurse
    }
}
