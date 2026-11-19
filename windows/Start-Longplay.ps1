$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$project = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $project

function Find-Python {
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
    )) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    foreach ($name in @('py.exe', 'python.exe')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found) {
            try {
                & $found.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null
                if ($LASTEXITCODE -eq 0) { return $found.Source }
            } catch { }

        }
    }
    return $null
}

try {
    Write-Host 'Longplay - Windows setup' -ForegroundColor Green
    Write-Host 'First launch requires internet access. Keep this window open while using the app.'
    $python = Find-Python
    if (-not $python) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw 'Install Python 3.12 from https://www.python.org/downloads/windows/ then run Start-Longplay.cmd again.'
        }
        Write-Host 'Installing Python 3.12 for your Windows account...'
        & $winget.Source install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed. Install Python 3.12 manually, then try again.' }
        $python = Find-Python
        if (-not $python) { throw 'Python was installed. Close this window and run Start-Longplay.cmd again.' }
    }
    $venvPython = Join-Path $project '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Creating the local Python environment...'
        & $python -m venv (Join-Path $project '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    $requirements = Join-Path $project 'requirements.txt'
    $stamp = Join-Path $project '.venv\longplay-requirements.sha256'
    $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    if (-not (Test-Path -LiteralPath $stamp) -or (Get-Content -LiteralPath $stamp -Raw).Trim() -ne $hash) {
        Write-Host 'Installing Longplay dependencies...'
        & $venvPython -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your internet connection and retry.' }
        Set-Content -LiteralPath $stamp -Value $hash
    }
    # Keep FFmpeg portable, inside this extracted folder. No administrator install.
    $tools = Join-Path $project 'tools'
    $ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    $ffprobe = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
    if (-not $ffmpeg -or -not $ffprobe) {
        $portable = Join-Path $tools 'ffmpeg'
        $bins = @(Get-ChildItem -LiteralPath $portable -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue)
        if ($bins.Count -eq 0) {
            New-Item -ItemType Directory -Force -Path $tools | Out-Null
            $archive = Join-Path $tools 'ffmpeg.zip'
            Write-Host 'Downloading portable FFmpeg from gyan.dev (this can take several minutes)...'
            Invoke-WebRequest -UseBasicParsing -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $archive
            Expand-Archive -LiteralPath $archive -DestinationPath $portable -Force
            Remove-Item -LiteralPath $archive
            $bins = @(Get-ChildItem -LiteralPath $portable -Filter ffmpeg.exe -Recurse)
        }
        if ($bins.Count -eq 0) { throw 'FFmpeg could not be installed.' }
        $env:PATH = $bins[0].DirectoryName + ';' + $env:PATH
    }
    & ffmpeg.exe -version > $null
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg did not start.' }
    & ffprobe.exe -version > $null
    if ($LASTEXITCODE -ne 0) { throw 'FFprobe did not start.' }

    # Choose an available port rather than stopping an unrelated program.
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, 3000)
    try { $listener.Start() } catch {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, 0)
        $listener.Start()
    }
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    $localUrl = "http://127.0.0.1:$port"
    Write-Host "`nOn this PC: $localUrl" -ForegroundColor Green
    Write-Host 'On iPhone: connect to the same private Wi-Fi, then open one of these in Safari:'
    $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
        $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' -and $_.AddressState -eq 'Preferred'
    })
    foreach ($address in $addresses) { Write-Host "  http://$($address.IPAddress):$port" }
    Write-Host 'If Windows Firewall asks, allow Python on PRIVATE networks only.'
    Write-Host 'Keep the PC awake. Close this window or press Ctrl+C to stop Longplay.'
    Write-Host ''
    $server = Start-Process -FilePath $venvPython -ArgumentList @('-m','uvicorn','app:app','--host','0.0.0.0','--port',"$port") -WorkingDirectory $project -NoNewWindow -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { throw 'The Longplay server stopped during startup.' }
        try {
            $request = [System.Net.WebRequest]::Create($localUrl)
            $request.Proxy = $null
            $request.Timeout = 2000
            $response = $request.GetResponse()
            try { $ready = ([int]$response.StatusCode -eq 200) } finally { $response.Close() }
            if ($ready) { break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { Write-Warning "The browser check could not connect. The server is still running; open $localUrl manually." }
    try { Start-Process $localUrl } catch { Write-Warning "Open $localUrl in your browser." }
    $server.WaitForExit()
    if ($server.ExitCode -ne 0) { throw 'Longplay stopped unexpectedly. Review the messages above.' }
} catch {
    Write-Host "`n$($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    if (Get-Variable server -ErrorAction SilentlyContinue) {
        if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
    }
}
