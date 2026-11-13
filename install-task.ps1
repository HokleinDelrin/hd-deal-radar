<#
.SYNOPSIS
  注册（或卸载）「史低雷达」的每日采集计划任务。

.DESCRIPTION
  默认每天 09:30 跑一次：
      python -m radar.cli fetch --pages 2 --specials 30 --epic
  工作目录固定为项目根（否则 `-m radar.cli` 找不到包）。

  为什么不直接用 `radar.cli daemon` 常驻：
  任务计划程序认的是「进程跑完 + 退出码」，常驻进程在它眼里永远是「运行中」，
  机器重启/注销后也不会自己回来；单次命令 + 每日触发器才符合它的模型。
  需要 daemon 的场合（连跑多轮、带 enrich）手动 `python -m radar.cli daemon --at 09:30`。

  实测踩坑（写死在这里，别改回去）：
  1) 项目路径含 `&`（...\Softwares&Tools\game-deal-radar）。PowerShell 里 `&` 是调用运算符，
     `cd C:\...\Softwares&Tools\...` 直接报错。本脚本所有路径都用参数 / -LiteralPath 传递；
     DryRun 打印的「等价手工命令」刻意写成 `Set-Location -LiteralPath '...'` 加引号形式，
     复制粘贴即可用。
  2) 计划任务不继承你当前终端里的 PATH。本机 PATH 上的 python 曾经是 Anaconda（没有项目依赖），
     所以这里把 python 解析成绝对路径写进 -Execute，必要时用 -Python 指定。
  3) 任务计划程序不替你切工作目录，-WorkingDirectory 必须显式给，否则 import radar 失败。
  4) 默认 principal 是「仅在用户登录时运行」。机器早上没登录就整天不采；要它开机就跑加 -S4U
     （S4U 不需要密码，但没有网络凭据——本项目只访问公网 HTTP，够用）。

.PARAMETER At
  每天几点跑，HH:mm，默认 09:30。

.PARAMETER Python
  python.exe 的绝对路径；不给就取当前 PATH 上的 python 的完整路径。

.PARAMETER TaskName
  任务名，默认 game-deal-radar-daily。

.PARAMETER S4U
  不管用户是否登录都运行（S4U，无需密码）。

.PARAMETER Unregister
  卸载同名任务后退出。

.PARAMETER DryRun
  只打印将要执行的命令与注册参数，不真的注册、不真的删除。

.EXAMPLE
  .\install-task.ps1 -DryRun
  .\install-task.ps1                 # 真注册：每天 09:30
  .\install-task.ps1 -At 08:00 -S4U # 每天 08:00，未登录也跑
  .\install-task.ps1 -Unregister -DryRun
  .\install-task.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$At = "09:30",
    [string]$Python = "",
    [string]$TaskName = "game-deal-radar-daily",
    [switch]$S4U,
    [switch]$Unregister,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# ---------- 路径（含 & ，一律 LiteralPath / 参数传递） ----------
$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).ProviderPath }
$root = (Resolve-Path -LiteralPath $root).ProviderPath

$cliArgs = "-m radar.cli fetch --pages 2 --specials 30 --epic"

if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source } else { $Python = "python" }
}

# ---------- 时刻校验 ----------
# 踩坑：PowerShell 5.1 的 [datetime]::TryParseExact 只认 5 参重载（string[], provider, styles, [ref]），
# 传 $null 当格式数组会抛 MethodException —— 所以用 ParseExact + try/catch，5.1 和 7 都稳。
try {
    $atTime = [datetime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
} catch {
    throw "-At 需要 HH:mm 格式，收到 '$At'"
}

# ---------- 卸载 ----------
if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "[跳过] 没有名为 '$TaskName' 的计划任务"
        return
    }
    if ($DryRun) {
        Write-Host "[dry-run] Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
        return
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[ok] 已卸载计划任务 '$TaskName'"
    return
}

# ---------- 描述将要执行的东西 ----------
$logon = if ($S4U) { "S4U（未登录也运行）" } else { "Interactive（仅登录时运行）" }
Write-Host "[计划] 任务名   : $TaskName"
Write-Host "[计划] 触发     : 每天 $($atTime.ToString('HH:mm'))"
Write-Host "[计划] 执行     : $Python"
Write-Host "[计划] 参数     : $cliArgs"
Write-Host "[计划] 工作目录 : $root"
Write-Host "[计划] 登录方式 : $logon"

if ($DryRun) {
    # 打印手工可用的等价命令：路径含 & ，必须整体加引号 / 用 -LiteralPath
    Write-Host ""
    Write-Host "[dry-run] 等价手工命令（可直接复制）："
    Write-Host "  Set-Location -LiteralPath '$root'"
    Write-Host "  & '$Python' $cliArgs"
    Write-Host ""
    Write-Host "[dry-run] 将调用的注册命令："
    Write-Host "  Register-ScheduledTask -TaskName '$TaskName' -Action (New-ScheduledTaskAction -Execute '$Python' -Argument '$cliArgs' -WorkingDirectory '$root') -Trigger (New-ScheduledTaskTrigger -Daily -At '$($atTime.ToString('HH:mm'))') -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)) -Description '...'"
    Write-Host ""
    Write-Host "[dry-run] 没有注册任何东西。去掉 -DryRun 才会真的写入任务计划程序。"
    return
}

if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "项目根不存在：$root"
}
if ($Python -ne "python" -and -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "python 不存在：$Python（用 -Python 指定绝对路径）"
}

# ---------- 真注册 ----------
$action = New-ScheduledTaskAction -Execute $Python -Argument $cliArgs -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $atTime
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew
$principal = if ($S4U) {
    New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
} else {
    New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "史低雷达每日采集：python -m radar.cli fetch --pages 2 --specials 30 --epic（工作目录 $root）" `
    -Force | Out-Null

Write-Host "[ok] 已注册 '$TaskName'，每天 $($atTime.ToString('HH:mm')) 运行"
Write-Host "     查看：Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "     手动跑一次：Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "     卸载：.\install-task.ps1 -Unregister"
