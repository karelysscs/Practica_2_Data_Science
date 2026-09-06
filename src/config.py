"""Lectura centralizada de parámetros desde ``config.md``.

El resto del código importa desde aquí; nunca define rutas ni umbrales propios.

    from src.config import CFG, get_path
    deps = CFG["departments"]
    raw_dir = get_path("raw")
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_MD = ROOT / "config.md"

_YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL)


@lru_cache(maxsize=1)
def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Devuelve el dict de configuración leído del bloque ``yaml`` de config.md."""
    md_path = Path(path) if path else CONFIG_MD
    if not md_path.exists():
        raise FileNotFoundError(f"No se encontró {md_path}")
    text = md_path.read_text(encoding="utf-8")
    match = _YAML_BLOCK.search(text)
    if not match:
        raise ValueError(
            "config.md no contiene un bloque ```yaml ... ``` con los parámetros."
        )
    cfg = yaml.safe_load(match.group(1))
    if not isinstance(cfg, dict):
        raise ValueError("El bloque yaml de config.md no define un mapeo válido.")
    cfg["_root"] = str(ROOT)
    return cfg


# Acceso rápido a nivel de módulo
CFG: dict[str, Any] = load_config()


def get_path(key: str, *, create: bool = True) -> Path:
    """Resuelve una ruta declarada en ``paths:`` de config.md (relativa a ROOT)."""
    try:
        rel = CFG["paths"][key]
    except KeyError as exc:
        opciones = ", ".join(CFG["paths"])
        raise KeyError(f"paths['{key}'] no existe. Opciones: {opciones}") from exc
    p = ROOT / rel
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def department_names() -> list[str]:
    """Lista de departamentos seleccionados (mayúsculas, sin tildes normalizadas)."""
    return list(CFG["departments"].values())


def department_ubigeos() -> dict[str, str]:
    """Mapa {DEPARTAMENTO: 'NN'} con el UBIGEO de 2 dígitos."""
    return dict(CFG["department_ubigeo"])


if __name__ == "__main__":
    import json

    print(json.dumps(load_config(), indent=2, ensure_ascii=False))
