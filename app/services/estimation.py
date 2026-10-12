"""Método de estimación de HU (etapa 3). Funciones puras: sin base de datos ni proveedor de IA.

Dos métodos, uno por tipo de HU (ver documents/metodo-estimacion-hu-ia.md):
- fibonacci_tradicional_v1 (HU_TRADICIONAL): 3 factores deterministas.
- ia_factores_v1 (HU_IA): 5 factores; el riesgo sube un nivel la estimación y añade un spike.

Los factores (1–5) los puntúa una persona o un agente; los puntos los calcula siempre este módulo.
Cambiar pesos o escala = cambiar `VERSION` y el documento del método.
"""

from __future__ import annotations

import math

TRADITIONAL_METHOD = "fibonacci_tradicional_v1"
IA_METHOD = "ia_factores_v1"
FIBONACCI = (1, 2, 3, 5, 8, 13)
SPIKE_POINTS = 2  # timebox de experimentación; puntos aparte de la historia
MIN_FACTOR, MAX_FACTOR = 1, 5

TRADITIONAL_WEIGHTS = {"complejidad": 0.4, "esfuerzo": 0.4, "incertidumbre": 0.2}
IA_WEIGHTS = {"datos": 0.25, "modelo": 0.25, "evaluacion": 0.15, "riesgo": 0.15, "integracion": 0.20}

METHOD_BY_TYPE = {"HU_TRADICIONAL": TRADITIONAL_METHOD, "HU_IA": IA_METHOD}
WEIGHTS_BY_METHOD = {TRADITIONAL_METHOD: TRADITIONAL_WEIGHTS, IA_METHOD: IA_WEIGHTS}


class EstimationError(ValueError):
    """Factores o tipo inválidos para estimar."""


def method_for(story_type: str | None) -> str:
    if story_type not in METHOD_BY_TYPE:
        raise EstimationError("La HU necesita tipo HU_IA o HU_TRADICIONAL antes de estimarse")
    return METHOD_BY_TYPE[story_type]


def _validate(method: str, factors: dict) -> dict[str, int]:
    expected = set(WEIGHTS_BY_METHOD[method])
    if set(factors) != expected:
        missing, extra = expected - set(factors), set(factors) - expected
        raise EstimationError(
            f"Factores incompletos o desconocidos para {method}: faltan {sorted(missing)}, sobran {sorted(extra)}"
        )
    clean: dict[str, int] = {}
    for name in expected:
        value = factors[name]
        # bool es subclase de int en Python: se rechaza explícitamente.
        if isinstance(value, bool) or not isinstance(value, int) or not MIN_FACTOR <= value <= MAX_FACTOR:
            raise EstimationError(f"El factor '{name}' debe ser entero entre {MIN_FACTOR} y {MAX_FACTOR}")
        clean[name] = value
    return clean


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def _index_from_raw(raw: float) -> int:
    # Escala lineal [1, 5] → índice Fibonacci [0, 5]. 1→1 pt, 5→13 pt.
    return _round_half_up((raw - MIN_FACTOR) * (len(FIBONACCI) - 1) / (MAX_FACTOR - MIN_FACTOR))


def _clamp(idx: int) -> int:
    return max(0, min(len(FIBONACCI) - 1, idx))


def estimate(story_type: str | None, factors: dict) -> dict:
    """Devuelve el bloque completo a guardar: method, points, range, spike y factores validados."""
    method = method_for(story_type)
    clean = _validate(method, factors)
    weights = WEIGHTS_BY_METHOD[method]
    raw = sum(weights[name] * clean[name] for name in weights)

    idx = _index_from_raw(raw)
    spike_required = False
    if method == IA_METHOD:
        if clean["riesgo"] >= 4:
            idx += 1
        spike_required = clean["datos"] >= 4 or clean["riesgo"] >= 4
    idx = _clamp(idx)

    return {
        "method": method,
        "points": FIBONACCI[idx],
        "range": {"min": FIBONACCI[_clamp(idx - 1)], "max": FIBONACCI[_clamp(idx + 1)]},
        "spike": {"required": spike_required, "points": SPIKE_POINTS if spike_required else 0},
        "raw_score": round(raw, 2),
        "factors": clean,
    }


def apply_to_story_data(story_type: str | None, data: dict) -> dict:
    """Si `data` trae `estimate_factors`, calcula y reemplaza puntos y método. Devuelve data nueva."""
    if data.get("estimate_factors") is None:
        return data
    result = estimate(story_type, data["estimate_factors"])
    sent_method = data.get("estimate_method")
    if sent_method and sent_method != result["method"]:
        raise EstimationError(f"estimate_method '{sent_method}' no corresponde al tipo; debe ser {result['method']}")
    out = dict(data)
    out["estimate_points"] = result["points"]
    out["estimate_method"] = result["method"]
    out["estimate_factors"] = {**result, "version": 1}
    return out


__all__ = [
    "EstimationError",
    "IA_METHOD",
    "TRADITIONAL_METHOD",
    "apply_to_story_data",
    "estimate",
    "method_for",
]
