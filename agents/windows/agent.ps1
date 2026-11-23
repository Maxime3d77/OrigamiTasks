# OrigamiTasks Windows Agent - Windows PowerShell 5.1+ / Windows 10, 11, Server 2016+
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ConfigFile = Join-Path $PSScriptRoot 'config.json'
$Version = '0.3.41'
$ModulesFile = Join-Path $PSScriptRoot 'modules.json'

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
function Save-AgentModules($response, $agentId) {
    $mods=$response.modules
    $revision=$response.modules_revision
    $policy=$response.pra_policy
    if ($null -eq $mods -or $null -eq $revision -or $null -eq $policy) { return }
    if (-not $mods.standard -or $mods.pra -or $mods.proxmox) {
        # Windows agents are workflow executors only: reject infra/PRA policies.
        return
    }
    foreach ($key in @('replication_enabled','promotion_enabled','fencing_ready','witness_ready')) {
        if ($policy.$key -ne $false) { return }
    }
    $obj=@{
        agent_id=$agentId
        revision=[int]$revision
        modules=@{standard=$true;proxmox=$false;pra=$false}
        site=$response.site
        pra_policy=@{
            replication_enabled=$false
            promotion_enabled=$false
            fencing_ready=$false
            witness_ready=$false
        }
    }
    $tmp="$ModulesFile.tmp"
    $obj | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $ModulesFile -Force
}
function Get-AgentModuleReport($agentId) {
    $status=@{standard='ready';proxmox='unsupported';pra='unsupported'}
    $revision=0
    try {
        if (Test-Path -LiteralPath $ModulesFile -PathType Leaf) {
            $policy=Get-Content -LiteralPath $ModulesFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ([int]$policy.agent_id -eq $agentId) {
                $revision=[int]$policy.revision
                if ($policy.modules.proxmox -eq $true -or $policy.modules.pra -eq $true) { $revision=0 }
            }
        }
    } catch { $revision=0; $status.proxmox='error' }
    return @{modules_applied_revision=$revision;modules_status=$status}
}
$cfg = Read-AgentConfig
if (-not $cfg.token) {
    $response = Send-AgentRequest $cfg '/api/agent/enroll' @{agent_id=$cfg.agent_id;enrollment_token=$cfg.enrollment_token;platform='windows';hostname=$env:COMPUTERNAME} $false
    $cfg | Add-Member -NotePropertyName token -NotePropertyValue $response.token -Force
    $cfg.PSObject.Properties.Remove('enrollment_token')
    Save-AgentConfig $cfg
}
$interval = 10
if ($null -ne $cfg.poll_interval_seconds) {
    $interval=[Math]::Min(60,[Math]::Max(5,[int]$cfg.poll_interval_seconds))
}
$delay=$interval
$lastWall=[DateTime]::UtcNow
$lastCpu=(Get-Process -Id $PID).CPU
while ($true) {
    try {
        $p=Get-Process -Id $PID
        $now=[DateTime]::UtcNow
        $elapsed=[Math]::Max(0.001,($now-$lastWall).TotalSeconds)
        $cpuPercent=[Math]::Round([Math]::Max(0,($p.CPU-$lastCpu)*100/$elapsed),2)
        $lastCpu=$p.CPU
        $lastWall=$now
        $rssMb=[Math]::Round($p.WorkingSet64/1MB,2)
        $moduleStatus=Get-AgentModuleReport ([int]$cfg.agent_id)
        $result = Send-AgentRequest $cfg '/api/agent/poll' @{
            hostname=$env:COMPUTERNAME;version=$Version
            cpu_percent=$cpuPercent;memory_mb=$rssMb
            modules_applied_revision=$moduleStatus.modules_applied_revision
            modules_status=$moduleStatus.modules_status
        }
        Save-AgentModules $result ([int]$cfg.agent_id)
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
        $delay=$interval
    } catch {
        Write-Error ('Agent polling error: ' + $_.Exception.GetType().Name) -ErrorAction Continue
        $delay=[Math]::Min($delay*2,60)
    }
    Start-Sleep -Seconds $delay
}
