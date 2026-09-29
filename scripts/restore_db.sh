#!/usr/bin/env bash
# =====================================================================
# Script de Restauração Completa do Banco de Dados no Servidor (meu_agente_db)
# =====================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_DIR="${PROJECT_DIR}/backups"

DUMP_CUSTOM="${BACKUP_DIR}/meu_agente_db_prod.dump"
DUMP_SQL="${BACKUP_DIR}/meu_agente_db_prod.sql"

echo "=========================================================="
echo "🔄 Iniciando Restauração do Banco de Dados PostgreSQL..."
echo "=========================================================="

# 1. Verifica existência do arquivo de backup
RESTORE_FILE=""
RESTORE_TYPE=""

if [ -f "${DUMP_CUSTOM}" ]; then
    RESTORE_FILE="${DUMP_CUSTOM}"
    RESTORE_TYPE="custom"
elif [ -f "${DUMP_SQL}" ]; then
    RESTORE_FILE="${DUMP_SQL}"
    RESTORE_TYPE="sql"
else
    # Procura qualquer arquivo .dump na pasta backups
    ANY_DUMP=$(find "${BACKUP_DIR}" -name "*.dump" | head -n 1)
    if [ -n "${ANY_DUMP}" ]; then
        RESTORE_FILE="${ANY_DUMP}"
        RESTORE_TYPE="custom"
    else
        echo "❌ [ERRO] Nenhum arquivo de backup encontrado em ${BACKUP_DIR}."
        echo "Por favor, copie o arquivo 'meu_agente_db_prod.dump' para a pasta 'backups/'."
        exit 1
    fi
fi

echo "📁 Arquivo de backup selecionado: ${RESTORE_FILE} (${RESTORE_TYPE})"

# 2. Garante que o container do banco de dados está rodando
echo "⏳ Verificando se o serviço 'db' está ativo e saudável..."
docker compose up -d db

MAX_RETRIES=15
COUNT=0
until docker compose exec -T db pg_isready -U postgres -d meu_agente_db >/dev/null 2>&1; do
    COUNT=$((COUNT + 1))
    if [ ${COUNT} -ge ${MAX_RETRIES} ]; then
        echo "❌ [ERRO] Tempo limite esgotado esperando o PostgreSQL inicializar."
        exit 1
    fi
    echo "Aguardando PostgreSQL ficar pronto (${COUNT}/${MAX_RETRIES})..."
    sleep 2
done

echo "✅ PostgreSQL está pronto para conexões."

# 3. Executa a restauração
echo "🚀 Restaurando dados no banco 'meu_agente_db'..."
if [ "${RESTORE_TYPE}" = "custom" ]; then
    docker compose exec -T db pg_restore -U postgres -d meu_agente_db --clean --if-exists --no-owner --no-acl < "${RESTORE_FILE}" || true
else
    docker compose exec -T db psql -U postgres -d meu_agente_db < "${RESTORE_FILE}"
fi

echo ""
echo "🔍 Validando integridade dos dados restaurados..."
docker compose exec -T db psql -U postgres -d meu_agente_db -c "
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

echo ""
echo "=========================================================="
echo "🎉 Restauração concluída com sucesso no PostgreSQL!"
echo "Agora você pode iniciar toda a stack com: docker compose up -d"
echo "=========================================================="
