# =====================================================================
# Script PowerShell de Restauração do Banco de Dados (meu_agente_db)
# =====================================================================
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BackupDir = Join-Path $ProjectDir "backups"

$DumpCustom = Join-Path $BackupDir "meu_agente_db_prod.dump"
$DumpSql = Join-Path $BackupDir "meu_agente_db_prod.sql"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "🔄 Iniciando Restauração do Banco de Dados PostgreSQL..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$RestoreFile = ""
$RestoreType = ""

if (Test-Path $DumpCustom) {
    $RestoreFile = $DumpCustom
    $RestoreType = "custom"
} elseif (Test-Path $DumpSql) {
    $RestoreFile = $DumpSql
    $RestoreType = "sql"
} else {
    Write-Host "❌ [ERRO] Nenhum arquivo de backup encontrado em $BackupDir." -ForegroundColor Red
    exit 1
}

Write-Host "📁 Arquivo de backup selecionado: $RestoreFile ($RestoreType)" -ForegroundColor Yellow

# 2. Garante que o banco está rodando
Write-Host "⏳ Verificando se o serviço 'db' está ativo..." -ForegroundColor Yellow
docker compose up -d db

$Ready = $false
for ($i = 0; $i -lt 15; $i++) {
    try {
        $res = docker compose exec -T db pg_isready -U postgres -d meu_agente_db 2>&1
        if ($LASTEXITCODE -eq 0) {
            $Ready = $true
            break
        }
    } catch {}
    Write-Host "Aguardando PostgreSQL ficar pronto ($i/15)..."
    Start-Sleep -Seconds 2
}

if (-not $Ready) {
    Write-Host "❌ [ERRO] PostgreSQL não respondeu a tempo." -ForegroundColor Red
    exit 1
}

Write-Host "✅ PostgreSQL está pronto para conexões." -ForegroundColor Green

# 3. Executa restauração
Write-Host "🚀 Restaurando dados no banco 'meu_agente_db'..." -ForegroundColor Green
if ($RestoreType -eq "custom") {
    cmd.exe /c "docker compose exec -T db pg_restore -U postgres -d meu_agente_db --clean --if-exists --no-owner --no-acl < `"$RestoreFile`""
} else {
    cmd.exe /c "docker compose exec -T db psql -U postgres -d meu_agente_db < `"$RestoreFile`""
}

Write-Host ""
Write-Host "🔍 Validando integridade dos dados restaurados..." -ForegroundColor Cyan
$valQuery = "
SELECT 'Tabelas Restauradas' as metrica, count(*)::text as total FROM information_schema.tables WHERE table_schema = 'public'
UNION ALL
SELECT 'Registros Financeiros', count(*)::text FROM financial_records
UNION ALL
SELECT 'Agentes Especializados', count(*)::text FROM agents
UNION ALL
SELECT 'Servidores MCP', count(*)::text FROM mcp_servers
UNION ALL
SELECT 'Configurações de Sistema', count(*)::text FROM settings;
"

docker compose exec -T db psql -U postgres -d meu_agente_db -c $valQuery

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "🎉 Restauração concluída com sucesso no PostgreSQL!" -ForegroundColor Green
Write-Host "Agora você pode iniciar toda a stack com: docker compose up -d" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
