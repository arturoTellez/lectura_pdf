# 🏦 Lectura de Estados de Cuenta - Podman Setup

Aplicación Streamlit para parsear y analizar estados de cuenta bancarios (BBVA, Scotiabank, Banorte).

## 🚀 Inicio Rápido

### 1. Construir e iniciar la aplicación
```bash
cd /Volumes/nve/Emprendimiento/lectura_estados_cuenta
podman-compose up -d --build
```

### 2. Acceder a la aplicación

- **Local:** http://localhost:8000
- **Red local:** http://192.168.1.106:8000
- **Frontend:** http://192.168.1.106:8000/static/index.html

---

## 📋 Comandos Útiles

| Comando | Descripción |
|---------|-------------|
| `podman-compose up -d` | Iniciar en segundo plano |
| `podman-compose down` | Detener la aplicación |
| `podman-compose logs -f` | Ver logs en tiempo real |
| `podman-compose restart` | Reiniciar la aplicación |
| `podman-compose build --no-cache` | Reconstruir imagen desde cero |
| `podman ps` | Ver contenedores activos |
| `podman machine start` | Iniciar la VM de Podman |
| `podman machine stop` | Detener la VM de Podman |

---

## ⚡ Configuración de Inicio Automático en macOS (Headless/SSH)

Esta configuración permite que la app se inicie automáticamente al encender la Mac Mini, **sin necesidad de login gráfico**.

### Paso 1: Instalar servicios de inicio

Los LaunchAgents ya están configurados. Para instalarlos:

```bash
# Copiar archivos de configuración
cp /Volumes/nve/Emprendimiento/lectura_estados_cuenta/com.podman.machine.plist ~/Library/LaunchAgents/
cp /Volumes/nve/Emprendimiento/lectura_estados_cuenta/com.estados-cuenta.app.plist ~/Library/LaunchAgents/

# Cargar los servicios
launchctl load ~/Library/LaunchAgents/com.podman.machine.plist
launchctl load ~/Library/LaunchAgents/com.estados-cuenta.app.plist
```

### Paso 2: Verificar instalación

```bash
# Ver que los archivos existen
ls -la ~/Library/LaunchAgents/ | grep -E "podman|estados"

# Ver logs de inicio
cat /tmp/podman-machine.log
cat /tmp/estados-cuenta.log
```

### Paso 3: Desactivar (si es necesario)

```bash
launchctl unload ~/Library/LaunchAgents/com.podman.machine.plist
launchctl unload ~/Library/LaunchAgents/com.estados-cuenta.app.plist
```

---

## 📁 Estructura de Archivos

```
lectura_estados_cuenta/
├── Dockerfile                        # Imagen de contenedor
├── docker-compose.yml               # Configuración de servicios (compatible con Podman)
├── .dockerignore                    # Archivos a ignorar en build
├── .env                             # Variables de entorno (API keys)
├── com.podman.machine.plist         # LaunchAgent: inicia Podman VM
├── com.estados-cuenta.app.plist     # LaunchAgent: inicia la app
├── app.py                           # Aplicación principal Streamlit
├── database.py                      # Manejo de SQLite
├── parsers.py                       # Parsers de estados de cuenta
├── ai_parsers.py                    # Parsers con AI (OpenAI, Gemini)
└── static/                          # Frontend web estático
```

---

## 🔑 Variables de Entorno

Crea un archivo `.env` con las API keys necesarias:

```env
OPENAI_API_KEY=tu-clave-openai
GOOGLE_API_KEY=tu-clave-google
```

---

## 🔧 Solución de Problemas

### Podman VM no inicia
```bash
# Verificar estado
podman machine info

# Reiniciar la VM
podman machine stop
podman machine start

# Ver logs
cat /tmp/podman-machine.log
```

### La aplicación no inicia
```bash
# Ver logs del contenedor
podman logs lectura-estados-cuenta

# Ver estado del contenedor
podman ps -a

# Reconstruir imagen
podman-compose build --no-cache
podman-compose up -d
```

### Puerto 8501 ocupado
```bash
# Ver qué está usando el puerto
lsof -i :8501

# Matar proceso
kill -9 $(lsof -t -i :8501)
```

### Verificar que la app responde
```bash
curl http://localhost:8000/health
# Debería responder: {"status":"ok"}

# Desde red local
curl http://192.168.1.106:8000/health
```

---

## 🐳 ¿Por qué Podman en lugar de Docker?

- **Sin daemon**: Podman funciona sin un servicio en segundo plano
- **Headless**: Funciona perfectamente en servidores sin GUI (ideal para Mac Mini por SSH)
- **Rootless**: Mayor seguridad por defecto
- **Compatible**: Usa los mismos Dockerfiles y docker-compose.yml
