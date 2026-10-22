$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent $PSScriptRoot
$log = Join-Path $raiz "logs_upload.txt"
$adminUsuario = if ($env:ADMIN_USUARIO) { $env:ADMIN_USUARIO } else { "admin" }
$adminSenha = $env:ADMIN_SENHA
if (-not $adminSenha) {
    Write-Error "Defina ADMIN_SENHA antes de executar este script."
    exit 1
}
"[$(Get-Date -Format HH:mm:ss)] iniciando upload dos videos reais" | Out-File -FilePath $log -Encoding utf8

# Ordena por tamanho: o maior e o encontro 1 (video 2), o menor o encontro 2 (video 3).
$arquivos = Get-ChildItem -Path (Join-Path $raiz "arquivos") -Filter *.mp4 | Sort-Object Length -Descending
$ids = @(2, 3)

for ($i = 0; $i -lt $arquivos.Count; $i++) {
    $videoId = $ids[$i]
    "[$(Get-Date -Format HH:mm:ss)] enviando video $videoId : $($arquivos[$i].Name) ($([math]::Round($arquivos[$i].Length/1GB,2)) GB)" |
        Out-File -FilePath $log -Append -Encoding utf8
    python (Join-Path $raiz "scripts\upload_video.py") --admin $adminUsuario --senha $adminSenha --video $videoId $arquivos[$i].FullName 2>&1 |
        Out-File -FilePath $log -Append -Encoding utf8
    "[$(Get-Date -Format HH:mm:ss)] video $videoId concluido (exit=$LASTEXITCODE)" |
        Out-File -FilePath $log -Append -Encoding utf8
}
"[$(Get-Date -Format HH:mm:ss)] todos os uploads concluidos" | Out-File -FilePath $log -Append -Encoding utf8
