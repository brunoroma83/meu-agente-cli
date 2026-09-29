# =====================================================================
# Script PowerShell de Exportação Completa do Banco de Dados (meu_agente_db)
# =====================================================================
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BackupDir = Join-Path $ProjectDir "backups"

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DumpCustom = Join-Path $BackupDir "meu_agente_db_prod.dump"
$DumpSql = Join-Path $BackupDir "meu_agente_db_prod.sql"
$ArchiveDump = Join-Path $BackupDir "backup_$Timestamp.dump"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "📦 Iniciando Exportação do Banco de Dados PostgreSQL..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# Verifica se o container db está respondendo
Write-Host "🔍 Verificando conectividade com o container do banco..." -ForegroundColor Yellow
$Ready = $false
try {
    $res = docker compose exec -T db pg_isready -U postgres -d meu_agente_db 2>&1
    if ($LASTEXITCODE -eq 0) { $Ready = $true }
} catch {}

if (-not $Ready) {
    Write-Host "⚠️ Container 'db' não está respondendo. Tentando iniciar..." -ForegroundColor Yellow
    docker compose -f "$ProjectDir\docker-compose.dev.yml" up -d db
    Start-Sleep -Seconds 3
}

# 1. Gera dump comprimido formato custom (-F c)
Write-Host "💾 Gerando dump comprimido em: $DumpCustom..." -ForegroundColor Green
$cmdCustom = "docker compose exec -T db pg_dump -U postgres -d meu_agente_db -F c --clean --if-exists"
cmd.exe /c "$cmdCustom > `"$DumpCustom`""

Copy-Item $DumpCustom $ArchiveDump

# 2. Gera dump em SQL texto
Write-Host "💾 Gerando script SQL legível em: $DumpSql..." -ForegroundColor Green
$cmdSql = "docker compose exec -T db pg_dump -U postgres -d meu_agente_db --clean --if-exists"
cmd.exe /c "$cmdSql > `"$DumpSql`""

Write-Host ""
Write-Host "✅ Exportação concluída com sucesso!" -ForegroundColor Green
Write-Host "----------------------------------------------------------"
Get-Item $DumpCustom, $DumpSql | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
Write-Host "----------------------------------------------------------"
Write-Host "Arquivos prontos para transferência para o servidor de produção:" -ForegroundColor Cyan
Write-Host "👉 $DumpCustom (Formato nativo pg_restore)"
Write-Host "👉 $DumpSql (Script SQL compatível)"
Write-Host "==========================================================" -ForegroundColor Cyan
