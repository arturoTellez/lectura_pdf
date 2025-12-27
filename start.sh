#!/bin/bash
# Script para iniciar la aplicación de lectura de estados de cuenta

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🚀 Iniciando aplicación de lectura de estados de cuenta..."

# Verificar si Docker está corriendo
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker no está corriendo. Por favor, inicia Docker primero."
    exit 1
fi

# Construir y levantar contenedor
docker-compose up -d --build

echo "✅ Aplicación iniciada correctamente"
echo "📊 Accede a: http://localhost:8501"
echo ""
echo "Comandos útiles:"
echo "  - Ver logs: docker-compose logs -f"
echo "  - Detener: docker-compose down"
echo "  - Reiniciar: docker-compose restart"
