$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent $PSScriptRoot
$log = Join-Path $raiz "logs_upload.txt"
$adminUsuario = if ($env:ADMIN_USUARIO) { $env:ADMIN_USUARIO } else { "admin" }
$adminSenha = $env:ADMIN_SENHA
if (-not $adminSenha) {
    Write-Error "Defina ADMIN_SENHA antes de executar este script."
    exit 1
}
"[$(Get-Date -Format HH:mm:ss)] retomando upload do video 3" | Out-File -FilePath $log -Encoding utf8

$arquivo = (Get-ChildItem -Path (Join-Path $raiz "arquivos") -Filter *.mp4 | Sort-Object Length | Select-Object -First 1).FullName
python (Join-Path $raiz "scripts\upload_video.py") --admin $adminUsuario --senha $adminSenha --video 3 $arquivo 2>&1 |
    Out-File -FilePath $log -Append -Encoding utf8
"[$(Get-Date -Format HH:mm:ss)] video 3 concluido (exit=$LASTEXITCODE)" | Out-File -FilePath $log -Append -Encoding utf8
