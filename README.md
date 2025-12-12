# Audio Tools

Colección de herramientas para procesamiento de audio.

## Requisitos

### Sistema
- Python 3.8+
- ffmpeg (incluye ffprobe)
- mediainfo

### Instalación de dependencias del sistema

**Ubuntu/Debian:**
```bash
sudo apt install ffmpeg mediainfo
```

**Arch Linux:**
```bash
sudo pacman -S ffmpeg mediainfo
```

**WSL (Windows Subsystem for Linux):**
```bash
# Actualizar repositorios
sudo apt update

# Instalar dependencias
sudo apt install ffmpeg mediainfo python3 python3-pip
```

### Instalación de dependencias Python

Se recomienda usar un entorno virtual para evitar conflictos con otros paquetes:

```bash
# Crear entorno virtual en la carpeta del proyecto
python3 -m venv .venv

# Activar el entorno virtual
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

> **Nota:** Cada vez que abras una nueva terminal, debes activar el entorno virtual con `source .venv/bin/activate` antes de usar las herramientas.

## Uso

```bash
python main.py <herramienta> [argumentos]
```

### Ver ayuda
```bash
python main.py --help
python main.py <herramienta> --help
```

## Herramientas Disponibles

### delay-fix

Analiza audio y aplica correcciones de delay con precisión sample-accurate para formatos PCM/FLAC.

**Uso:**
```bash
# Solo análisis
python main.py delay-fix archivo.flac

# Aplicar delay con duración objetivo
python main.py delay-fix archivo.flac 500ms 1:23:45.678
python main.py delay-fix archivo.flac -200ms 00:45:30.500
```

**Argumentos:**
| Argumento | Descripción |
|-----------|-------------|
| `archivo` | Archivo de audio a procesar |
| `delay` | Delay a aplicar (ej: `500ms`, `-200ms`, `1.5s`) |
| `duracion_objetivo` | Duración final deseada (ej: `1:23:45.678`, `3600.5`) |

**Formatos soportados:**
- FLAC, WAV, W64 → Precisión sample-accurate
- AAC, AC3, TrueHD, etc. → Precisión frame-accurate
