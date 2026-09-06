"""Utilidades transversales: logging, geometría y E/S."""
from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path

from src.config import get_path

_EARTH_R_M = 6_371_000.0


def get_logger(name: str) -> logging.Logger:
    """Logger que escribe a consola y a ``logs/run_YYYYMMDD.log``."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    log_file = get_path("logs") / f"run_{datetime.now():%Y%m%d}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distancia en metros sobre la esfera entre dos puntos (grados decimales)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(a))


def timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")
