param(
    [string]$OpenClawRoot = "$env:APPDATA\npm\node_modules\openclaw",
    [string]$NpmBinRoot = "$env:APPDATA\npm",
    [string]$PythonExe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    [string]$RouterScript = (Join-Path (Split-Path -Parent $PSScriptRoot) "openclaw_command_router.py"),
    [switch]$RestartGateway
)

$ErrorActionPreference = "Stop"

function Resolve-TargetFile {
    param(
        [string]$DistDir
    )

    $candidates = Get-ChildItem -Path $DistDir -File -Filter "commands-registry-*.js" -ErrorAction Stop
    foreach ($file in $candidates) {
        $raw = Get-Content -Path $file.FullName -Raw -Encoding UTF8
        if ($raw.Contains("function normalizeCommandBody(raw, options) {")) {
            return @{
                FilePath = $file.FullName
                Content  = $raw
            }
        }
    }
    throw "Cannot find commands-registry-*.js containing normalizeCommandBody in: $DistDir"
}

function Set-CommandWrappers {
    param(
        [string]$BinRoot,
        [string]$PyExe,
        [string]$Router
    )

    if (-not (Test-Path -LiteralPath $BinRoot)) {
        throw "npm bin root not found: $BinRoot"
    }
    if (-not (Test-Path -LiteralPath $PyExe)) {
        throw "Python executable not found: $PyExe"
    }
    if (-not (Test-Path -LiteralPath $Router)) {
        throw "Router script not found: $Router"
    }

    $wrappers = @{
        "jielong.cmd" = "jielong"
        "cuiban.cmd" = "cuiban"
        "huizong.cmd" = "huizong"
        "jlstatus.cmd" = "status"
    }

    foreach ($entry in $wrappers.GetEnumerator()) {
        $cmdPath = Join-Path $BinRoot $entry.Key
        $action = $entry.Value
        $lines = @(
            "@echo off",
            "setlocal",
            "chcp 65001 >nul",
            "set PYTHONUTF8=1",
            "set PYTHONIOENCODING=utf-8",
            "`"$PyExe`" -X utf8 `"$Router`" $action --json"
        )
        Set-Content -LiteralPath $cmdPath -Value ($lines -join "`r`n") -Encoding Ascii
        Write-Host "Wrapper updated: $cmdPath -> $action --json"
    }
}

if (-not (Test-Path -LiteralPath $OpenClawRoot)) {
    throw "OpenClaw root not found: $OpenClawRoot"
}

$distDir = Join-Path $OpenClawRoot "dist"
if (-not (Test-Path -LiteralPath $distDir)) {
    throw "OpenClaw dist directory not found: $distDir"
}

$resolved = Resolve-TargetFile -DistDir $distDir
$targetFile = $resolved.FilePath
$content = [string]$resolved.Content
$newline = if ($content.Contains("`r`n")) { "`r`n" } else { "`n" }

if ($content.Contains("NO_PREFIX_TEXT_COMMAND_MAP")) {
    Write-Host "Already patched: $targetFile"
} else {
    $insertBlockLines = @(
        "const NO_PREFIX_TEXT_COMMAND_MAP = /* @__PURE__ */ new Map([",
        "	[""\u53d1\u63a5\u9f99"", ""/bash jielong""],",
        "	[""\u53d1\u65e5\u62a5\u63a5\u9f99"", ""/bash jielong""],",
        "	[""\u50ac\u529e"", ""/bash cuiban""],",
        "	[""\u50ac\u65e5\u62a5"", ""/bash cuiban""],",
        "	[""\u6c47\u603b"", ""/bash huizong""],",
        "	[""\u65e5\u62a5\u6c47\u603b"", ""/bash huizong""],",
        "	[""\u72b6\u6001"", ""/bash jlstatus""],",
        "	[""\u63a5\u9f99\u72b6\u6001"", ""/bash jlstatus""],",
        "	[""\u67e5\u8be2\u72b6\u6001"", ""/bash jlstatus""]",
        "]);",
        "function resolveNoPrefixTextCommand(raw) {",
        "	const trimmed = raw.trim();",
        "	if (!trimmed) return null;",
        "	const normalized = trimmed.replace(/[\s\u3000]+/g, """");",
        "	if (!normalized) return null;",
        "	const strippedPunctuation = normalized.replace(/[。\uFF0E\.,，!！;；:：、]+$/g, """");",
        "	if (!strippedPunctuation) return null;",
        "	return NO_PREFIX_TEXT_COMMAND_MAP.get(strippedPunctuation) ?? null;",
        "}"
    )
    $insertBlock = [string]::Join($newline, $insertBlockLines)

    $anchor = "function normalizeCommandBody(raw, options) {"
    if (-not $content.Contains($anchor)) {
        throw "Anchor not found in target file: $anchor"
    }
    $content = $content.Replace($anchor, "$insertBlock$newline$anchor")

    $needle = "const trimmed = raw.trim();"
    $replacement = "const trimmed = raw.trim();$newline`tconst noPrefixCommand = resolveNoPrefixTextCommand(trimmed);$newline`tif (noPrefixCommand) return noPrefixCommand;"
    if (-not $content.Contains($needle)) {
        throw "Needle not found in target file: $needle"
    }
    $content = $content.Replace($needle, $replacement)

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupFile = "$targetFile.bak.no-prefix.$stamp"
    Copy-Item -LiteralPath $targetFile -Destination $backupFile -Force

    Set-Content -LiteralPath $targetFile -Value $content -Encoding UTF8
    Write-Host "Patch applied: $targetFile"
    Write-Host "Backup saved: $backupFile"
}

Set-CommandWrappers -BinRoot $NpmBinRoot -PyExe $PythonExe -Router $RouterScript

if ($RestartGateway) {
    Write-Host "Restarting OpenClaw Gateway task..."
    & schtasks /End /TN "\OpenClaw Gateway" | Out-Null
    Start-Sleep -Seconds 1
    & schtasks /Run /TN "\OpenClaw Gateway" | Out-Null
    Start-Sleep -Seconds 2
    Write-Host "Gateway task restart triggered."
}
