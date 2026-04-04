#!/bin/zsh
# Ejecuta el bot usando el entorno virtual del proyecto
cd "$(dirname "$0")"
if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
  .venv/bin/python main.py
else
  echo ".venv no encontrado. Crea el entorno virtual con: python3 -m venv .venv"
  exit 1
fi
