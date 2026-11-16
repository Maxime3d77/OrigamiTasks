# OrigamiTasks Windows Agent - Windows PowerShell 5.1+ / Windows 10, 11, Server 2016+
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ConfigFile = Join-Path $PSScriptRoot 'config.json'
$Version = '0.3.0'

function Read-AgentConfig {
    Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Save-AgentConfig($config) {
    $tmp = "$ConfigFile.tmp"
    $config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $ConfigFile -Force
}
function Send-AgentRequest($cfg, [string]$path, $body, [bool]$authorized=$true) {
    if (-not ([string]$cfg.server).StartsWith('https://')) { throw 'HTTPS is required' }
    $headers = @{}
    if ($authorized) {
        $headers['Authorization'] = 'Bearer ' + $cfg.token
        $headers['X-Origami-Agent-ID'] = [string]$cfg.agent_id
    }
    Invoke-RestMethod -Uri ($cfg.server.TrimEnd('/') + $path) -Method POST -Headers $headers -ContentType 'application/json; charset=utf-8' -Body (ConvertTo-Json -InputObject $body -Depth 8 -Compress) -TimeoutSec 40 -MaximumRedirection 0
}
function Run-Command([string]$file,[string]$args,[int]$seconds) {
    $stdout = Join-Path $env:TEMP ([guid]::NewGuid().ToString() + '.out')
    $stderr = Join-Path $env:TEMP ([guid]::NewGuid().ToString() + '.err')
    try {
        $p = Start-Process -FilePath $file -ArgumentList $args -Wait:$false -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        if (-not $p.WaitForExit($seconds * 1000)) {
            try { $p.Kill() } catch {}
            return @{exit_code=124;output='Command timed out; verify before retrying'}
        }
        $out = ((Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue) + "`n" + (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue))
        return @{exit_code=[int]$p.ExitCode;output=[string]$out.Substring(0,[Math]::Min($out.Length,32000))}
    } finally {
        Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
    }
}
function Invoke-AgentTask($task) {
    $kind = [string]$task.action
    $data = $task.payload
    $timeout = [Math]::Min([Math]::Max([int]$task.timeout,1),1800)
    switch ($kind) {
        'inventory' {
            $os = Get-CimInstance Win32_OperatingSystem
            return @{exit_code=0;output=(@{hostname=$env:COMPUTERNAME; os=$os.Caption;version=$os.Version;arch=$env:PROCESSOR_ARCHITECTURE; agent=$Version}|ConvertTo-Json -Compress)}
        }
        'script' {
            $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes([string]$data.script))
            return Run-Command 'powershell.exe' ("-NoProfile -NonInteractive -EncodedCommand " + $encoded) $timeout
        }
        'processes' { return @{exit_code=0;output=((Get-Process | Select-Object -First 120 Id,ProcessName,CPU | Out-String) | Select-Object -First 32000)} }
        'disk' { return @{exit_code=0;output=(Get-PSDrive -PSProvider FileSystem | Out-String)} }
        'service_status' {
            return @{exit_code=0;output=(Get-Service -Name ([string]$data.service) -ErrorAction Stop | Out-String)}
        }
        'service_start' { Start-Service -Name ([string]$data.service) -ErrorAction Stop; return @{exit_code=0;output='Service started'} }
        'service_stop' { Stop-Service -Name ([string]$data.service) -ErrorAction Stop; return @{exit_code=0;output='Service stopped'} }
        'service_restart' { Restart-Service -Name ([string]$data.service) -ErrorAction Stop; return @{exit_code=0;output='Service restarted'} }
        default { return @{exit_code=1;output='Unsupported action'} }
    }
}
$cfg = Read-AgentConfig
if (-not $cfg.token) {
    $response = Send-AgentRequest $cfg '/api/agent/enroll' @{agent_id=$cfg.agent_id;enrollment_token=$cfg.enrollment_token;platform='windows';hostname=$env:COMPUTERNAME} $false
    $cfg | Add-Member -NotePropertyName token -NotePropertyValue $response.token -Force
    $cfg.PSObject.Properties.Remove('enrollment_token')
    Save-AgentConfig $cfg
}
$delay=3
while ($true) {
    try {
        $result = Send-AgentRequest $cfg '/api/agent/poll' @{hostname=$env:COMPUTERNAME;version=$Version}
        if ($null -ne $result.task) {
            try {
                $r = Invoke-AgentTask $result.task
            } catch {
                $r = @{exit_code=1;output=($_.Exception.GetType().Name+': '+$_.Exception.Message)}
            }
            $out = [string]$r.output
            if ($out.Length -gt 32000) { $out=$out.Substring(0,32000) }
            $null = Send-AgentRequest $cfg '/api/agent/report' @{task_id=$result.task.id;lease=$result.task.lease;exit_code=[int]$r.exit_code;output=$out}
        }
        $delay=3
    } catch {
        Write-Error ('Agent polling error: ' + $_.Exception.GetType().Name) -ErrorAction Continue
        $delay=[Math]::Min($delay*2,60)
    }
    Start-Sleep -Seconds $delay
}
