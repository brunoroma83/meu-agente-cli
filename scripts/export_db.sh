#!/usr/bin/env bash
# =====================================================================
# Script de Exportação Completa do Banco de Dados (meu_agente_db)
# =====================================================================
set -e

# Diretório base do projeto
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_DIR="${PROJECT_DIR}/backups"

mkdir -p "${BACKUP_DIR}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DUMP_CUSTOM="${BACKUP_DIR}/meu_agente_db_prod.dump"
DUMP_SQL="${BACKUP_DIR}/meu_agente_db_prod.sql"
ARCHIVE_DUMP="${BACKUP_DIR}/backup_${TIMESTAMP}.dump"

echo "=========================================================="
echo "📦 Iniciando Exportação do Banco de Dados PostgreSQL..."
echo "=========================================================="

# Identifica o comando do docker compose (pode usar compose ou compose dev)
COMPOSE_CMD="docker compose"
if docker compose ps meu-agente-db 2>/dev/null | grep -q "meu-agente-db"; then
    COMPOSE_CMD="docker compose"
elif docker compose -f "${PROJECT_DIR}/docker-compose.dev.yml" ps meu-agente-db 2>/dev/null | grep -q "meu-agente-db"; then
    COMPOSE_CMD="docker compose -f ${PROJECT_DIR}/docker-compose.dev.yml"
fi

# Verifica se o container db está respondendo
echo "🔍 Verificando conectividade com o container do banco..."
if ! ${COMPOSE_CMD} exec -T db pg_isready -U postgres -d meu_agente_db >/dev/null 2>&1; then
    echo "⚠️ O container 'db' não está respondendo. Tentando iniciar..."
    ${COMPOSE_CMD} up -d db
    sleep 3
fi

# 1. Gera o dump em formato binário comprimido (recomendado para pg_restore)
echo "💾 Gerando dump comprimido em: ${DUMP_CUSTOM}..."
${COMPOSE_CMD} exec -T db pg_dump -U postgres -d meu_agente_db -F c --clean --if-exists > "${DUMP_CUSTOM}"

# Guarda uma cópia com timestamp para histórico
cp "${DUMP_CUSTOM}" "${ARCHIVE_DUMP}"

# 2. Gera também o dump em SQL legível (fallback universal)
echo "💾 Gerando script SQL em: ${DUMP_SQL}..."
${COMPOSE_CMD} exec -T db pg_dump -U postgres -d meu_agente_db --clean --if-exists > "${DUMP_SQL}"

echo ""
echo "✅ Exportação concluída com sucesso!"
echo "----------------------------------------------------------"
ls -lh "${DUMP_CUSTOM}" "${DUMP_SQL}"
echo "----------------------------------------------------------"
echo "Arquivos prontos para transferência para o servidor de produção:"
echo "👉 ${DUMP_CUSTOM} (Formato nativo pg_restore)"
echo "👉 ${DUMP_SQL} (Script SQL compatível)"
echo "=========================================================="
