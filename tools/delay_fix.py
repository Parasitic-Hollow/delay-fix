#!/usr/bin/env python3
"""
Delay Fix - Herramienta para análisis de audio y aplicación de delay
Versión 1.0.0: Primera versión estable
===============================================================
DESCRIPCIÓN:
Analiza audio para extraer silencios y aplica delays precisos ajustados
a frame boundaries, permitiendo target de duración exacta.

FLUJO DE PROCESAMIENTO:
1. Empaquetado a MKA (ffmpeg)      → Contenedor con metadatos confiables
2. Extracción de metadatos (pymediainfo) → Frame duration crítico
3. Conversión a WAV (ffmpeg)       → Para análisis de silencio
4. Análisis de silencio (ffmpeg)   → Estrategia 500ms→400ms→300ms
5. Extracción de silencio (ffmpeg) → Alineado a frame boundaries
6. Aplicación delay/target (ffmpeg)→ Concatenación precisa

CARACTERÍSTICAS:
- Delay positivo: Agrega silencio al inicio
- Delay negativo: Corta inicio del audio  
- Target exacto: Ajusta duración total a valor específico
- Frame boundary alignment: Todo ajustado a múltiplos de frame duration
- Sin re-encoding: Copia directa de streams (lossless)
- Archivo final en directorio original del archivo de entrada
"""

import os
import sys
import subprocess
import re
import math
import shutil
import json
from pathlib import Path
import tempfile
from decimal import Decimal, InvalidOperation

# Importar pymediainfo
try:
    from pymediainfo import MediaInfo
    MEDIAINFO_AVAILABLE = True
except ImportError:
    MEDIAINFO_AVAILABLE = False
    print("Error: pymediainfo no está instalado.")
    print("Instala con: pip install pymediainfo")
    sys.exit(1)

def calcular_spf_preciso(tags, sample_rate):
    """
    Calcula SPF (Samples Per Frame) de manera precisa usando
    NUMBER_OF_FRAMES y DURATION de los tags
    """
    try:
        numero_frames = None
        duracion_str = None
        
        if isinstance(tags, dict):
            for key, value in tags.items():
                key_str = str(key).upper()
                if 'NUMBER_OF_FRAMES' in key_str:
                    numero_frames = str(value)
                elif key_str == 'DURATION' and not duracion_str:
                    duracion_str = str(value)
        else:
            tags_str = str(tags)
            if 'NUMBER_OF_FRAMES' in tags_str.upper():
                match = re.search(r'NUMBER_OF_FRAMES\s*[:=]?\s*(\d+)', tags_str, re.IGNORECASE)
                if match:
                    numero_frames = match.group(1)
            
            if not duracion_str and 'DURATION' in tags_str.upper():
                match = re.search(r'DURATION\s*[:=]?\s*([\d:.]+)', tags_str, re.IGNORECASE)
                if match:
                    duracion_str = match.group(1)
        
        if not numero_frames or not duracion_str or not sample_rate:
            return None
        
        partes = duracion_str.split(':')
        if len(partes) == 3:
            horas = int(partes[0])
            minutos = int(partes[1])
            segundos_ms = partes[2]
        elif len(partes) == 2:
            horas = 0
            minutos = int(partes[0])
            segundos_ms = partes[1]
        else:
            return None
        
        if '.' in segundos_ms:
            segundos, milisegundos = segundos_ms.split('.')
            segundos_total = int(segundos) + float('0.' + milisegundos)
        else:
            segundos_total = int(segundos_ms)
        
        duracion_total_segundos = horas * 3600 + minutos * 60 + segundos_total
        
        try:
            numero_frames_int = int(numero_frames)
        except ValueError:
            match = re.search(r'(\d+)', numero_frames)
            if match:
                numero_frames_int = int(match.group(1))
            else:
                return None
        
        spf = (sample_rate * duracion_total_segundos) / numero_frames_int
        return round(spf)
        
    except Exception as e:
        print(f"  Error en calcular_spf_preciso: {e}")
        return None

def formato_tiempo_amigable(segundos_str):
    """
    Convierte segundos a formato HH:MM:SS.ms manteniendo precisión
    """
    try:
        if isinstance(segundos_str, str):
            segundos_str = segundos_str.replace(' s', '').replace('seg', '').strip()
        
        total = Decimal(str(segundos_str))
        
        horas = int(total // 3600)
        minutos = int((total % 3600) // 60)
        segundos_int = int(total % 60)
        
        milisegundos_decimal = (total - int(total)) * 1000
        milisegundos = int(milisegundos_decimal)
        
        formato_hhmmss = f"{horas:02d}:{minutos:02d}:{segundos_int:02d}.{milisegundos:03d}"
        segundos_formato = f"{float(total):.3f} s"
        
        return f"{formato_hhmmss} ({segundos_formato})"
        
    except (ValueError, InvalidOperation, AttributeError):
        return str(segundos_str)

def obtener_metadatos_mediainfo(mka_file):
    """
    PASO 2: Extracción de metadatos REALES del contenedor MKA
    Herramienta: pymediainfo
    Propósito: Obtener frame duration crítico para alineación precisa
    """
    metadatos = {
        'Codec': 'N/A',
        'Canales': 'N/A',
        'Bitrate': 'N/A',
        'Sample_rate': 'N/A',
        'SPF': 'N/A',
        'Duration_sample': 'N/A',
        'Duration_frame': 'N/A',
        'Frame_duration_ms': None,
        'Frame_rate': 'N/A'
    }
    
    try:
        if not MEDIAINFO_AVAILABLE:
            print("  ERROR: pymediainfo no está disponible")
            return metadatos
        
        media_info = MediaInfo.parse(str(mka_file))
        
        audio_track = None
        for track in media_info.tracks:
            if track.track_type == 'Audio':
                audio_track = track
                break
        
        if not audio_track:
            print(f"  No se encontró track de audio en {mka_file}")
            return metadatos
        
        metadatos['Codec'] = getattr(audio_track, 'codec_id', getattr(audio_track, 'format', 'N/A'))
        metadatos['Canales'] = getattr(audio_track, 'channel_s', getattr(audio_track, 'channels', 'N/A'))
        
        bitrate = getattr(audio_track, 'bit_rate', None)
        if bitrate:
            try:
                bitrate_val = int(str(bitrate).replace(' ', ''))
                metadatos['Bitrate'] = f"{bitrate_val / 1000:.0f} kbps"
            except:
                metadatos['Bitrate'] = str(bitrate)
        
        sample_rate = getattr(audio_track, 'sampling_rate', None)
        sample_rate_val = None
        if sample_rate:
            try:
                sample_rate_val = float(str(sample_rate).replace(' ', ''))
                metadatos['Sample_rate'] = f"{sample_rate_val:.0f} Hz"
            except:
                metadatos['Sample_rate'] = str(sample_rate)
        
        frame_rate = getattr(audio_track, 'frame_rate', None)
        if frame_rate:
            metadatos['Frame_rate'] = str(frame_rate)
        
        duration = getattr(audio_track, 'duration', None)
        if duration:
            try:
                segundos = float(str(duration).replace(' ', '')) / 1000.0
                metadatos['Duration_sample'] = formato_tiempo_amigable(segundos)
            except:
                metadatos['Duration_sample'] = str(duration)
        
        spf = None
        frame_duration_ms = None
        
        tags = {}
        try:
            track_data = audio_track.to_data()
            if isinstance(track_data, dict) and 'tag' in track_data:
                tags = track_data['tag']
            elif hasattr(audio_track, 'extra'):
                tags = getattr(audio_track, 'extra', {})
        except:
            pass
        
        if tags and sample_rate_val:
            spf_calculado = calcular_spf_preciso(tags, sample_rate_val)
            if spf_calculado:
                spf = spf_calculado
                print(f"  SPF calculado desde NUMBER_OF_FRAMES/DURATION: {spf}")
        
        if not spf:
            for attr_name in dir(audio_track):
                if not attr_name.startswith('_'):
                    attr_value = getattr(audio_track, attr_name)
                    if attr_value and isinstance(attr_value, str):
                        attr_lower = attr_name.lower()
                        if any(keyword in attr_lower for keyword in ['sample_per_frame', 'samples_per_frame', 'spf']):
                            try:
                                spf = int(str(attr_value).replace(' ', ''))
                                break
                            except:
                                pass
            
            if not spf and tags:
                if isinstance(tags, dict):
                    for key, value in tags.items():
                        key_lower = str(key).lower()
                        if any(keyword in key_lower for keyword in ['sample_per_frame', 'samples_per_frame', 'spf']):
                            try:
                                spf = int(str(value).replace(' ', ''))
                                break
                            except:
                                pass
                else:
                    tags_str = str(tags)
                    match = re.search(r'(?:sample_per_frame|samples_per_frame|spf)\s*[:=]?\s*(\d+)', 
                                     tags_str, re.IGNORECASE)
                    if match:
                        try:
                            spf = int(match.group(1))
                        except:
                            pass
        
        if not spf and frame_rate and sample_rate_val:
            try:
                frame_rate_str = str(frame_rate).replace(' ', '')
                if '/' in frame_rate_str:
                    num, den = map(int, frame_rate_str.split('/'))
                    if den > 0:
                        fps = num / den
                else:
                    fps = float(frame_rate_str)
                
                spf = int(sample_rate_val / fps)
                print(f"  SPF calculado de frame rate: {sample_rate_val} / {fps} = {spf}")
            except Exception as e:
                print(f"  Error calculando SPF de frame rate: {e}")
        
        if not spf:
            for attr_name in ['samples_per_frame', 'sample_per_frame', 'spf', 'nb_samples']:
                attr_value = getattr(audio_track, attr_name, None)
                if attr_value:
                    try:
                        spf = int(str(attr_value).replace(' ', ''))
                        break
                    except:
                        pass
        
        if not spf and metadatos['Codec'] and any(codec in str(metadatos['Codec']).lower() 
                                                 for codec in ['truehd', 'mlp']):
            print(f"  Analizando Dolby TrueHD/MLP...")
            if sample_rate_val == 48000:
                spf = 40
                print(f"  SPF asumido para TrueHD @ 48kHz: {spf}")
        
        if spf and sample_rate_val:
            frame_duration_ms = (spf / sample_rate_val) * 1000
            metadatos['SPF'] = str(spf)
            metadatos['Frame_duration_ms'] = frame_duration_ms
            metadatos['Duration_frame'] = f"{frame_duration_ms:.6f} ms"
            
            print(f"  Cálculo final: ({spf} / {sample_rate_val}) × 1000 = {frame_duration_ms:.6f} ms")
            
        elif sample_rate_val:
            print(f"\n  ERROR CRÍTICO: No se pudo determinar SPF")
            print(f"  Frame duration es REQUERIDO para el proceso")
            print(f"  Información disponible del archivo:")
            print(f"    Codec: {metadatos['Codec']}")
            print(f"    Sample rate: {sample_rate_val} Hz")
            print(f"    Frame rate: {frame_rate if frame_rate else 'N/A'}")
            print(f"    Canales: {metadatos['Canales']}")
        
        return metadatos
        
    except Exception as e:
        print(f"Error en obtener_metadatos_mediainfo: {e}")
        import traceback
        traceback.print_exc()
        return metadatos

def mostrar_metadatos(metadatos, nombre_archivo):
    """Muestra los metadatos de forma formateada"""
    print(f"\n{'='*60}")
    print(f"METADATOS CONFIABLES (desde contenedor MKA): {nombre_archivo}")
    print(f"{'='*60}")
    for clave, valor in metadatos.items():
        if clave != 'Frame_duration_ms':
            print(f"{clave:<18}: {valor}")
    print(f"{'='*60}")

def crear_mka_con_ffmpeg(input_file, output_mka):
    """
    PASO 1: Crear archivo MKA usando ffmpeg
    Herramienta: ffmpeg
    Propósito: Crear contenedor con metadatos confiables para análisis
    Método: Copia directa sin re-encoding (lossless)
    """
    print(f"  Creando MKA con ffmpeg...")
    
    cmd = [
        "ffmpeg",
        "-i", str(input_file),
        "-c", "copy",
        "-map", "0:a:0",
        "-y",
        "-loglevel", "error",
        str(output_mka)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error en ffmpeg: {result.stderr[:200]}")
            return False
        
        if output_mka.exists() and output_mka.stat().st_size > 0:
            print(f"  MKA creado: {output_mka.name}")
            print(f"  Tamaño: {output_mka.stat().st_size:,} bytes")
            return True
        else:
            print(f"Error: Archivo MKA no se creó correctamente")
            return False
            
    except FileNotFoundError:
        print(f"Error: ffmpeg no encontrado")
        return False
    except Exception as e:
        print(f"Error creando MKA: {e}")
        return False

def convertir_mka_a_wav(mka_path, temp_dir):
    """
    PASO 3: Convertir archivo MKA a WAV para análisis de silencio
    Herramienta: ffmpeg
    Propósito: Crear formato PCM sin compresión para detección precisa de silencio
    Parámetros: 16-bit PCM, mono, sin compresión DRC
    """
    wav_name = mka_path.stem + ".wav"
    wav_path = temp_dir / wav_name
    
    print(f"Paso 3/5: Convirtiendo MKA a WAV (16-bit mono, sin compresión)...")
    
    cmd = [
        "ffmpeg",
        "-drc_scale", "0",
        "-i", str(mka_path),
        "-c:a", "pcm_s16le",
        "-f", "wav",
        "-rf64", "always",
        "-ac", "1",
        "-y",
        "-loglevel", "error",
        str(wav_path)
    ]
    
    print(f"  Parámetros: 16-bit PCM, mono, sin compresión DRC")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Error en ffmpeg: {result.stderr[:200]}")
            return None
        
        if wav_path.exists() and wav_path.stat().st_size > 0:
            print(f"WAV creado: {wav_path.name}")
            print(f"  Tamaño: {wav_path.stat().st_size:,} bytes")
            return wav_path
        else:
            print(f"Error: Archivo WAV no se creó correctamente")
            return None
            
    except FileNotFoundError:
        print(f"Error: ffmpeg no encontrado")
        return None
    except Exception as e:
        print(f"Error en conversión: {e}")
        return None

def calcular_duracion_ajustada(duracion_deseada_ms, frame_duration_ms, mostrar_ajuste=True):
    """
    Calcula la duración ajustada al múltiplo más cercano del frame duration
    """
    if frame_duration_ms <= 0:
        return duracion_deseada_ms
    
    frames_exactos = duracion_deseada_ms / frame_duration_ms
    
    frames_abajo = math.floor(frames_exactos)
    frames_arriba = math.ceil(frames_exactos)
    
    if abs(frames_exactos - frames_abajo) < 0.000001:
        frames_elegidos = frames_abajo
    elif abs(frames_exactos - frames_arriba) < 0.000001:
        frames_elegidos = frames_arriba
    else:
        duracion_abajo = frames_abajo * frame_duration_ms
        duracion_arriba = frames_arriba * frame_duration_ms
        
        diff_abajo = abs(duracion_deseada_ms - duracion_abajo)
        diff_arriba = abs(duracion_deseada_ms - duracion_arriba)
        
        if diff_abajo <= diff_arriba:
            frames_elegidos = frames_abajo
            duracion_ajustada_ms = duracion_abajo
        else:
            frames_elegidos = frames_arriba
            duracion_ajustada_ms = duracion_arriba
    
    duracion_ajustada_ms = frames_elegidos * frame_duration_ms
    
    duracion_ajustada_ms = max(duracion_ajustada_ms, frame_duration_ms * 3)
    
    if mostrar_ajuste and abs(duracion_deseada_ms - duracion_ajustada_ms) > 0.1:
        print(f"  Ajuste frame boundary:")
        print(f"    Deseado: {duracion_deseada_ms:.2f}ms ({frames_exactos:.3f} frames)")
        print(f"    Ajustado: {duracion_ajustada_ms:.2f}ms ({frames_elegidos} frames)")
        print(f"    Diferencia: {duracion_ajustada_ms - duracion_deseada_ms:+.2f}ms")
    
    return duracion_ajustada_ms

def ajustar_timecode_a_boundary(timecode_s, frame_duration_ms):
    """
    Ajusta timecode al frame boundary más cercano
    """
    if frame_duration_ms <= 0:
        return timecode_s
    
    frame_duration_s = frame_duration_ms / 1000.0
    frames_exactos = timecode_s / frame_duration_s
    
    frames_abajo = math.floor(frames_exactos)
    frames_arriba = math.ceil(frames_exactos)
    
    timecode_abajo = frames_abajo * frame_duration_s
    timecode_arriba = frames_arriba * frame_duration_s
    
    if abs(timecode_s - timecode_abajo) <= abs(timecode_s - timecode_arriba):
        return timecode_abajo
    else:
        return timecode_arriba

def parsear_salida_silencedetect(salida_ffmpeg, umbral_db):
    """
    Parsea la salida de ffmpeg silencedetect
    """
    patron_start = re.compile(r'\[silencedetect[^\]]*\]\s+silence_start:\s*(\d+\.?\d*)')
    patron_end = re.compile(r'\[silencedetect[^\]]*\]\s+silence_end:\s*(\d+\.?\d*)\s*\|\s*silence_duration:\s*(\d+\.?\d*)')
    
    segmentos = []
    inicio_pendiente = None
    
    for linea in salida_ffmpeg.split('\n'):
        match_start = patron_start.search(linea)
        if match_start:
            inicio_pendiente = float(match_start.group(1))
            continue
        
        match_end = patron_end.search(linea)
        if match_end and inicio_pendiente is not None:
            fin = float(match_end.group(1))
            duracion = float(match_end.group(2))
            
            segmentos.append({
                'inicio': inicio_pendiente,
                'fin': fin,
                'duracion': duracion,
                'umbral': umbral_db
            })
            
            inicio_pendiente = None
    
    if segmentos:
        print(f"  Encontrados {len(segmentos)} segmentos con {umbral_db}dB")
    else:
        print(f"  Sin segmentos con {umbral_db}dB")
    
    return segmentos

def detectar_silencio_ffmpeg(archivo_wav, umbral_db, duracion_minima_segundos):
    """
    Detecta segmentos de silencio usando ffmpeg silencedetect
    """
    comando = [
        'ffmpeg', '-i', str(archivo_wav),
        '-af', f'silencedetect=noise={umbral_db}dB:d={duracion_minima_segundos:.3f}',
        '-f', 'null', '-'
    ]
    
    try:
        resultado = subprocess.run(comando, capture_output=True, text=True, check=True)
        return parsear_salida_silencedetect(resultado.stderr, umbral_db)
    except subprocess.CalledProcessError as e:
        print(f"  Error en ffmpeg ({umbral_db}dB): {e.stderr[:200]}")
        return None

def buscar_silencio_estrategia_escalonada(archivo_wav, frame_duration_ms):
    """
    PASO 4: Busca segmentos de silencio usando estrategia 500ms → 400ms → 300ms
    Herramienta: ffmpeg silencedetect
    Propósito: Encontrar segmento de silencio adecuado para extraer
    Estrategia: Fase 1 (umbrales sensibles) → Fase 2 (umbrales menos sensibles)
    """
    print("\n" + "="*60)
    print("ANÁLISIS DE SILENCIOS (Estrategia 500ms → 400ms → 300ms)")
    print("="*60)
    
    umbrales_fase1 = [-90, -80, -70]
    umbrales_fase2 = [-60, -50]
    duraciones_ms = [500, 400, 300]
    
    print(f"\nConfiguración de búsqueda:")
    print(f"  Frame duration: {frame_duration_ms:.4f} ms")
    print(f"  Fase 1 (umbrales sensibles): {', '.join(map(str, umbrales_fase1))} dB")
    print(f"  Fase 2 (umbrales menos sensibles): {', '.join(map(str, umbrales_fase2))} dB")
    
    print(f"\n--- FASE 1: Buscando con umbrales sensibles ---")
    for duracion_obj in duraciones_ms:
        mostrar_ajuste = (duracion_obj == duraciones_ms[0])
        duracion_ajustada_ms = calcular_duracion_ajustada(
            duracion_obj, frame_duration_ms, mostrar_ajuste=mostrar_ajuste
        )
        duracion_ajustada_s = duracion_ajustada_ms / 1000.0
        
        print(f"\nProbando {duracion_obj}ms (ajustado a frames: {duracion_ajustada_ms:.2f}ms):")
        
        for umbral in umbrales_fase1:
            segmentos = detectar_silencio_ffmpeg(archivo_wav, umbral, duracion_ajustada_s)
            if segmentos:
                primer_segmento = segmentos[0]
                print(f"    Silencio encontrado: {primer_segmento['duracion']:.3f}s "
                      f"a {primer_segmento['umbral']}dB "
                      f"(inicio: {primer_segmento['inicio']:.2f}s)")
                return primer_segmento, duracion_obj, duracion_ajustada_ms, umbral
    
    print(f"\n--- FASE 2: Buscando con umbrales menos sensibles ---")
    for duracion_obj in duraciones_ms:
        duracion_ajustada_ms = calcular_duracion_ajustada(
            duracion_obj, frame_duration_ms, mostrar_ajuste=False
        )
        duracion_ajustada_s = duracion_ajustada_ms / 1000.0
        
        print(f"\nProbando {duracion_obj}ms (ajustado a frames: {duracion_ajustada_ms:.2f}ms):")
        
        for umbral in umbrales_fase2:
            segmentos = detectar_silencio_ffmpeg(archivo_wav, umbral, duracion_ajustada_s)
            if segmentos:
                primer_segmento = segmentos[0]
                print(f"    Silencio encontrado: {primer_segmento['duracion']:.3f}s "
                      f"a {primer_segmento['umbral']}dB "
                      f"(inicio: {primer_segmento['inicio']:.2f}s)")
                return primer_segmento, duracion_obj, duracion_ajustada_ms, umbral
    
    print("\nNo se encontraron segmentos de silencio en ningún umbral/duración")
    return None, None, None, None

def analizar_silencios_wav(wav_path, frame_duration_ms):
    """
    Analiza silencios en archivo WAV y genera timecodes ajustados
    """
    print(f"\nPaso 4/5: Analizando silencios en WAV...")
    
    segmento, duracion_objetivo_ms, duracion_objetivo_ajustada_ms, umbral_encontrado = buscar_silencio_estrategia_escalonada(wav_path, frame_duration_ms)
    
    if not segmento:
        return None
    
    inicio_original = segmento['inicio']
    fin_original = segmento['fin']
    
    inicio_ajustado = ajustar_timecode_a_boundary(inicio_original, frame_duration_ms)
    fin_ajustado = ajustar_timecode_a_boundary(fin_original, frame_duration_ms)
    
    duracion_ajustada_s = fin_ajustado - inicio_ajustado
    
    resultado = {
        'duracion_objetivo_ms': duracion_objetivo_ms,
        'duracion_objetivo_ajustada_ms': duracion_objetivo_ajustada_ms,
        'umbral_busqueda_db': umbral_encontrado,
        'frame_duration_ms': frame_duration_ms,
        
        'inicio_original_s': inicio_original,
        'fin_original_s': fin_original,
        'duracion_original_s': segmento['duracion'],
        'duracion_original_ms': segmento['duracion'] * 1000,
        'umbral_detectado_db': segmento['umbral'],
        
        'inicio_ajustado_s': inicio_ajustado,
        'fin_ajustado_s': fin_ajustado,
        'duracion_ajustada_s': duracion_ajustada_s,
        'duracion_ajustada_ms': duracion_ajustada_s * 1000,
        
        'diferencia_inicio_ms': (inicio_ajustado - inicio_original) * 1000,
        'diferencia_fin_ms': (fin_ajustado - fin_original) * 1000,
        'diferencia_duracion_ms': (duracion_ajustada_s - segmento['duracion']) * 1000,
        
        'frames_inicio_original': inicio_original / (frame_duration_ms / 1000),
        'frames_fin_original': fin_original / (frame_duration_ms / 1000),
        'frames_inicio_ajustado': inicio_ajustado / (frame_duration_ms / 1000),
        'frames_fin_ajustado': fin_ajustado / (frame_duration_ms / 1000),
    }
    
    return resultado

def mostrar_resultado_silencios(resultado, nombre_archivo):
    """
    Muestra los resultados del análisis de silencios
    """
    if not resultado:
        print(f"\nNo se encontraron silencios adecuados en {nombre_archivo}")
        return
    
    print(f"\n{'='*60}")
    print(f"RESULTADOS ANÁLISIS DE SILENCIOS: {nombre_archivo}")
    print(f"{'='*60}")
    
    print(f"\nCONFIGURACIÓN DE BÚSQUEDA:")
    print(f"  Duración solicitada: {resultado['duracion_objetivo_ms']} ms")
    print(f"  Ajustada a frames: {resultado['duracion_objetivo_ajustada_ms']:.2f} ms")
    print(f"  Umbral de búsqueda: {resultado['umbral_busqueda_db']} dB")
    print(f"  Frame duration: {resultado['frame_duration_ms']:.4f} ms")
    
    print(f"\nDETECCIÓN ORIGINAL (por ffmpeg):")
    print(f"  Umbral detectado: {resultado['umbral_detectado_db']} dB")
    print(f"  Inicio: {resultado['inicio_original_s']:.6f} s")
    print(f"  Fin:    {resultado['fin_original_s']:.6f} s")
    print(f"  Duración: {resultado['duracion_original_s']:.6f} s ({resultado['duracion_original_ms']:.2f} ms)")
    
    print(f"\nAJUSTE A FRAME BOUNDARIES:")
    print(f"  Inicio ajustado: {resultado['inicio_ajustado_s']:.6f} s")
    print(f"  Fin ajustado:    {resultado['fin_ajustado_s']:.6f} s")
    print(f"  Duración ajustada: {resultado['duracion_ajustada_s']:.6f} s ({resultado['duracion_ajustada_ms']:.2f} ms)")
    
    print(f"\nDIFERENCIAS POR AJUSTE:")
    print(f"  Inicio: {resultado['diferencia_inicio_ms']:+.3f} ms")
    print(f"  Fin:    {resultado['diferencia_fin_ms']:+.3f} ms")
    print(f"  Duración: {resultado['diferencia_duracion_ms']:+.3f} ms")
    
    frames_originales = resultado['duracion_original_ms'] / resultado['frame_duration_ms']
    frames_ajustados = resultado['duracion_ajustada_ms'] / resultado['frame_duration_ms']
    
    print(f"\nINFORMACIÓN DE FRAMES:")
    print(f"  Frames originales: {frames_originales:.2f}")
    print(f"  Frames ajustados: {frames_ajustados:.2f}")
    print(f"  Frames inicio ajustado: {resultado['frames_inicio_ajustado']:.2f}")
    print(f"  Frames fin ajustado: {resultado['frames_fin_ajustado']:.2f}")
    
    inicio_formato = formato_tiempo_amigable(resultado['inicio_ajustado_s'])
    fin_formato = formato_tiempo_amigable(resultado['fin_ajustado_s'])
    duracion_formato = formato_tiempo_amigable(resultado['duracion_ajustada_s'])
    
    print(f"\nFORMATO AMIGABLE:")
    print(f"  Inicio: {inicio_formato}")
    print(f"  Fin:    {fin_formato}")
    print(f"  Duración: {duracion_formato}")
    
    print(f"\nPARÁMETROS PARA USO FUTURO:")
    print(f"  frame_duration_ms = {resultado['frame_duration_ms']:.6f}")
    print(f"  inicio_ajustado_s = {resultado['inicio_ajustado_s']:.6f}")
    print(f"  fin_ajustado_s = {resultado['fin_ajustado_s']:.6f}")
    print(f"  duracion_ajustada_s = {resultado['duracion_ajustada_s']:.6f}")
    
    print(f"{'='*60}")

def segundos_a_formato_ffmpeg(segundos):
    """
    Convierte segundos a formato HH:MM:SS.mmm para ffmpeg
    """
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = segundos % 60
    
    return f"{horas:02d}:{minutos:02d}:{segs:06.3f}"

def extraer_segmento_ffmpeg(mka_path, inicio_s, fin_s, output_path):
    """
    Extrae segmento usando ffmpeg -ss inicio -t duracion -c copy
    NOTA: ffmpeg con -y sobrescribe automáticamente si el archivo existe
    """
    duracion_s = fin_s - inicio_s
    
    inicio_formato = segundos_a_formato_ffmpeg(inicio_s)
    
    cmd = [
        "ffmpeg",
        "-i", str(mka_path),
        "-ss", inicio_formato,
        "-t", f"{duracion_s:.6f}",
        "-c", "copy",
        "-map", "0",
        "-y",
        "-loglevel", "error",
        str(output_path)
    ]
    
    print(f"  Comando ffmpeg: -ss {inicio_s:.3f}s -t {duracion_s:.3f}s -c copy")
    print(f"  Extracción: {inicio_s:.3f}s a {fin_s:.3f}s ({duracion_s:.3f}s)")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path
            else:
                print(f"  Error: Archivo no se creó o está vacío")
                return None
        else:
            print(f"  Error ffmpeg (código {result.returncode}):")
            if result.stderr:
                print(f"    {result.stderr[:200]}")
            return None
            
    except Exception as e:
        print(f"  Error ejecutando ffmpeg: {e}")
        return None

def extraer_silencio_del_mka(mka_path, resultado_silencios, temp_dir):
    """
    PASO 5: Extrae el segmento de silencio del MKA usando ffmpeg
    Herramienta: ffmpeg
    Propósito: Extraer silencio identificado para usarlo en delays positivos
    Característica: Timecodes exactos alineados a frame boundaries
    """
    print(f"\nPaso 5/5: Extrayendo segmento de silencio del MKA...")
    
    if not resultado_silencios:
        print("  No hay resultados de silencio para extraer")
        return None
    
    inicio = resultado_silencios['inicio_ajustado_s']
    fin = resultado_silencios['fin_ajustado_s']
    duracion_s = fin - inicio
    frame_duration_ms = resultado_silencios['frame_duration_ms']
    frame_duration_s = frame_duration_ms / 1000.0
    
    frames_exactos = duracion_s / frame_duration_s
    frames_enteros = round(frames_exactos)
    
    if abs(frames_exactos - frames_enteros) > 0.000001:
        duracion_ajustada_s = frames_enteros * frame_duration_s
        fin = inicio + duracion_ajustada_s
        print(f"  Ajuste de duración a frame exacto:")
        print(f"    Original: {duracion_s:.6f}s ({frames_exactos:.3f} frames)")
        print(f"    Ajustado: {duracion_ajustada_s:.6f}s ({frames_enteros} frames)")
        print(f"    Diferencia: {(duracion_ajustada_s - duracion_s)*1000:+.3f} ms")
        duracion_s = duracion_ajustada_s
    
    duracion_ms = duracion_s * 1000
    
    nombre_base = Path(mka_path).stem
    for sufijo in ["_analisis", "_temp", "_processed"]:
        if nombre_base.endswith(sufijo):
            nombre_base = nombre_base[:-len(sufijo)]
    
    silencio_mka = temp_dir / f"{nombre_base}_silencio.mka"
    
    print(f"  Timecodes exactos en frames: {inicio:.3f}s - {fin:.3f}s")
    print(f"  Duración exacta: {duracion_s:.6f}s ({duracion_ms:.3f} ms)")
    print(f"  Frames: {frames_enteros} × {frame_duration_ms:.4f}ms")
    print(f"  Archivo: {silencio_mka.name}")
    
    archivo_silencio = extraer_segmento_ffmpeg(mka_path, inicio, fin, silencio_mka)
    
    if archivo_silencio and archivo_silencio.exists():
        tamaño = archivo_silencio.stat().st_size
        
        try:
            media_info = MediaInfo.parse(str(archivo_silencio))
            for track in media_info.tracks:
                if track.track_type == 'Audio' and hasattr(track, 'duration'):
                    duracion_real_ms = float(str(track.duration).replace(' ', ''))
                    duracion_real_s = duracion_real_ms / 1000.0
                    print(f"  Silencio extraído: {tamaño:,} bytes")
                    print(f"  Duración real verificada: {duracion_real_s:.6f}s ({duracion_real_ms:.3f} ms)")
                    
                    resultado_silencios['duracion_ajustada_s'] = duracion_real_s
                    resultado_silencios['duracion_ajustada_ms'] = duracion_real_ms
                    resultado_silencios['fin_ajustado_s'] = inicio + duracion_real_s
                    
                    return archivo_silencio
        except:
            pass
        
        print(f"  Silencio extraído: {tamaño:,} bytes")
        return archivo_silencio
    
    print("  No se pudo extraer el segmento de silencio")
    return None

def parsear_delay(delay_str):
    """
    Parsea el valor de delay con flexibilidad de formato
    Retorna delay en milisegundos (float), positivo o negativo
    """
    if not delay_str:
        return None
    
    delay_str = delay_str.strip().lower()
    
    es_negativo = delay_str.startswith('-')
    if es_negativo:
        delay_str = delay_str[1:]
    
    delay_str = delay_str.replace(' ', '')
    
    if delay_str.endswith('ms'):
        try:
            valor = float(delay_str[:-2])
            return -valor if es_negativo else valor
        except ValueError:
            return None
    elif delay_str.endswith('s'):
        try:
            segundos = float(delay_str[:-1])
            valor = segundos * 1000.0
            return -valor if es_negativo else valor
        except ValueError:
            return None
    else:
        try:
            valor = float(delay_str)
            if valor < 100 and '.' in delay_str:
                valor = valor * 1000.0
            return -valor if es_negativo else valor
        except ValueError:
            return None

def parsear_target(target_str):
    """
    Parsea el valor de target con formatos flexibles
    Retorna target en segundos (float)
    """
    if not target_str:
        return None
    
    target_str = target_str.strip().replace(' ', '')
    
    if ':' in target_str:
        partes = target_str.split(':')
        
        if len(partes) == 3:
            horas = float(partes[0])
            minutos = float(partes[1])
            
            seg_parts = partes[2].split('.')
            segundos = float(seg_parts[0])
            if len(seg_parts) > 1:
                segundos += float('0.' + seg_parts[1])
            
            total_segundos = horas * 3600 + minutos * 60 + segundos
            
        elif len(partes) == 2:
            minutos = float(partes[0])
            
            seg_parts = partes[1].split('.')
            segundos = float(seg_parts[0])
            if len(seg_parts) > 1:
                segundos += float('0.' + seg_parts[1])
            
            total_segundos = minutos * 60 + segundos
            
        else:
            return None
    
    else:
        try:
            total_segundos = float(target_str)
            
            if target_str.startswith('.'):
                pass
            
        except ValueError:
            return None
    
    return total_segundos

def ajustar_delay_a_frames(delay_ms, frame_duration_ms):
    """
    Ajusta el delay a frame boundaries (floor/ceiling)
    Retorna: (delay_ajustado_ms, frames_elegidos, frames_exactos)
    """
    if frame_duration_ms <= 0:
        return delay_ms, delay_ms / frame_duration_ms, delay_ms / frame_duration_ms
    
    frames_exactos = delay_ms / frame_duration_ms
    
    if delay_ms < 0:
        frames_abajo = math.floor(frames_exactos)
        frames_arriba = math.ceil(frames_exactos)
    else:
        frames_abajo = math.floor(frames_exactos)
        frames_arriba = math.ceil(frames_exactos)
    
    if abs(frames_exactos - frames_abajo) < 0.000001:
        delay_ajustado_ms = frames_abajo * frame_duration_ms
        frames_elegidos = frames_abajo
        return delay_ajustado_ms, frames_elegidos, frames_exactos
    
    delay_abajo_ms = frames_abajo * frame_duration_ms
    delay_arriba_ms = frames_arriba * frame_duration_ms
    
    diff_abajo = abs(delay_ms - delay_abajo_ms)
    diff_arriba = abs(delay_ms - delay_arriba_ms)
    
    if diff_abajo <= diff_arriba:
        delay_ajustado_ms = delay_abajo_ms
        frames_elegidos = frames_abajo
    else:
        delay_ajustado_ms = delay_arriba_ms
        frames_elegidos = frames_arriba
    
    return delay_ajustado_ms, frames_elegidos, frames_exactos

def calcular_duracion_exacta_mka(mka_path, frame_duration_ms):
    """
    Calcula la duración EXACTA (en frames) de un archivo MKA
    Usa pymediainfo para obtener duración real, luego ajusta a frame boundaries
    """
    metadatos = obtener_metadatos_mediainfo(mka_path)
    if metadatos['Duration_sample'] == 'N/A':
        return None, None
    
    try:
        match = re.search(r'\((\d+\.?\d*)\s*s\)', metadatos['Duration_sample'])
        if match:
            duracion_s = float(match.group(1))
        else:
            duracion_s = float(metadatos['Duration_sample'])
        
        duracion_ms = duracion_s * 1000
        frames_exactos = duracion_ms / frame_duration_ms
        frames_enteros = round(frames_exactos)
        
        duracion_exacta_ms = frames_enteros * frame_duration_ms
        return duracion_exacta_ms, frames_enteros
        
    except Exception as e:
        print(f"  Error calculando duración exacta: {e}")
        return None, None

def crear_segmentos_delay(silencio_base_path, delay_ms, frame_duration_ms, duracion_silencio_ms, temp_dir, sufijo="_delay"):
    """
    Crea segmentos de silencio para aplicar delay positivo
    NOTA: Siempre calcula duración EXACTA del archivo base, nunca asume valores
    """
    if not silencio_base_path.exists():
        print(f"  Error: Archivo base de silencio no encontrado: {silencio_base_path}")
        return None, None
    
    if delay_ms <= 0:
        print(f"  Error: Esta función solo maneja delays positivos")
        return None, None
    
    print(f"\n  Creando segmentos para {delay_ms:.2f} ms (sufijo: {sufijo}):")
    print(f"    Frame duration: {frame_duration_ms:.4f} ms")
    
    duracion_exacta_ms, frames_base = calcular_duracion_exacta_mka(silencio_base_path, frame_duration_ms)
    
    if duracion_exacta_ms is None:
        print(f"  Usando duración del análisis: {duracion_silencio_ms:.3f} ms")
        duracion_silencio_ms_ajustada = calcular_duracion_ajustada(
            duracion_silencio_ms, frame_duration_ms, mostrar_ajuste=False
        )
        frames_base = round(duracion_silencio_ms_ajustada / frame_duration_ms)
    else:
        duracion_silencio_ms_ajustada = duracion_exacta_ms
        print(f"    Duración exacta del silencio base: {duracion_silencio_ms_ajustada:.6f} ms")
        print(f"    Frames del silencio base: {frames_base}")
    
    if duracion_silencio_ms_ajustada <= 0:
        print(f"  Error: Duración del silencio base es 0 o negativa")
        return None, None
    
    delay_ajustado_ms, frames_elegidos, frames_exactos = ajustar_delay_a_frames(delay_ms, frame_duration_ms)
    
    print(f"\n  1. Ajuste delay a frame boundaries:")
    print(f"     Solicitado: {delay_ms:.2f} ms ({frames_exactos:.3f} frames)")
    print(f"     Opción floor: {math.floor(frames_exactos)} frames = {math.floor(frames_exactos) * frame_duration_ms:.2f} ms")
    print(f"     Opción ceiling: {math.ceil(frames_exactos)} frames = {math.ceil(frames_exactos) * frame_duration_ms:.2f} ms")
    print(f"     Ajustado: {delay_ajustado_ms:.2f} ms ({frames_elegidos} frames)")
    print(f"     Diferencia: {delay_ajustado_ms - delay_ms:+.2f} ms")
    
    repeticiones_completas = int(delay_ajustado_ms // duracion_silencio_ms_ajustada)
    resto_ms = delay_ajustado_ms % duracion_silencio_ms_ajustada
    
    print(f"\n  2. Cálculo de segmentos:")
    print(f"     Delay ajustado: {delay_ajustado_ms:.2f} ms")
    print(f"     Duración segmento base: {duracion_silencio_ms_ajustada:.2f} ms")
    print(f"     Segmentos completos necesarios: {repeticiones_completas}")
    if resto_ms > 0:
        print(f"     Segmento parcial necesario: {resto_ms:.2f} ms")
    
    segmentos_creados = []
    nombre_base = silencio_base_path.stem
    if nombre_base.endswith("_silencio"):
        nombre_base = nombre_base[:-9]
    
    print(f"\n  3. Creando archivos de segmentos:")
    
    for i in range(repeticiones_completas):
        segmento_num = i + 1
        nombre_segmento = f"{nombre_base}_silencio{sufijo}_{segmento_num}.mka"
        path_segmento = temp_dir / nombre_segmento
        
        try:
            shutil.copy2(silencio_base_path, path_segmento)
            print(f"     {nombre_segmento} (completo, {duracion_silencio_ms_ajustada:.2f} ms)")
            segmentos_creados.append(path_segmento)
        except Exception as e:
            print(f"     Error copiando segmento {segmento_num}: {e}")
            return None, None
    
    if resto_ms > 0:
        resto_ajustado_ms = calcular_duracion_ajustada(resto_ms, frame_duration_ms, mostrar_ajuste=False)
        
        if resto_ajustado_ms > 0:
            segmento_num = repeticiones_completas + 1
            nombre_segmento = f"{nombre_base}_silencio{sufijo}_{segmento_num}.mka"
            path_segmento = temp_dir / nombre_segmento
            
            inicio_s = 0.0
            fin_s = resto_ajustado_ms / 1000.0
            
            archivo_parcial = extraer_segmento_ffmpeg(silencio_base_path, inicio_s, fin_s, path_segmento)
            
            if archivo_parcial and archivo_parcial.exists():
                print(f"     {nombre_segmento} (parcial, {resto_ajustado_ms:.2f} ms)")
                segmentos_creados.append(archivo_parcial)
            else:
                print(f"     Error: No se pudo crear segmento parcial de {resto_ajustado_ms:.2f} ms")
    
    if not segmentos_creados:
        print(f"  Error: No se crearon segmentos")
        return None, None
    
    print(f"\n  4. Resumen de segmentos creados:")
    total_segundos = 0
    for i, segmento in enumerate(segmentos_creados, 1):
        if segmento.exists():
            tamaño = segmento.stat().st_size
            duracion_seg_ms, _ = calcular_duracion_exacta_mka(segmento, frame_duration_ms)
            if duracion_seg_ms:
                total_segundos += duracion_seg_ms / 1000
                print(f"     Segmento {i}: {segmento.name} ({tamaño:,} bytes, {duracion_seg_ms:.2f} ms)")
            else:
                print(f"     Segmento {i}: {segmento.name} ({tamaño:,} bytes)")
        else:
            print(f"     Segmento {i}: {segmento.name} (ERROR: no existe)")
    
    suma_total_ms = total_segundos * 1000 if total_segundos > 0 else delay_ajustado_ms
    diferencia_ms = suma_total_ms - delay_ajustado_ms
    print(f"\n  5. Verificación:")
    print(f"     Delay ajustado solicitado: {delay_ajustado_ms:.2f} ms")
    print(f"     Suma de segmentos creados: {suma_total_ms:.2f} ms")
    print(f"     Diferencia: {diferencia_ms:+.2f} ms")
    
    if abs(diferencia_ms) > 0.1:
        print(f"     ADVERTENCIA: Diferencia significativa en la suma de segmentos")
    
    return segmentos_creados, delay_ajustado_ms

def crear_audio_con_delay(mka_path, inicio_corte_s, temp_dir, nombre_sufijo="_delay"):
    """
    Crea un archivo de audio con delay aplicado (para delay negativo)
    NOTA: ffmpeg con -y sobrescribe automáticamente si el archivo existe
    """
    if not mka_path.exists():
        print(f"  Error: Archivo MKA no encontrado: {mka_path}")
        return None
    
    nombre_base = mka_path.stem
    if nombre_base.endswith("_temp") or nombre_base.endswith("_analisis"):
        nombre_base = nombre_base[:-5]
    
    # Limpiar múltiples sufijos
    for sufijo in ["_temp", "_analisis", "_delay", "_target"]:
        if nombre_base.endswith(sufijo):
            nombre_base = nombre_base[:-len(sufijo)]
    
    # Asegurar que no haya sufijo duplicado
    if not nombre_base.endswith(nombre_sufijo):
        nombre_delay = f"{nombre_base}{nombre_sufijo}.mka"
    else:
        nombre_delay = f"{nombre_base}.mka"
    
    path_delay = temp_dir / nombre_delay
    
    print(f"  Creando audio con delay aplicado desde {inicio_corte_s:.3f}s...")
    
    try:
        media_info = MediaInfo.parse(str(mka_path))
        duracion_total_s = None
        for track in media_info.tracks:
            if track.track_type == 'Audio' and hasattr(track, 'duration'):
                duracion_total_ms = float(str(track.duration).replace(' ', ''))
                duracion_total_s = duracion_total_ms / 1000.0
                break
        
        if duracion_total_s is None:
            print(f"  Error: No se pudo obtener duración del audio")
            return None
    except Exception as e:
        print(f"  Error obteniendo duración con pymediainfo: {e}")
        return None
    
    if inicio_corte_s >= duracion_total_s:
        print(f"  Error: El corte ({inicio_corte_s:.3f}s) excede la duración total ({duracion_total_s:.3f}s)")
        return None
    
    inicio_formato = segundos_a_formato_ffmpeg(inicio_corte_s)
    
    cmd = [
        "ffmpeg",
        "-i", str(mka_path),
        "-ss", inicio_formato,
        "-c", "copy",
        "-map", "0",
        "-y",
        "-loglevel", "error",
        str(path_delay)
    ]
    
    print(f"  Comando ffmpeg: -ss {inicio_corte_s:.3f}s -c copy")
    print(f"  Corte: desde {inicio_corte_s:.3f}s hasta el final ({duracion_total_s:.3f}s)")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            if path_delay.exists() and path_delay.stat().st_size > 0:
                tamaño = path_delay.stat().st_size
                duracion_delay_s = duracion_total_s - inicio_corte_s
                print(f"  Audio con delay creado: {path_delay.name}")
                print(f"    Tamaño: {tamaño:,} bytes")
                print(f"    Duración: {duracion_delay_s:.3f}s")
                return path_delay
            else:
                print(f"  Error: Archivo no se creó o está vacío")
                return None
        else:
            print(f"  Error ffmpeg (código {result.returncode}):")
            if result.stderr:
                print(f"    {result.stderr[:200]}")
            return None
            
    except Exception as e:
        print(f"  Error ejecutando ffmpeg: {e}")
        return None

def calcular_duracion_audio_segundos(mka_path):
    """
    Calcula la duración del audio en segundos usando pymediainfo
    """
    try:
        media_info = MediaInfo.parse(str(mka_path))
        for track in media_info.tracks:
            if track.track_type == 'Audio' and hasattr(track, 'duration'):
                duracion_ms = float(str(track.duration).replace(' ', ''))
                return duracion_ms / 1000.0
        return None
    except Exception as e:
        print(f"  Error calculando duración con pymediainfo: {e}")
        return None

def concatenar_con_ffmpeg(archivos_a_concatenar, output_path, temp_dir):
    """
    PASO 6: Concatenar archivos MKA usando ffmpeg concat
    Herramienta: ffmpeg concat
    Propósito: Unir segmentos de silencio + audio original (delay positivo)
    Característica: concat_list.txt se conserva en directorio temporal
    """
    if not archivos_a_concatenar:
        print(f"  Error: No hay archivos para concatenar")
        return None
    
    print(f"\n  Concatenando {len(archivos_a_concatenar)} archivos...")
    print(f"  Archivo final: {output_path.name}")
    
    for i, archivo in enumerate(archivos_a_concatenar, 1):
        if not archivo.exists():
            print(f"  Error: Archivo {i} no existe: {archivo}")
            return None
        print(f"    {i}. {archivo.name}")
    
    lista_file = temp_dir / "concat_list.txt"
    with open(lista_file, 'w', encoding='utf-8') as f:
        for archivo in archivos_a_concatenar:
            f.write(f"file '{archivo.resolve()}'\n")
    
    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(lista_file),
        "-c", "copy",
        "-y",
        "-loglevel", "error",
        str(output_path)
    ]
    
    print(f"  Comando ffmpeg: -f concat -i [lista] -c copy")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            if output_path.exists() and output_path.stat().st_size > 0:
                tamaño = output_path.stat().st_size
                print(f"  Archivo concatenado creado exitosamente")
                print(f"    Tamaño: {tamaño:,} bytes")
                
                duracion_final = calcular_duracion_audio_segundos(output_path)
                if duracion_final:
                    print(f"    Duración: {duracion_final:.3f}s ({formato_tiempo_amigable(duracion_final)})")
                
                # CONCAT_LIST.TXT SE CONSERVA EN DIRECTORIO TEMPORAL
                print(f"    Lista de concatenación conservada en temporales: {lista_file.name}")
                
                return output_path
            else:
                print(f"  Error: Archivo no se creó o está vacío")
                if result.stderr:
                    print(f"    Error: {result.stderr[:200]}")
                return None
        else:
            print(f"  Error ffmpeg (código {result.returncode}):")
            if result.stderr:
                print(f"    {result.stderr[:200]}")
            return None
            
    except Exception as e:
        print(f"  Error ejecutando ffmpeg: {e}")
        return None

def cortar_final_audio(mka_path, duracion_corte_ms, frame_duration_ms, temp_dir, nombre_sufijo="_cortado"):
    """
    Corta el FINAL de un archivo de audio para alcanzar target
    NOTA: ffmpeg con -y sobrescribe automáticamente si el archivo existe
    """
    if not mka_path.exists():
        print(f"  Error: Archivo MKA no encontrado: {mka_path}")
        return None
    
    nombre_base = mka_path.stem
    for sufijo in ["_temp", "_analisis", "_delay", "_target"]:
        if nombre_base.endswith(sufijo):
            nombre_base = nombre_base[:-len(sufijo)]
    
    # Asegurar que no haya sufijo duplicado
    if not nombre_base.endswith(nombre_sufijo):
        nombre_cortado = f"{nombre_base}{nombre_sufijo}.mka"
    else:
        nombre_cortado = f"{nombre_base}.mka"
    
    path_cortado = temp_dir / nombre_cortado
    
    print(f"  Cortando final del audio por {duracion_corte_ms:.2f} ms...")
    
    try:
        media_info = MediaInfo.parse(str(mka_path))
        duracion_total_s = None
        for track in media_info.tracks:
            if track.track_type == 'Audio' and hasattr(track, 'duration'):
                duracion_total_ms = float(str(track.duration).replace(' ', ''))
                duracion_total_s = duracion_total_ms / 1000.0
                break
        
        if duracion_total_s is None:
            print(f"  Error: No se pudo obtener duración del audio")
            return None
    except Exception as e:
        print(f"  Error obteniendo duración con pymediainfo: {e}")
        return None
    
    # Ajustar corte a frame boundaries
    duracion_corte_s = duracion_corte_ms / 1000.0
    duracion_corte_ajustada_ms = calcular_duracion_ajustada(duracion_corte_ms, frame_duration_ms, mostrar_ajuste=False)
    duracion_corte_ajustada_s = duracion_corte_ajustada_ms / 1000.0
    
    if duracion_corte_ajustada_s >= duracion_total_s:
        print(f"  Error: El corte ({duracion_corte_ajustada_s:.3f}s) excede la duración total ({duracion_total_s:.3f}s)")
        return None
    
    duracion_final_s = duracion_total_s - duracion_corte_ajustada_s
    
    print(f"  Ajuste corte a frame boundaries:")
    print(f"    Corte solicitado: {duracion_corte_ms:.2f} ms ({duracion_corte_ms/frame_duration_ms:.3f} frames)")
    print(f"    Corte ajustado: {duracion_corte_ajustada_ms:.2f} ms")
    print(f"    Duración original: {duracion_total_s:.3f}s")
    print(f"    Duración final deseada: {duracion_final_s:.3f}s")
    
    # Usar ffmpeg para cortar el final (usando -t en lugar de -to)
    cmd = [
        "ffmpeg",
        "-i", str(mka_path),
        "-t", f"{duracion_final_s:.6f}",
        "-c", "copy",
        "-map", "0",
        "-y",
        "-loglevel", "error",
        str(path_cortado)
    ]
    
    print(f"  Comando ffmpeg: -t {duracion_final_s:.3f}s -c copy")
    print(f"  Corte: mantener primeros {duracion_final_s:.3f}s de {duracion_total_s:.3f}s")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            if path_cortado.exists() and path_cortado.stat().st_size > 0:
                tamaño = path_cortado.stat().st_size
                print(f"  Audio cortado creado: {path_cortado.name}")
                print(f"    Tamaño: {tamaño:,} bytes")
                print(f"    Duración: {duracion_final_s:.3f}s")
                return path_cortado
            else:
                print(f"  Error: Archivo no se creó o está vacío")
                return None
        else:
            print(f"  Error ffmpeg (código {result.returncode}):")
            if result.stderr:
                print(f"    {result.stderr[:200]}")
            return None
            
    except Exception as e:
        print(f"  Error ejecutando ffmpeg: {e}")
        return None

def procesar_delay_con_target(input_file, delay_str, target_str, temp_dir):
    """
    Procesa la aplicación de delay (positivo o negativo) con target
    Incluye concatenación final de segmentos con ffmpeg
    NOTA: Siempre crea nuevos archivos, nunca reutiliza existentes
    """
    print(f"\n{'='*60}")
    print(f"PROCESAMIENTO DE DELAY CON TARGET v1.0.0")
    print(f"{'='*60}")
    
    delay_ms = parsear_delay(delay_str)
    if delay_ms is None:
        print(f"Error: No se pudo parsear el delay: '{delay_str}'")
        print(f"Formatos aceptados: 2000, 2000ms, 2.0s, 2.0, -2000ms, -2.0s, 0")
        return "error", None
    
    target_s = parsear_target(target_str)
    if target_s is None:
        print(f"Error: No se pudo parsear el target: '{target_str}'")
        print(f"Formatos aceptados: HH:MM:SS.ms, H:MM:SS, MM:SS.ms, SS.ms, .ms, S.ms")
        return "error", None
    
    es_negativo = delay_ms < 0
    delay_absoluto = abs(delay_ms)
    
    print(f"Delay solicitado: {delay_ms:.2f} ms ({delay_ms/1000:.3f} s)")
    print(f"Target solicitado: {target_s:.3f} s ({formato_tiempo_amigable(target_s)})")
    
    if delay_ms == 0:
        print(f"Tipo: SOLO AJUSTE PARA TARGET (delay=0)")
    else:
        print(f"Tipo: {'NEGATIVO (cortar inicio)' if es_negativo else 'POSITIVO (agregar silencio)'} con ajuste para target")
    
    nombre_base = Path(input_file).stem
    mka_name = nombre_base + ".mka"
    mka_path = temp_dir / mka_name
    
    print(f"\nPaso 1/5: Creando contenedor MKA con ffmpeg...")
    
    if not crear_mka_con_ffmpeg(input_file, mka_path):
        return "error", None
    
    print(f"\nPaso 2/5: Extrayendo metadatos confiables con pymediainfo...")
    
    metadatos = obtener_metadatos_mediainfo(mka_path)
    
    if metadatos['Frame_duration_ms'] is None:
        print(f"\n{'='*60}")
        print(f"ERROR CRÍTICO: No se pudo determinar frame duration")
        print(f"{'='*60}")
        print(f"El proceso no puede continuar sin frame duration.")
        print(f"Metadatos disponibles:")
        mostrar_metadatos(metadatos, mka_name)
        return "error", None
    
    frame_duration_ms = metadatos['Frame_duration_ms']
    print(f"  Frame duration: {frame_duration_ms:.4f} ms")
    
    duracion_audio_s = calcular_duracion_audio_segundos(mka_path)
    if duracion_audio_s is None:
        print(f"ERROR: No se pudo obtener duración del audio")
        return "error", None
    
    print(f"  Duración audio original: {duracion_audio_s:.3f} s")
    print(f"  Target solicitado: {target_s:.3f} s")
    
    print(f"\nPaso 3/5: Calculando ajuste para target...")
    
    if delay_ms == 0:
        print(f"  Modo: Solo ajuste para target (delay=0)")
        
        ajuste_target_s = target_s - duracion_audio_s
        ajuste_target_ms = ajuste_target_s * 1000.0
        
        print(f"  Ajuste necesario para target: {ajuste_target_ms:.2f} ms ({ajuste_target_s:.3f} s)")
        
        if abs(ajuste_target_ms) < 0.1:
            print(f"\n  ¡Target ya alcanzado! No se necesita ajuste.")
            return "exacto", None
        
        elif ajuste_target_ms > 0:
            print(f"\n  Target > Audio: Necesario agregar {ajuste_target_ms:.2f} ms de silencio")
            resultado_target = "positivo"
        else:
            print(f"\n  Target < Audio: Necesario cortar {abs(ajuste_target_ms):.2f} ms")
            resultado_target = "negativo"
        
        if resultado_target == "positivo":
            print(f"\nPaso 4/5: Creando archivo base de silencio...")
            
            wav_path = convertir_mka_a_wav(mka_path, temp_dir)
            if not wav_path:
                print(f"Error: No se pudo crear archivo WAV para análisis")
                return "error", None
            
            resultado_silencios = analizar_silencios_wav(wav_path, frame_duration_ms)
            
            if not resultado_silencios:
                print(f"Error: No se encontraron silencios adecuados")
                return "error", None
            
            mostrar_resultado_silencios(resultado_silencios, Path(input_file).name)
            
            archivo_silencio = extraer_silencio_del_mka(mka_path, resultado_silencios, temp_dir)
            
            if not archivo_silencio or not archivo_silencio.exists():
                print(f"Error: No se pudo extraer el segmento de silencio")
                return "error", None
            
            silencio_base_path = archivo_silencio
            duracion_silencio_ms = resultado_silencios['duracion_ajustada_ms']
            
            print(f"  Duración silencio base: {duracion_silencio_ms:.2f} ms")
            
            print(f"\nPaso 5/5: Creando segmentos para target ({ajuste_target_ms:.2f} ms)...")
            
            segmentos_target, ajuste_real_ms = crear_segmentos_delay(
                silencio_base_path, 
                ajuste_target_ms, 
                frame_duration_ms, 
                duracion_silencio_ms, 
                temp_dir,
                sufijo="_target"
            )
            
            if not segmentos_target:
                print(f"Error: No se pudieron crear segmentos para target")
                return "error", None
            
            print(f"\nPaso 6/5: Concatenando audio con segmentos de target (ffmpeg)...")
            
            archivos_concatenar = [mka_path] + segmentos_target
            
            archivo_final_path = temp_dir / f"{nombre_base}_target.mka"
            
            archivo_final = concatenar_con_ffmpeg(archivos_concatenar, archivo_final_path, temp_dir)
            
            if not archivo_final:
                print(f"Error: No se pudo concatenar los archivos")
                return "error", None
            
            duracion_final_s = calcular_duracion_audio_segundos(archivo_final)
            if duracion_final_s:
                diferencia_final_s = target_s - duracion_final_s
                print(f"\n  Verificación final:")
                print(f"    Target solicitado: {target_s:.6f} s")
                print(f"    Duración final: {duracion_final_s:.6f} s")
                print(f"    Diferencia: {diferencia_final_s:.6f} s ({diferencia_final_s*1000:.3f} ms)")
            
            return "solo_target", archivo_final
        
        else:
            print(f"\nPaso 4/5: Procesando corte para target...")
            
            corte_ms = abs(ajuste_target_ms)
            corte_ajustado_ms, corte_frames_elegidos, corte_frames_exactos = ajustar_delay_a_frames(
                -corte_ms if corte_ms > 0 else 0, frame_duration_ms
            )
            corte_ajustado_ms = abs(corte_ajustado_ms)
            
            print(f"\n  Ajuste a frame boundaries:")
            print(f"    Corte necesario: {corte_ms:.2f} ms ({corte_ms/frame_duration_ms:.3f} frames)")
            print(f"    Corte ajustado: {corte_ajustado_ms:.2f} ms ({corte_frames_elegidos} frames)")
            
            inicio_corte_s = corte_ajustado_ms / 1000.0
            
            print(f"\nPaso 5/5: Creando audio ajustado al target...")
            
            archivo_target = crear_audio_con_delay(mka_path, inicio_corte_s, temp_dir, nombre_sufijo="_target")
            
            if not archivo_target or not archivo_target.exists():
                print(f"Error: No se pudo crear el audio ajustado al target")
                return "error", None
            
            duracion_final_s = calcular_duracion_audio_segundos(archivo_target)
            if duracion_final_s:
                diferencia_final_s = target_s - duracion_final_s
                print(f"\n  Verificación:")
                print(f"    Duración final: {duracion_final_s:.6f} s")
                print(f"    Target deseado: {target_s:.6f} s")
                print(f"    Diferencia: {diferencia_final_s:.6f} s ({diferencia_final_s*1000:.3f} ms)")
            
            return "solo_target", archivo_target
    
    else:
        print(f"\n  Modo: Delay con ajuste para target")
        
        delay_ajustado_ms, frames_elegidos, frames_exactos = ajustar_delay_a_frames(delay_ms, frame_duration_ms)
        
        print(f"  Delay solicitado: {delay_ms:.2f} ms")
        print(f"  Delay ajustado a frames: {delay_ajustado_ms:.2f} ms")
        
        if es_negativo:
            duracion_despues_delay_s = duracion_audio_s - (abs(delay_ajustado_ms) / 1000.0)
        else:
            duracion_despues_delay_s = duracion_audio_s + (delay_ajustado_ms / 1000.0)
        
        print(f"  Duración después del delay: {duracion_despues_delay_s:.3f} s")
        print(f"  Target solicitado: {target_s:.3f} s")
        
        ajuste_target_s = target_s - duracion_despues_delay_s
        ajuste_target_ms = ajuste_target_s * 1000.0
        
        print(f"  Ajuste necesario para target: {ajuste_target_ms:.2f} ms ({ajuste_target_s:.3f} s)")
        
        if abs(ajuste_target_ms) < 0.1:
            print(f"\n  ¡Target ya alcanzado! No se necesita ajuste adicional.")
            resultado_target = "exacto"
        elif ajuste_target_ms > 0:
            print(f"\n  Target > (Audio + Delay): Necesario agregar {ajuste_target_ms:.2f} ms de silencio")
            resultado_target = "positivo"
        else:
            print(f"\n  Target < (Audio + Delay): Necesario cortar {abs(ajuste_target_ms):.2f} ms adicionales")
            resultado_target = "negativo"
        
        if es_negativo:
            print(f"\nPaso 4/5: Procesando delay NEGATIVO con ajuste para target...")
            
            if ajuste_target_ms < 0:
                corte_total_ms = abs(delay_ajustado_ms) + abs(ajuste_target_ms)
                print(f"  Corte total necesario: {corte_total_ms:.2f} ms")
                print(f"    - Delay solicitado: {abs(delay_ms):.2f} ms")
                print(f"    - Ajuste para target: +{abs(ajuste_target_ms):.2f} ms")
            else:
                corte_total_ms = abs(delay_ajustado_ms)
            
            corte_ajustado_ms, corte_frames_elegidos, corte_frames_exactos = ajustar_delay_a_frames(
                -corte_total_ms if corte_total_ms > 0 else 0, frame_duration_ms
            )
            corte_ajustado_ms = abs(corte_ajustado_ms)
            
            print(f"\n  Ajuste a frame boundaries:")
            print(f"    Corte solicitado: {corte_total_ms:.2f} ms ({corte_total_ms/frame_duration_ms:.3f} frames)")
            print(f"    Corte ajustado: {corte_ajustado_ms:.2f} ms ({corte_frames_elegidos} frames)")
            
            inicio_corte_s = corte_ajustado_ms / 1000.0
            
            if abs(ajuste_target_ms) < 0.1:
                nombre_sufijo = "_delay"
            else:
                nombre_sufijo = "_delay_target"
            
            print(f"\nPaso 5/5: Creando audio con delay aplicado...")
            
            archivo_delay = crear_audio_con_delay(mka_path, inicio_corte_s, temp_dir, nombre_sufijo)
            
            if not archivo_delay or not archivo_delay.exists():
                print(f"Error: No se pudo crear el audio con delay")
                return "error", None
            
            duracion_final_s = calcular_duracion_audio_segundos(archivo_delay)
            if duracion_final_s:
                diferencia_final_s = target_s - duracion_final_s
                print(f"\n  Verificación:")
                print(f"    Duración final: {duracion_final_s:.6f} s")
                print(f"    Target deseado: {target_s:.6f} s")
                print(f"    Diferencia: {diferencia_final_s:.6f} s ({diferencia_final_s*1000:.3f} ms)")
            
            return "negativo_con_target", archivo_delay
            
        else:
            # CASO: DELAY POSITIVO con target
            print(f"\nPaso 4/5: Procesando delay POSITIVO con ajuste para target...")
            
            print(f"  Creando archivo base de silencio...")
            
            wav_path = convertir_mka_a_wav(mka_path, temp_dir)
            if not wav_path:
                print(f"Error: No se pudo crear archivo WAV para análisis")
                return "error", None
            
            resultado_silencios = analizar_silencios_wav(wav_path, frame_duration_ms)
            
            if not resultado_silencios:
                print(f"Error: No se encontraron silencios adecuados")
                return "error", None
            
            mostrar_resultado_silencios(resultado_silencios, Path(input_file).name)
            
            archivo_silencio = extraer_silencio_del_mka(mka_path, resultado_silencios, temp_dir)
            
            if not archivo_silencio or not archivo_silencio.exists():
                print(f"Error: No se pudo extraer el segmento de silencio")
                return "error", None
            
            silencio_base_path = archivo_silencio
            duracion_silencio_ms = resultado_silencios['duracion_ajustada_ms']
            
            print(f"  Duración silencio base: {duracion_silencio_ms:.2f} ms")
            
            print(f"\n  Creando segmentos para delay de {delay_ajustado_ms:.2f} ms...")
            
            segmentos_delay, delay_real_ms = crear_segmentos_delay(
                silencio_base_path, 
                delay_ajustado_ms, 
                frame_duration_ms, 
                duracion_silencio_ms, 
                temp_dir,
                sufijo="_delay"
            )
            
            if not segmentos_delay:
                print(f"Error: No se pudieron crear los segmentos de delay")
                return "error", None
            
            # CONCERN: Aquí está el problema - si resultado_target es "negativo", 
            # deberíamos cortar el final, no agregar más silencio
            segmentos_target = []
            ajuste_real_ms = 0
            
            if resultado_target == "negativo":
                # TARGET < (Audio + Delay): Necesitamos CORTAR el final
                print(f"\n  Target < (Audio + Delay): Cortando {abs(ajuste_target_ms):.2f} ms del final...")
                
                # Primero crear archivo con delay aplicado
                archivos_concatenar = segmentos_delay + [mka_path]
                archivo_con_delay_path = temp_dir / f"{nombre_base}_con_delay_temp.mka"
                
                archivo_con_delay = concatenar_con_ffmpeg(archivos_concatenar, archivo_con_delay_path, temp_dir)
                
                if not archivo_con_delay:
                    print(f"Error: No se pudo crear archivo con delay para luego cortar")
                    return "error", None
                
                # Ahora cortar el final para alcanzar el target
                print(f"\n  Cortando final del archivo para alcanzar target...")
                
                archivo_final = cortar_final_audio(
                    archivo_con_delay,
                    abs(ajuste_target_ms),
                    frame_duration_ms,
                    temp_dir,
                    nombre_sufijo="_delay_target"
                )
                
                if not archivo_final:
                    print(f"Error: No se pudo cortar el final del archivo")
                    return "error", None
                
                # Limpiar archivo temporal
                archivo_con_delay_path.unlink(missing_ok=True)
                
            elif resultado_target == "positivo":
                # TARGET > (Audio + Delay): Necesitamos agregar MÁS silencio
                print(f"\n  Creando segmentos adicionales para target ({ajuste_target_ms:.2f} ms)...")
                
                segmentos_target, ajuste_real_ms = crear_segmentos_delay(
                    silencio_base_path, 
                    ajuste_target_ms, 
                    frame_duration_ms, 
                    duracion_silencio_ms, 
                    temp_dir,
                    sufijo="_target"
                )
                
                if not segmentos_target:
                    print(f"  Advertencia: No se pudieron crear segmentos para target")
                    # Continuar sin segmentos target
                
                print(f"\nPaso 5/5: Concatenando todos los segmentos (ffmpeg)...")
                
                archivo_final_path = temp_dir / f"{nombre_base}_delay_target.mka"
                
                archivos_concatenar = segmentos_delay + [mka_path]
                if segmentos_target:
                    archivos_concatenar.extend(segmentos_target)
                
                archivo_final = concatenar_con_ffmpeg(archivos_concatenar, archivo_final_path, temp_dir)
                
                if not archivo_final:
                    print(f"Error: No se pudo concatenar los archivos")
                    return "error", None
            else:
                # resultado_target == "exacto" - Solo delay, target ya alcanzado
                print(f"\nPaso 5/5: Concatenando segmentos de delay...")
                
                archivo_final_path = temp_dir / f"{nombre_base}_delay.mka"
                
                archivos_concatenar = segmentos_delay + [mka_path]
                archivo_final = concatenar_con_ffmpeg(archivos_concatenar, archivo_final_path, temp_dir)
                
                if not archivo_final:
                    print(f"Error: No se pudo concatenar los archivos")
                    return "error", None
            
            # Calcular duración total de silencio generado
            duracion_total_silencio_ms = delay_real_ms
            if resultado_target == "positivo":
                duracion_total_silencio_ms += ajuste_real_ms
            
            print(f"\n  Duración total de silencio generado: {duracion_total_silencio_ms:.2f} ms")
            print(f"    - Delay solicitado: {delay_ajustado_ms:.2f} ms")
            if resultado_target == "positivo":
                print(f"    - Ajuste para target: {ajuste_real_ms:.2f} ms")
            elif resultado_target == "negativo":
                print(f"    - Corte para target: {abs(ajuste_target_ms):.2f} ms")
            
            # Calcular duración final estimada
            if resultado_target == "negativo":
                duracion_final_estimada_s = duracion_despues_delay_s + (ajuste_target_ms / 1000.0)
            else:
                duracion_final_estimada_s = duracion_audio_s + (duracion_total_silencio_ms / 1000.0)
            
            diferencia_target_s = target_s - duracion_final_estimada_s
            
            print(f"\n  Estimación final:")
            print(f"    Audio original: {duracion_audio_s:.6f} s")
            if resultado_target != "negativo":
                print(f"    Silencio total: {duracion_total_silencio_ms/1000:.6f} s")
            print(f"    Duración estimada: {duracion_final_estimada_s:.6f} s")
            print(f"    Target deseado: {target_s:.6f} s")
            print(f"    Diferencia estimada: {diferencia_target_s:.6f} s ({diferencia_target_s*1000:.3f} ms)")
            
            # Verificar duración real
            duracion_final_real_s = calcular_duracion_audio_segundos(archivo_final)
            if duracion_final_real_s:
                diferencia_final_s = target_s - duracion_final_real_s
                print(f"\n  Verificación real:")
                print(f"    Duración final real: {duracion_final_real_s:.6f} s")
                print(f"    Target deseado: {target_s:.6f} s")
                print(f"    Diferencia real: {diferencia_final_s:.6f} s ({diferencia_final_s*1000:.3f} ms)")
            
            return "positivo_con_target", archivo_final

def main():
    if len(sys.argv) < 2:
        print("Delay Fix v1.0.0 - Herramienta para análisis de audio y aplicación de delay")
        print("="*70)
        print("DESCRIPCIÓN:")
        print("  Analiza silencios en audio y aplica delays precisos ajustados a frame boundaries")
        print("  Permite target de duración exacta con alineación perfecta a frames")
        print("\nMODO 1: Análisis de silencio (extrae segmento para delays positivos)")
        print("  Uso: python delay_fix.py <archivo_de_audio>")
        print("\nMODO 2: Aplicar delay (positivo o negativo)")
        print("  Uso: python delay_fix.py <archivo_de_audio> <delay>")
        print("\nMODO 3: Aplicar delay con target exacto")
        print("  Uso: python delay_fix.py <archivo_de_audio> <delay> <target>")
        print("  NOTA: Si delay=0, solo se aplica ajuste para target")
        
        print("\nFORMATOS DE DELAY (positivo o negativo):")
        print("  2000       (2000 milisegundos)")
        print("  2000ms     (2000 milisegundos)")
        print("  2.0s       (2.0 segundos)")
        print("  -2000      (-2000 milisegundos - corta inicio)")
        print("  -2000ms    (-2000 milisegundos - corta inicio)")
        print("  -2.0s      (-2.0 segundos - corta inicio)")
        
        print("\nFORMATOS DE TARGET (duración total deseada):")
        print("  01:35:50     (1 hora, 35 minutos, 50 segundos)")
        print("  1:35:50.500  (1 hora, 35 minutos, 50 segundos y 500ms)")
        print("  35:50.500    (35 minutos, 50 segundos y 500ms)")
        print("  50.500       (50 segundos y 500ms)")
        print("  .500         (500 milisegundos)")
        print("  1.5          (1.5 segundos = 1500ms)")
        
        print("\nEJEMPLOS PRÁCTICOS:")
        print("  Analizar silencio:")
        print("    python delay_fix.py audio.aac")
        print("\n  Aplicar delay positivo (agrega 2 segundos de silencio):")
        print("    python delay_fix.py audio.aac 2000")
        print("\n  Aplicar delay negativo (corta 2 segundos del inicio):")
        print("    python delay_fix.py audio.aac -2000")
        print("\n  Ajustar duración total a 1:35:50 (con delay positivo):")
        print("    python delay_fix.py audio.aac 2000 01:35:50")
        print("\n  Solo ajustar duración total a 1:35:50 (delay=0):")
        print("    python delay_fix.py audio.aac 0 01:35:50")
        
        print("\nNOTAS IMPORTANTES:")
        print("  • Archivo final se guarda en el mismo directorio del archivo de entrada")
        print("  • Archivos temporales se guardan en directorio temporal del sistema")
        print("  • concat_list.txt se conserva en temporales para debugging")
        print("  • Requiere: ffmpeg en PATH y pymediainfo instalado")
        print("  • pymediainfo: pip install pymediainfo")
        
        print("\nFLUJO DE PROCESAMIENTO:")
        print("  1. Empaquetado a MKA (ffmpeg) → Contenedor con metadatos confiables")
        print("  2. Extracción de metadatos (pymediainfo) → Frame duration crítico")
        print("  3. Conversión a WAV (ffmpeg) → Para análisis de silencio")
        print("  4. Análisis de silencio (ffmpeg) → Estrategia 500ms→400ms→300ms")
        print("  5. Extracción de silencio (ffmpeg) → Alineado a frame boundaries")
        print("  6. Aplicación delay/target (ffmpeg) → Concatenación precisa")
        return 1
    
    input_file = sys.argv[1]
    
    if not os.path.exists(input_file):
        print(f"Error: Archivo no encontrado: {input_file}")
        return 1
    
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except:
        print("Error: ffmpeg no encontrado")
        print("  Instala FFmpeg para usar esta herramienta")
        return 1
    
    print(f"\n[Delay Fix v1.0.0]")
    print(f"Archivo origen: {Path(input_file).name}")
    
    temp_dir = Path(tempfile.gettempdir()) / "delay_fix"
    temp_dir.mkdir(exist_ok=True)
    
    if len(sys.argv) == 4:
        delay_str = sys.argv[2]
        target_str = sys.argv[3]
        
        resultado, archivo_final = procesar_delay_con_target(input_file, delay_str, target_str, temp_dir)
        
        if resultado == "error":
            print(f"\nError en el proceso de delay con target.")
            return 1
        elif resultado == "exacto":
            print(f"\n{'='*60}")
            print(f"PROCESO COMPLETADO EXITOSAMENTE")
            print(f"{'='*60}")
            print(f"Target ya alcanzado - No se necesitó ajuste adicional")
            print(f"{'='*60}")
            return 0
        else:
            # MODIFICACIÓN: Mover archivo final al directorio original
            if archivo_final and archivo_final.exists():
                destino_final = Path(input_file).parent / archivo_final.name
                shutil.move(str(archivo_final), str(destino_final))
                archivo_final = destino_final
                print(f"\n  Archivo final movido a directorio original: {destino_final}")
            
            print(f"\n{'='*60}")
            print(f"PROCESO COMPLETADO EXITOSAMENTE")
            print(f"{'='*60}")
            
            if resultado == "solo_target":
                print(f"Tipo: SOLO AJUSTE PARA TARGET (delay=0)")
                print(f"Archivo final creado: {archivo_final.name}")
                
                duracion_final = calcular_duracion_audio_segundos(archivo_final)
                if duracion_final:
                    print(f"Duración final: {duracion_final:.3f}s ({formato_tiempo_amigable(duracion_final)})")
                
            elif resultado == "positivo_con_target":
                print(f"Tipo: DELAY POSITIVO con ajuste para target")
                print(f"Archivo final creado: {archivo_final.name}")
                
                duracion_final = calcular_duracion_audio_segundos(archivo_final)
                if duracion_final:
                    print(f"Duración final: {duracion_final:.3f}s ({formato_tiempo_amigable(duracion_final)})")
                
            elif resultado == "negativo_con_target":
                print(f"Tipo: DELAY NEGATIVO con ajuste para target")
                print(f"Archivo final creado: {archivo_final.name}")
                
                duracion_final = calcular_duracion_audio_segundos(archivo_final)
                if duracion_final:
                    print(f"Duración final: {duracion_final:.3f}s ({formato_tiempo_amigable(duracion_final)})")
            
            print(f"\nUbicación del archivo final:")
            print(f"  {archivo_final}")
            print(f"  Tamaño: {archivo_final.stat().st_size:,} bytes")
            
            print(f"\nComando para copiar (si es necesario):")
            print(f"  copy \"{archivo_final}\" .")
            
            print(f"\nARCHIVOS TEMPORALES:")
            print(f"  Directorio: {temp_dir}")
            print(f"  Incluye: concat_list.txt (conservado para debugging)")
            print(f"  Los archivos temporales se sobrescriben automáticamente")
            
            print(f"{'='*60}")
            return 0
            
    elif len(sys.argv) == 3:
        delay_str = sys.argv[2]
        
        delay_ms = parsear_delay(delay_str)
        if delay_ms == 0:
            print(f"Error: Delay no puede ser 0 sin target")
            print(f"Si desea solo ajustar al target, use: python delay_fix.py {input_file} 0 <target>")
            return 1
        
        print(f"Modo: Aplicar delay sin target")
        print(f"Delay solicitado: {delay_str}")
        print(f"\nNOTA: Para usar target, ejecute: python delay_fix.py {input_file} {delay_str} <target>")
        
        print(f"\n{'='*60}")
        print(f"PROCESAMIENTO DE DELAY (sin target) v1.0.0")
        print(f"{'='*60}")
        
        es_negativo = delay_ms < 0
        
        nombre_base = Path(input_file).stem
        mka_name = nombre_base + ".mka"
        mka_path = temp_dir / mka_name
        
        print(f"\nPaso 1/5: Creando contenedor MKA con ffmpeg...")
        if not crear_mka_con_ffmpeg(input_file, mka_path):
            return 1
        
        print(f"\nPaso 2/5: Extrayendo metadatos con pymediainfo...")
        metadatos = obtener_metadatos_mediainfo(mka_path)
        
        if metadatos['Frame_duration_ms'] is None:
            print(f"\n{'='*60}")
            print(f"ERROR CRÍTICO: No se pudo determinar frame duration")
            print(f"{'='*60}")
            print(f"El proceso no puede continuar sin frame duration.")
            print(f"Metadatos disponibles:")
            mostrar_metadatos(metadatos, mka_name)
            return 1
        
        frame_duration_ms = metadatos['Frame_duration_ms']
        print(f"  Frame duration: {frame_duration_ms:.4f} ms")
        
        if es_negativo:
            print(f"\nPaso 3/5: Procesando delay NEGATIVO...")
            
            delay_ajustado_ms, frames_elegidos, frames_exactos = ajustar_delay_a_frames(delay_ms, frame_duration_ms)
            inicio_corte_s = abs(delay_ajustado_ms) / 1000.0
            
            print(f"  Delay ajustado: {delay_ajustado_ms:.2f} ms ({frames_elegidos} frames)")
            print(f"  Corte desde: {inicio_corte_s:.3f}s")
            
            archivo_delay = crear_audio_con_delay(mka_path, inicio_corte_s, temp_dir, "_delay")
            if not archivo_delay:
                return 1
            
            archivo_final = archivo_delay
            resultado = "negativo"
        else:
            print(f"\nPaso 3/5: Creando archivo base de silencio...")
            
            wav_path = convertir_mka_a_wav(mka_path, temp_dir)
            if not wav_path:
                print(f"Error: No se pudo crear archivo WAV")
                return 1
            
            resultado_silencios = analizar_silencios_wav(wav_path, frame_duration_ms)
            if not resultado_silencios:
                print(f"Error: No se encontraron silencios adecuados")
                return 1
            
            archivo_silencio = extraer_silencio_del_mka(mka_path, resultado_silencios, temp_dir)
            if not archivo_silencio:
                return 1
            
            silencio_base_path = archivo_silencio
            duracion_silencio_ms = resultado_silencios['duracion_ajustada_ms']
            
            print(f"\nPaso 4/5: Creando segmentos para delay de {abs(delay_ms):.2f} ms...")
            
            segmentos, delay_real_ms = crear_segmentos_delay(
                silencio_base_path, abs(delay_ms), frame_duration_ms, 
                duracion_silencio_ms, temp_dir, "_delay"
            )
            
            if not segmentos:
                return 1
            
            print(f"\nPaso 5/5: Concatenando segmentos...")
            
            archivo_final_path = temp_dir / f"{nombre_base}_delay.mka"
            archivos_concatenar = segmentos + [mka_path]
            
            archivo_final = concatenar_con_ffmpeg(archivos_concatenar, archivo_final_path, temp_dir)
            if not archivo_final:
                return 1
            
            resultado = "positivo"
        
        # MODIFICACIÓN: Mover archivo final al directorio original
        if archivo_final and archivo_final.exists():
            destino_final = Path(input_file).parent / archivo_final.name
            shutil.move(str(archivo_final), str(destino_final))
            archivo_final = destino_final
            print(f"\n  Archivo final movido a directorio original: {destino_final}")
        
        print(f"\nProceso completado exitosamente.")
        print(f"Tipo de delay: {resultado.upper()}")
        print(f"Archivo final creado: {archivo_final.name}")
        
        duracion_final = calcular_duracion_audio_segundos(archivo_final)
        if duracion_final:
            print(f"Duración final: {duracion_final:.3f}s ({formato_tiempo_amigable(duracion_final)})")
        
        print(f"\nUbicación: {archivo_final}")
        print(f"Tamaño: {archivo_final.stat().st_size:,} bytes")
        
        print(f"\nNOTA: Para usar target, agregue un tercer parámetro")
        print(f"{'='*60}")
        return 0
            
    else:
        print(f"Objetivo: Empaquetar a MKA → Extraer metadatos → Convertir a WAV → Analizar silencios → Extraer silencio")
        print("-" * 60)
        
        nombre_base = Path(input_file).stem
        mka_name = nombre_base + ".mka"
        mka_path = temp_dir / mka_name
        
        print(f"Paso 1/5: Creando contenedor MKA con ffmpeg...")
        
        if not crear_mka_con_ffmpeg(input_file, mka_path):
            return 1
        
        print(f"Paso 2/5: Extrayendo metadatos confiables con pymediainfo...")
        
        if not mka_path.exists():
            print(f"Error: Archivo MKA no se creó correctamente")
            return 1
        
        metadatos = obtener_metadatos_mediainfo(mka_path)
        
        if metadatos['Frame_duration_ms'] is None:
            print(f"\n{'='*60}")
            print(f"ERROR CRÍTICO: No se pudo determinar frame duration")
            print(f"{'='*60}")
            print(f"El proceso no puede continuar sin frame duration.")
            print(f"Metadatos disponibles:")
            mostrar_metadatos(metadatos, mka_name)
            print(f"\nEl archivo puede no tener la metadata necesaria.")
            return 1
        
        frame_duration_ms = metadatos['Frame_duration_ms']
        print(f"  Frame duration: {frame_duration_ms:.4f} ms")
        
        wav_path = convertir_mka_a_wav(mka_path, temp_dir)
        
        if not wav_path:
            print(f"Error: No se pudo crear archivo WAV para análisis")
            return 1
        
        resultado_silencios = analizar_silencios_wav(
            wav_path, 
            metadatos['Frame_duration_ms']
        )
        
        mostrar_resultado_silencios(resultado_silencios, Path(input_file).name)
        
        archivo_silencio = extraer_silencio_del_mka(
            mka_path, 
            resultado_silencios, 
            temp_dir
        )
        
        print(f"\n{'='*60}")
        print(f"RESUMEN DEL PROCESO v1.0.0")
        print(f"{'='*60}")
        print(f"1. MKA (metadatos confiables):")
        print(f"   {mka_path.name}")
        print(f"   Tamaño: {mka_path.stat().st_size:,} bytes")
        
        print(f"\n2. WAV (análisis de silencio):")
        print(f"   {wav_path.name}")
        print(f"   Tamaño: {wav_path.stat().st_size:,} bytes")
        print(f"   Formato: PCM S16LE Mono (16-bit, 1 canal)")
        
        print(f"\n3. ANÁLISIS DE SILENCIOS:")
        if resultado_silencios:
            print(f"   Encontrado a {resultado_silencios['umbral_detectado_db']} dB")
            print(f"   Duración original: {resultado_silencios['duracion_original_ms']:.1f} ms")
            print(f"   Duración ajustada: {resultado_silencios['duracion_ajustada_ms']:.1f} ms")
            print(f"   Inicio ajustado: {resultado_silencios['inicio_ajustado_s']:.3f} s")
            print(f"   Fin ajustado: {resultado_silencios['fin_ajustado_s']:.3f} s")
            print(f"   Frame duration: {resultado_silencios['frame_duration_ms']:.4f} ms")
        else:
            print(f"   No se encontraron silencios adecuados")
        
        print(f"\nARCHIVO DE SILENCIO EXTRAÍDO (PARA DELAYS POSITIVOS):")
        if archivo_silencio and resultado_silencios:
            print(f"   {archivo_silencio.name}")
            print(f"   Ubicación temporal: {archivo_silencio}")
            print(f"   Tamaño: {archivo_silencio.stat().st_size:,} bytes")
            print(f"   Duración: {resultado_silencios['duracion_ajustada_s']:.3f}s ({resultado_silencios['duracion_ajustada_ms']:.2f}ms)")
            print(f"")
            print(f"   Para copiar a carpeta actual:")
            print(f"     copy \"{archivo_silencio}\" .")
            print(f"")
            print(f"   Este segmento será utilizado para aplicar delays positivos.")
            print(f"   Usar: python delay_fix.py audio.aac <delay> [target]")
            print(f"   Ejemplos:")
            print(f"     python delay_fix.py audio.aac 2000")
            print(f"     python delay_fix.py audio.aac 2000 01:35:50")
            print(f"     python delay_fix.py audio.aac 0 01:35:50  (solo target)")
        else:
            print(f"   No se pudo extraer el segmento de silencio")
        
        print(f"\nARCHIVOS TEMPORALES:")
        print(f"  Directorio: {temp_dir}")
        print(f"  Incluye: concat_list.txt (conservado para debugging)")
        print(f"  Los archivos temporales se sobrescriben automáticamente")
        print(f"{'='*60}")
        
        return 0

if __name__ == "__main__":
    sys.exit(main())