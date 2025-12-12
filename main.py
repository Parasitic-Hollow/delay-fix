#!/usr/bin/env python3
"""
Audio Tools - Punto de Entrada Principal
=========================================
Dispatcher central para herramientas de procesamiento de audio.

Uso:
    python main.py                           # Menú interactivo
    python main.py <herramienta> [args]      # Uso directo

Ejemplos:
    python main.py delay-fix input.flac
    python main.py delay-fix input.flac 500ms 1:23:45.678
"""

import sys
import importlib
from pathlib import Path

# Registro de herramientas - agregar nuevas herramientas aquí
TOOLS = {
    "delay-fix": {
        "module": "tools.delay_fix",
        "description": "Analiza audio y aplica correcciones de delay"
    },
    # Herramientas futuras se agregan aquí:
    # "fps-fix": {
    #     "module": "tools.fps_fix",
    #     "description": "Corrige problemas de FPS en audio"
    # },
}


def print_banner():
    """Imprime el banner de la aplicación."""
    print()
    print("=" * 50)
    print("           AUDIO TOOLS")
    print("=" * 50)


def show_menu():
    """Muestra el menú interactivo y retorna la herramienta seleccionada."""
    print_banner()
    print("\nHerramientas disponibles:\n")
    
    tool_list = list(TOOLS.keys())
    
    for i, name in enumerate(tool_list, 1):
        desc = TOOLS[name]["description"]
        print(f"  {i}. {name}")
        print(f"     {desc}\n")
    
    print(f"  0. Salir")
    print()
    
    while True:
        try:
            opcion = input("Selecciona una opción: ").strip()
            
            if opcion == "0" or opcion.lower() in ("salir", "exit", "q"):
                return None
            
            # Intentar como número
            try:
                idx = int(opcion)
                if 1 <= idx <= len(tool_list):
                    return tool_list[idx - 1]
                else:
                    print(f"Opción inválida. Ingresa un número del 1 al {len(tool_list)} (o 0 para salir)")
            except ValueError:
                # Intentar como nombre de herramienta
                if opcion in TOOLS:
                    return opcion
                print(f"Herramienta '{opcion}' no encontrada. Intenta de nuevo.")
                
        except (KeyboardInterrupt, EOFError):
            print("\n")
            return None


def get_tool_args(tool_name):
    """Solicita los argumentos para la herramienta seleccionada."""
    print(f"\n--- {tool_name} ---")
    print("Ingresa los argumentos (o presiona Enter para ver la ayuda):\n")
    
    try:
        args_str = input(f"{tool_name} > ").strip()
        if args_str:
            return args_str.split()
        return ["--help"]
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return None


def run_tool(tool_name, args):
    """Ejecuta la herramienta especificada con los argumentos dados."""
    if tool_name not in TOOLS:
        print(f"Error: Herramienta desconocida '{tool_name}'")
        return 1
    
    tool_info = TOOLS[tool_name]
    
    try:
        module = importlib.import_module(tool_info["module"])
        
        # Configurar sys.argv para la herramienta
        original_argv = sys.argv
        sys.argv = [f"main.py {tool_name}"] + args
        
        try:
            if hasattr(module, "main"):
                return module.main()
            else:
                print(f"Error: La herramienta '{tool_name}' no tiene función main()")
                return 1
        finally:
            sys.argv = original_argv
            
    except ImportError as e:
        print(f"Error: No se pudo importar el módulo '{tool_info['module']}'")
        print(f"Detalles: {e}")
        return 1
    except Exception as e:
        print(f"Error ejecutando herramienta '{tool_name}': {e}")
        return 1


def main():
    """Punto de entrada principal."""
    # Con argumentos - modo directo
    if len(sys.argv) >= 2:
        first_arg = sys.argv[1]
        
        # Ayuda
        if first_arg in ("--help", "-h", "help", "ayuda"):
            print_banner()
            print("\nUso:")
            print("  python main.py                    Menú interactivo")
            print("  python main.py <herramienta>      Ejecutar herramienta")
            print("\nHerramientas disponibles:")
            for name, info in TOOLS.items():
                print(f"  {name:<15} {info['description']}")
            return 0
        
        # Ejecutar herramienta directamente
        tool_name = first_arg
        tool_args = sys.argv[2:]
        return run_tool(tool_name, tool_args)
    
    # Sin argumentos - menú interactivo
    tool_name = show_menu()
    
    if not tool_name:
        print("Saliendo...")
        return 0
    
    args = get_tool_args(tool_name)
    
    if args is None:
        print("Cancelado.")
        return 0
    
    return run_tool(tool_name, args)


if __name__ == "__main__":
    sys.exit(main())
