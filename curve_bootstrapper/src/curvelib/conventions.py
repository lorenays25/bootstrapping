"""
conventions.py — Resolución de convenciones POR INSTRUMENTO.

PROBLEMA QUE RESUELVE
---------------------
Una misma curva puede mezclar instrumentos con convenciones distintas. Caso
real: en PEN_OIS_TIBO los swaps OIS liquidan T+2 pero los bonos soberanos
liquidan T+1. Antes, `conventions:` vivía solo a nivel de CURVA y valía para
todos sus instrumentos por igual. Ahora cada instrumento resuelve su propia
convención efectiva, y el sistema deja registro de DE DÓNDE salió cada valor.

JERARQUÍA DE RESOLUCIÓN (de menos a más específico; gana el más específico)
--------------------------------------------------------------------------
    1. curve            `conventions:` del bloque de la curva  (defaults)
    2. preset           bloque `conventions:` raíz del YAML, referenciado
                        por el instrumento vía `convention: <nombre>`
    3. instrument       sub-bloque `conventions:` dentro del instrumento
    4. instrument-flat  campos sueltos en el instrumento (forma histórica;
                        se mantiene por compatibilidad y gana sobre todo)

Los DEFAULTS DEL MOTOR no se materializan aquí a propósito: siguen viviendo
en los `conv.get(clave, default)` de cada clase de instrumento. Algunos son
dependientes de la clase (p.ej. short_end_payment_style es 'bullet' en
ois_swap y 'periodic' en el resto), así que inyectarlos en el diccionario
resuelto cambiaría el comportamiento. Para REPORTE sí se muestran, vía
`effective_conventions()`, claramente marcados como 'default'.

TRAZABILIDAD
------------
`resolve()` devuelve (resuelto, procedencia). `procedencia[campo]` dice en
qué capa se fijó el valor. Esto alimenta el endpoint /conventions y sirve
para reconciliar contra el sistema de primera línea: se puede responder
"¿de dónde salió el spot_lag de este pilar?" sin leer el YAML a mano.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Capas, de menos a más específica. El orden importa: define la precedencia.
LAYERS = ("curve", "preset", "instrument", "instrument-flat")

# Claves del instrumento que son DATOS, no convenciones (nunca se mergean
# como convención ni se reportan como tales).
NON_CONVENTION_KEYS = frozenset({
    "type", "tenor", "quote", "convention", "conventions", "id", "label",
    "comment", "ticker",
})


class ConventionError(ValueError):
    """Error de configuración de convenciones (campo faltante o inválido)."""


# ---------------------------------------------------------------------------
def _as_dict(value, ctx: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConventionError(f"{ctx}: se esperaba un mapa de convenciones, se recibió {type(value).__name__}.")
    return dict(value)


def split_instrument_spec(spec: dict) -> Tuple[dict, dict]:
    """Separa el spec del instrumento en (datos, convenciones_flat)."""
    data = {k: v for k, v in spec.items() if k in NON_CONVENTION_KEYS}
    flat = {k: v for k, v in spec.items() if k not in NON_CONVENTION_KEYS}
    return data, flat


def resolve(
    spec: dict,
    curve_conv: Optional[dict] = None,
    presets: Optional[dict] = None,
) -> Tuple[dict, Dict[str, str]]:
    """Resuelve la convención efectiva de UN instrumento.

    spec       : dict del instrumento tal cual viene del YAML
    curve_conv : `conventions:` de la curva (defaults para sus instrumentos)
    presets    : bloque `conventions:` raíz del YAML {nombre: {...}}

    Devuelve (resuelto, procedencia).
    """
    curve_conv = _as_dict(curve_conv, "conventions de la curva")
    presets = presets or {}

    resolved: dict = {}
    provenance: Dict[str, str] = {}

    def apply(layer: str, values: dict) -> None:
        for k, v in values.items():
            resolved[k] = v
            provenance[k] = layer

    # 1) curva
    apply("curve", curve_conv)

    # 2) preset referenciado por nombre
    preset_name = spec.get("convention")
    if preset_name is not None:
        if preset_name not in presets:
            raise ConventionError(
                f"El instrumento referencia convention: '{preset_name}', que no está "
                f"definida en el bloque raíz `conventions:`. "
                f"Disponibles: {sorted(presets) or '(ninguna)'}"
            )
        preset = _as_dict(presets[preset_name], f"preset '{preset_name}'")
        # un preset puede fijar el 'type'; no es convención, se ignora aquí
        preset.pop("type", None)
        apply("preset", preset)

    # 3) sub-bloque `conventions:` del instrumento
    apply("instrument", _as_dict(spec.get("conventions"), "conventions del instrumento"))

    # 4) campos sueltos del instrumento (forma histórica; máxima precedencia)
    _, flat = split_instrument_spec(spec)
    apply("instrument-flat", flat)

    return resolved, provenance


def instrument_type(spec: dict, presets: Optional[dict] = None) -> Optional[str]:
    """Tipo del instrumento: el propio `type`, o el que herede de su preset.
    Permite escribir `- {convention: pen_govt_bond, maturity: ..., quote: ...}`
    sin repetir `type: sovereign_bond` en cada bono."""
    if spec.get("type"):
        return spec["type"]
    name = spec.get("convention")
    if name and presets and name in presets:
        return (presets[name] or {}).get("type")
    return None


# ---------------------------------------------------------------------------
# Validación por tipo de instrumento
# ---------------------------------------------------------------------------
def validate(
    itype: str,
    resolved: dict,
    schema: dict,
    required_by_type: dict,
    provenance: Optional[dict] = None,
    strict: bool = False,
) -> List[str]:
    """Valida la convención resuelta contra el catálogo.

    Detecta tres cosas:
      - campo OBLIGATORIO faltante para el tipo -> siempre error
      - campo DESCONOCIDO (typo) -> aviso, o error si strict
      - campo válido pero NO APLICABLE al tipo -> aviso, o error si strict

    REGLA DE APLICABILIDAD: solo se avisa cuando el campo se fijó
    explícitamente EN EL INSTRUMENTO (o en su preset). Los campos heredados
    de la curva se ignoran en silencio, porque una curva mixta comparte
    legítimamente convenciones entre tipos distintos: p.ej. PEN_X_SOFR
    define fx_pair a nivel curva y lo usan sus fx_forward, mientras sus
    xccy_fixed_float simplemente no lo leen. Avisar de eso sería ruido.

    Los avisos no rompen configuraciones existentes: se devuelven para que
    la UI y el endpoint /conventions los muestren.
    """
    warnings: List[str] = []
    provenance = provenance or {}

    for field in required_by_type.get(itype, ()):
        if resolved.get(field) is None:
            raise ConventionError(
                f"[{itype}] falta la convención obligatoria '{field}'. "
                f"Defínela en el instrumento, en su preset, o en las conventions de la curva."
            )

    for field, value in resolved.items():
        spec = schema.get(field)
        layer = provenance.get(field, "?")
        set_on_instrument = layer in ("preset", "instrument", "instrument-flat")

        if spec is None:
            msg = (f"[{itype}] convención desconocida '{field}' (¿typo?). "
                   f"Se ignora en el cálculo. [origen: {layer}]")
            if strict:
                raise ConventionError(msg)
            warnings.append(msg)
            continue

        applies = spec.get("applies_to")
        if applies and itype not in applies and set_on_instrument:
            msg = (f"[{itype}] la convención '{field}' no aplica a este tipo "
                   f"(aplica a: {', '.join(sorted(applies))}). Se ignora. "
                   f"[origen: {layer}]")
            if strict:
                raise ConventionError(msg)
            warnings.append(msg)
            continue

        if spec.get("type") == "enum" and value is not None:
            allowed = spec.get("values", ())
            if allowed and value not in allowed:
                raise ConventionError(
                    f"[{itype}] {field}={value!r} inválido. Valores permitidos: {list(allowed)}."
                )

    return warnings


def effective_conventions(itype: str, resolved: dict, provenance: dict,
                          schema: dict, class_defaults: Optional[dict] = None) -> List[dict]:
    """Vista de REPORTE: toda convención aplicable al tipo, con su valor
    efectivo y su procedencia. Los campos no fijados en ninguna capa se
    muestran con su default (del motor o de la clase), marcados 'default'.

    Es la vista que consume la UI y el endpoint /conventions; no participa
    del cálculo.
    """
    class_defaults = class_defaults or {}
    rows = []
    for field, spec in schema.items():
        applies = spec.get("applies_to")
        if applies and itype not in applies:
            continue
        if field in resolved:
            rows.append({"field": field, "value": resolved[field],
                         "source": provenance.get(field, "?"),
                         "type": spec.get("type"),
                         "values": spec.get("values"),
                         "description": spec.get("description", "")})
        else:
            default = class_defaults.get(field, spec.get("default"))
            rows.append({"field": field, "value": default,
                         "source": "default",
                         "type": spec.get("type"),
                         "values": spec.get("values"),
                         "description": spec.get("description", "")})
    return sorted(rows, key=lambda r: r["field"])
