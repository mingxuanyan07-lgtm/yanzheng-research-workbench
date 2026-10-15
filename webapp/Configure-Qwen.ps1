param(
    [string]$BaseUrl = '',
    [string]$ModelName = 'qwen-plus'
)

$ErrorActionPreference = 'Stop'
$researchConfigPath = Join-Path $PSScriptRoot '.env'
$researchUtf8 = New-Object System.Text.UTF8Encoding($false)

Write-Host '研证 · 配置百炼千问' -ForegroundColor Cyan
Write-Host '请在此本机窗口输入。API Key 不显示、不写入聊天。'
Write-Host '配置保存在 webapp/.env；网页只显示配置状态。'
Write-Host ''

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = Read-Host '粘贴控制台的 OpenAI 兼容 Base URL（以 /compatible-mode/v1 结尾）'
}
$BaseUrl = $BaseUrl.Trim().TrimEnd('/')
$researchUri = $null
if (-not [Uri]::TryCreate($BaseUrl, [UriKind]::Absolute, [ref]$researchUri)) {
    throw '接入地址格式不正确，请复制控制台的 HTTPS Base URL。'
}
$researchHost = $researchUri.DnsSafeHost.ToLowerInvariant()
$researchAllowedHost = $researchHost -in @('dashscope.aliyuncs.com', 'dashscope-intl.aliyuncs.com', 'dashscope-us.aliyuncs.com') -or
    $researchHost -match '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(cn-beijing|ap-southeast-1|us-east-1|ap-northeast-1|cn-hongkong|eu-central-1)\.maas\.aliyuncs\.com$'
if ($researchUri.Scheme -ne 'https' -or -not $researchAllowedHost -or $researchUri.UserInfo -or
    $researchUri.Query -or $researchUri.Fragment -or $researchUri.AbsolutePath -ne '/compatible-mode/v1' -or
    -not $researchUri.IsDefaultPort) {
    throw '请使用百炼官方 HTTPS 兼容接口地址，不能包含账号、查询参数或非标准端口。'
}
if ($ModelName -notmatch '^[A-Za-z0-9._:/-]{1,100}$') { throw '模型名称格式不正确。' }

$researchSecureKey = Read-Host '粘贴 API Key 后按 Enter（输入隐藏）' -AsSecureString
$researchKeyPointer = [IntPtr]::Zero
try {
    $researchKeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($researchSecureKey)
    $researchPlainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($researchKeyPointer).Trim()
    if ($researchPlainKey.Length -lt 12 -or $researchPlainKey -match '[\s"'']') {
        throw 'API Key 为空或含有不支持的字符，请重新配置。'
    }
    $researchLines = @(
        'YANZHENG_MODEL_PROVIDER=qwen',
        "DASHSCOPE_API_KEY=$researchPlainKey",
        "QWEN_BASE_URL=$BaseUrl",
        "QWEN_MODEL=$ModelName",
        'QWEN_TIMEOUT_SECONDS=120',
        'QWEN_MAX_TOKENS=3500'
    )
    [IO.File]::WriteAllText($researchConfigPath, (($researchLines -join "`n") + "`n"), $researchUtf8)
} finally {
    if ($researchKeyPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($researchKeyPointer) }
    $researchPlainKey = $null
    $researchLines = $null
    if ($researchSecureKey) { $researchSecureKey.Dispose() }
}

Write-Host ''
Write-Host '配置已保存；尚未发起付费模型调用。' -ForegroundColor Green
Write-Host '回到研证网页，点击“刷新状态”，再点击“用千问生成方向”。'
Write-Host '如项目已选题，请先返回候选比较。'
