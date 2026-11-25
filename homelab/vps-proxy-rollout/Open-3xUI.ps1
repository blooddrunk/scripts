param(
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9._-]*$')]
    [string]$Distro = 'Ubuntu'
)

$ErrorActionPreference = 'Stop'
if ([Console]::IsOutputRedirected) {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
}

function New-PanelEntry {
    param([string]$Alias, [string]$Address, [string]$RemotePort)

    if ($Alias -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]*$') {
        throw '请输入 SSH 配置中的主机别名，例如 lightlayer-zh。'
    }
    $uri = $null
    if ((-not [uri]::TryCreate($Address, [UriKind]::Absolute, [ref]$uri)) -or
        $uri.Scheme -ne 'https' -or $uri.HostNameType -ne 'Dns' -or $uri.UserInfo) {
        throw '请输入带证书域名和登录路径的 HTTPS 地址，不要在地址中填写账号密码。'
    }
    if (-not $RemotePort) {
        $RemotePort = $uri.Port
    }
    $port = 0
    if ((-not [int]::TryParse($RemotePort, [ref]$port)) -or $port -lt 1 -or $port -gt 65535) {
        throw '面板管理端口必须是 1 到 65535 之间的整数。'
    }
    [pscustomobject]@{ profile = $Alias; port = $port; url = $uri.AbsoluteUri }
}

foreach ($command in 'wsl.exe', 'curl.exe') {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "找不到 $command。请在装有 WSL 和 curl 的 Windows 上运行。"
    }
}

$browser = foreach ($app in 'Microsoft\Edge\Application\msedge.exe', 'Google\Chrome\Application\chrome.exe') {
    foreach ($root in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
        if ($root) {
            $candidate = Join-Path $root $app
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate }
        }
    }
}
$browser = $browser | Select-Object -First 1
if (-not $browser) { throw '找不到 Edge 或 Chrome。' }

$guideDir = Join-Path $env:LOCALAPPDATA 'VPS-3xUI-Guide'
$config = Join-Path $guideDir 'panels.json'
$legacy = Join-Path $env:LOCALAPPDATA 'VPS-3xUI\panels.json'
$panels = @()
$save = $false
$source = $null
if (Test-Path -LiteralPath $config) {
    $source = $config
} elseif ((Test-Path -LiteralPath $legacy) -and
    (Read-Host '沿用旧 Edge 快捷方式的面板清单？[Y/n]') -in @('', 'y')) {
    $source = $legacy
    $save = $true
}
if ($source) {
    $entries = Get-Content -LiteralPath $source -Raw | ConvertFrom-Json
    $panels = @(foreach ($entry in $entries) {
        if ($source -ne $legacy -or $entry.profile -ne 'nosla') {
            New-PanelEntry $entry.profile $entry.url $entry.port
        }
    })
}

$panel = $null
while (-not $panel) {
    Write-Host '选择要打开的 3x-ui 面板'
    for ($index = 0; $index -lt $panels.Count; $index++) {
        Write-Host ("  {0}. {1}" -f ($index + 1), $panels[$index].profile)
    }
    $choice = Read-Host '输入编号，N 新增或更新，Q 退出'
    if ($null -eq $choice -or $choice -eq 'q') { return }
    if ($choice -eq 'n') {
        try {
            $alias = (Read-Host "WSL $Distro 中的 SSH 主机别名").Trim()
            $address = (Read-Host '原面板 HTTPS 登录地址（含路径）').Trim()
            $port = (Read-Host 'VPS 上的 3x-ui 管理端口（直接回车使用地址里的端口）').Trim()
            $panel = New-PanelEntry $alias $address $port
            $panels = @($panels | Where-Object { $_.profile -ne $alias }) + @($panel)
            $save = $true
        } catch {
            Write-Warning $_.Exception.Message
        }
    } else {
        $number = 0
        if ([int]::TryParse($choice, [ref]$number) -and $number -ge 1 -and $number -le $panels.Count) {
            $panel = $panels[$number - 1]
        }
    }
}

# Pick a Windows loopback port so existing shortcuts can keep their own ports.
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
try {
    $listener.Start()
    $localPort = $listener.LocalEndpoint.Port
} finally {
    $listener.Stop()
}
$panelUri = [UriBuilder]::new($panel.url)
$panelUri.Port = $localPort
$panelHost = $panelUri.Uri.IdnHost
$url = $panelUri.Uri.AbsoluteUri
$resolve = "${panelHost}:${localPort}:127.0.0.1"
$wslArgs = @('--distribution', $Distro, '--exec')
$socketDir = & wsl.exe @wslArgs mktemp -d /tmp/vps-3xui-guide.XXXXXXXX
if ($LASTEXITCODE -ne 0 -or $socketDir -cnotmatch '^/tmp/vps-3xui-guide\.[a-zA-Z0-9]{8}$') {
    throw "无法在 WSL $Distro 中准备 SSH 隧道。"
}
$socket = "$socketDir/ssh"
$connected = $false
try {
    Write-Host "正在建立 $($panel.profile) 的 SSH 隧道。"
    $forward = "127.0.0.1:${localPort}:127.0.0.1:$($panel.port)"
    & wsl.exe @wslArgs ssh -fNT -M -S $socket -o ControlPersist=no -o ExitOnForwardFailure=yes `
        -o ConnectTimeout=8 -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -L $forward -- $panel.profile
    if ($LASTEXITCODE -ne 0) {
        throw "SSH 隧道建立失败。可先在 WSL $Distro 中运行 ssh $($panel.profile) 检查连接。"
    }
    $connected = $true

    # Give WSL localhost forwarding time to become reachable from Windows.
    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        $httpStatus = & curl.exe -q --noproxy '*' --globoff --silent --fail --max-time 6 `
            --output NUL --write-out '%{http_code}' --resolve $resolve -- $url
        $curlExit = $LASTEXITCODE
        if ($curlExit -ne 7) { break }
        Start-Sleep -Milliseconds 300
    }
    if ($curlExit -ne 0) {
        throw "Windows 无法通过隧道验证面板（curl $curlExit，HTTP $httpStatus）。请确认管理端口、证书域名和登录路径。"
    }

    $dataDir = Join-Path $guideDir "Browser\$panelHost"
    if ($save) {
        New-Item -ItemType Directory -Path $guideDir -Force | Out-Null
        ConvertTo-Json -InputObject $panels | Set-Content -LiteralPath $config -Encoding UTF8
    }
    Write-Host '已验证隧道和 HTTPS，正在打开面板。'
    Start-Process -FilePath $browser -ArgumentList @(
        "--user-data-dir=`"$dataDir`""
        "--host-resolver-rules=`"MAP $panelHost 127.0.0.1`""
        '--no-proxy-server'
        '--no-first-run'
        '--new-window'
        "`"$url`""
    )
    $null = Read-Host '面板用完后，回到这里按 Enter 关闭本次 SSH 隧道'
} finally {
    if ($connected) {
        & wsl.exe @wslArgs ssh -S $socket -O exit -- $panel.profile
    }
    & wsl.exe @wslArgs rmdir -- $socketDir
}
