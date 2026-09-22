"""
Diagnóstico: ejecuta este script desde la carpeta de Noted.
    python diagnostico.py
Copia el resultado completo y mándalo a Ines.
"""
import sys
import os
import json
import shutil
from pathlib import Path

# La raiz del repo: este script vive en tools/, config.py en la raiz.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("Noted — diagnóstico")
print("=" * 60)

# 1. Config
try:
    import config
    print("\n[OK] config.py cargado")
    print(f"     PROJECT_DIR  : {config.PROJECT_DIR}")
    print(f"     MINUTES_DIR  : {config.MINUTES_DIR}")
    print(f"     RECORDINGS_DIR: {config.RECORDINGS_DIR}")
    print(f"     OUTPUT_DIR env: {os.getenv('OUTPUT_DIR', '(no definida)')}")
    settings_path = config.PROJECT_DIR / 'settings.json'
    if settings_path.exists():
        s = json.loads(settings_path.read_text(encoding='utf-8'))
        print(f"     settings.json output_dir: {s.get('output_dir', '(no definida)')}")
    else:
        print("     settings.json: NO EXISTE")
except Exception as e:
    print(f"\n[ERROR] config.py: {e}")

# 2. Carpetas
print()
for label, d in [("MINUTES_DIR", config.MINUTES_DIR),
                  ("RECORDINGS_DIR", config.RECORDINGS_DIR),
                  ("recordings/processed", config.RECORDINGS_DIR / 'processed')]:
    exists = d.exists()
    n_md  = len(list(d.glob("*.md")))   if exists else 0
    n_wav = len(list(d.glob("*.wav")))  if exists else 0
    n_txt = len(list(d.glob("*.txt")))  if exists else 0
    n_json= len(list(d.glob("*.json"))) if exists else 0
    print(f"[{'OK' if exists else 'FALTA'}] {label}")
    print(f"     Ruta  : {d}")
    if exists:
        print(f"     .md   : {n_md}   .wav: {n_wav}   .txt: {n_txt}   .json: {n_json}")
    else:
        print("     *** La carpeta NO EXISTE ***")

# 3. Últimos .md
print()
if config.MINUTES_DIR.exists():
    mds = sorted(config.MINUTES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mds:
        print(f"Últimas minutas ({min(5, len(mds))} de {len(mds)}):")
        for md in mds[:5]:
            print(f"  {md.name}")
    else:
        print("[AVISO] La carpeta minutes/ existe pero NO tiene archivos .md")
else:
    print("[ERROR] La carpeta minutes/ no existe")

# 4. API key
print()
api_key = os.getenv("ANTHROPIC_API_KEY", "")
if api_key:
    print(f"[OK] ANTHROPIC_API_KEY: ...{api_key[-6:]}")
else:
    print("[ERROR] ANTHROPIC_API_KEY no encontrada — las minutas NO se pueden generar con Claude")

# 5. claude CLI
print()
claude = shutil.which("claude")
print(f"[{'OK' if claude else 'FALTA'}] claude CLI en PATH: {claude or 'NO ENCONTRADO'}")

# 6. .env
print()
env_path = config.PROJECT_DIR / '.env'
print(f"[{'OK' if env_path.exists() else 'FALTA'}] .env: {env_path}")

print()
print("=" * 60)
print("Copia todo lo de arriba y mándalo a Ines.")
print("=" * 60)
