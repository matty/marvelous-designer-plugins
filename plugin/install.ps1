<#
.SYNOPSIS
    Put the md-mcp listener somewhere Marvelous Designer can keep pointing at.

.DESCRIPTION
    MD registers a plugin by absolute path, and it holds that path for good: nothing in
    the API removes an entry, and a registered path that later stops existing is a
    button that fails when clicked. So the listener goes to a stable per-user location
    of its own rather than staying in a repo checkout that will be moved, renamed or
    thrown away.

    Copies the listener (and its README and manifest) to $Destination, verifying them
    against the manifest's SHA-256 when one is present -- a packaged release always has
    one, a run straight from a checkout does not. Then prints the two lines to paste
    into MD once, and puts them on the clipboard.

    Installing does not register anything: the md-mcp entry in MD's Plugin tab is
    registered inside MD, on the first run. See plugin/README.md.

.PARAMETER Destination
    Where to install. Defaults to %LOCALAPPDATA%\md-mcp.

.PARAMETER Uninstall
    Remove an install made by this script, and the record of what it registered.

.EXAMPLE
    .\install.ps1

.EXAMPLE
    .\install.ps1 -Destination D:\tools\md-mcp
#>

[CmdletBinding()]
param(
    [string] $Destination = (Join-Path $env:LOCALAPPDATA 'md-mcp'),
    [switch] $Uninstall
)

$ErrorActionPreference = 'Stop'

$source = $PSScriptRoot
$entry = 'md_mcp_listener.py'
# Where the listener keeps what it has to remember: the generated Plugin tab entries,
# and the record of which of them MD has been told about. Always under LOCALAPPDATA,
# which is inside a default install and outside a custom one, so it is named separately.
$state = Join-Path $env:LOCALAPPDATA 'md-mcp'

function Get-Sha256([string] $path) {
    if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
        return (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLower()
    }
    # Not hypothetical: measured on Windows PowerShell 5.1.26100 on the machine this
    # was written on, where Get-FileHash is simply absent. Falling back rather than
    # skipping the check -- the hash is the only thing standing between a truncated
    # download and an install.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($path)
        try {
            return -join ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString('x2') })
        }
        finally { $stream.Dispose() }
    }
    finally { $sha.Dispose() }
}

if ($Uninstall) {
    if (-not (Test-Path (Join-Path $Destination $entry))) {
        Write-Host "Nothing to uninstall: no $entry in $Destination"
    }
    else {
        # Only ever a directory this script filled. Recursively removing a path handed
        # in on the command line, on the strength of its name alone, is how an install
        # script takes someone's documents folder with it.
        Remove-Item -Path $Destination -Recurse -Force
        Write-Host "Removed $Destination"
    }
    if (Test-Path $state) {
        # The generated entries and the registration record. Removing the record is
        # what lets a later install register itself again.
        Remove-Item -Path $state -Recurse -Force
        Write-Host "Removed $state"
    }
    Write-Host ''
    Write-Host 'MD keeps its own plugin list, and its API has no call that removes an'
    Write-Host 'entry from it. Any md-mcp entries still in the Plugin tab now point at'
    Write-Host 'files that are gone -- remove them in Plug-in Manager.'
    return
}

if (-not (Test-Path (Join-Path $source $entry))) {
    throw "No $entry beside this script ($source). Run install.ps1 from the unpacked plugin package, or from the repo's plugin/ directory."
}

$manifestPath = Join-Path $source 'manifest.json'
$version = 'source checkout'
if (Test-Path $manifestPath) {
    $manifest = Get-Content -Path $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $version = "$($manifest.version) ($($manifest.commit))"
    foreach ($name in $manifest.files.PSObject.Properties.Name) {
        $file = Join-Path $source $name
        if (-not (Test-Path $file)) {
            throw "$name is listed in manifest.json but missing from $source -- the package is incomplete."
        }
        $actual = Get-Sha256 $file
        $expected = ([string] $manifest.files.$name).ToLower()
        if ($actual -ne $expected) {
            throw "$name does not match manifest.json (expected $expected, got $actual). Re-download the package rather than installing this."
        }
    }
    Write-Host "Verified $($manifest.files.PSObject.Properties.Name.Count) file(s) against manifest.json"
}
else {
    Write-Host 'No manifest.json beside this script, so nothing was verified -- this is'
    Write-Host 'a checkout rather than a release package. That is fine for your own'
    Write-Host 'machine and not what you want to hand to someone else.'
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
foreach ($name in @($entry, 'README.md', 'manifest.json')) {
    $file = Join-Path $source $name
    if (Test-Path $file) {
        Copy-Item -Path $file -Destination (Join-Path $Destination $name) -Force
    }
}

$installed = (Resolve-Path (Join-Path $Destination $entry)).Path
$bootstrap = @"
path = r"$installed"
exec(compile(open(path, encoding="utf-8").read(), path, "exec"), {"__file__": path, "__name__": "__main__"})
"@

# Kept beside the listener as well as printed: this is the one thing needed again on a
# machine where the terminal has long since been closed.
Set-Content -Path (Join-Path $Destination 'bootstrap.txt') -Value $bootstrap -Encoding UTF8

Write-Host ''
Write-Host "Installed md-mcp plugin $version to $Destination"
Write-Host ''
Write-Host 'Once, in Marvelous Designer: Main Menu > Plugins > Python Editor, paste'
Write-Host 'these two lines and press Run.'
Write-Host ''
Write-Host $bootstrap
Write-Host ''

try {
    Set-Clipboard -Value $bootstrap
    Write-Host '(copied to the clipboard; also saved as bootstrap.txt beside the listener)'
}
catch {
    Write-Host "(also saved as bootstrap.txt beside the listener)"
}

Write-Host ''
Write-Host 'That Run opens a small window with Start and Stop in it, and adds one entry'
Write-Host '-- md-mcp -- to MD''s Plugin tab, so every later session is a click and no'
Write-Host 'pasting. Press Start to open the port: the window comes up idle, because the'
Write-Host 'bridge runs Python inside MD and that should take a press rather than happen'
Write-Host 'because somebody opened a menu entry. The Run itself never finishes, which is'
Write-Host 'correct: the listener is the running script, and MD stays usable while it'
Write-Host 'serves.'
Write-Host ''
Write-Host 'Closing the window does not stop the bridge. Clicking md-mcp brings it back.'
Write-Host ''
Write-Host 'If the entry does not appear, add it by hand -- Plugin tab > Plug-in Manager'
Write-Host "> + ADD > $installed"
