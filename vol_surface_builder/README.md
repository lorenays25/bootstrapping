# vollib — Superficies de Volatilidad FX (Módulo 2)

Construye superficies de volatilidad implícita por par FX replicando la
metodología de Calypso, a partir de cotizaciones ATM / Risk Reversal / Butterfly.
Es el Módulo 2 del motor de factores de riesgo: consume las curvas del Módulo 1
(`curve_bootstrapper`) y alimenta al Módulo 3 (impactos: valorización y Griegas).

Seis superficies configuradas: **USD/MXN, USD/PEN, USD/BRL, USD/CLP, USD/COP y
EUR/USD**.

## Inicio rápido

### Interfaz web — pestaña "Superficies de Vol"

El módulo está integrado a la interfaz del Módulo 1: el mismo servidor sirve las
dos pestañas.

    cd ../curve_bootstrapper
    pip install -r requirements.txt -r ../vol_surface_builder/requirements.txt
    python server.py            # http://127.0.0.1:8000  ->  pestaña "Superficies de Vol"

En la pestaña: eliges el par, ves sus convenciones tal como están en el panel de
Calypso (con las desviaciones respecto del manual marcadas en ámbar), sus
cotizaciones con las alas ya derivadas, y con "▶ Construir superficies" obtienes
los 7 puntos del smile por tenor en bid/mid/ask, descargables como CSV. El botón
de sensibilidad compara la configuración actual contra la convención recomendada
para pares emergentes y reporta el desplazamiento de strikes.

Endpoints que agrega al servidor: `GET /vol/config`, `POST /vol/build`,
`POST /vol/sensitivity`, `POST /vol/export-csv`. Los de curvas no cambian, y si
`vollib` no está disponible la pestaña de curvas sigue funcionando igual (el
import es tolerante a fallo).

### Línea de comandos

    pip install -r requirements.txt
    python examples/run_all.py                  # construye las 6, bid/mid/ask
    python examples/sensibilidad_hallazgo1.py   # impacto de la convención de delta
    python tests/test_vollib.py                 # suite completa

Desde tu propio código:

```python
import sys, datetime; sys.path.insert(0, "src")
from vollib.orchestrator import build_from_file

vs, avisos = build_from_file("config/surfaces.yaml")
s = vs.sides["mid"]["USDMXN"]

s.vol(datetime.date(2027, 9, 1), 18.50)   # vol para (expiry, strike)
s.slice_by_tenor("1Y").table()            # los 7 puntos del smile de 1Y
vs.to_csv("USDMXN", "usdmxn_surface.csv") # tabla bid/mid/ask
```

## Estructura

    config/surfaces.yaml     Parametrización de las 6 superficies
    data/vol_quotes/         Exports de Calypso: quotes, parámetros, underlyings
    data/curves/             Curvas de descuento y tipos de cambio (Calypso)
    src/vollib/
        dates.py             Fechas: ACT/365, días hábiles, normalización de tenores
        curves.py            Curvas de descuento (log-lineal en DF) + spots FX
        deltas.py            Convenciones de delta y conversión delta <-> strike
        smile.py             SmileSlice: quotes -> 5 vols -> strikes -> spline
        surface.py           VolSurface / VolSurfaceSet: interpolación en plazo
        quotes_loader.py     Lectura y validación de los exports
        orchestrator.py      YAML -> superficies, pipeline bid/mid/ask
    examples/                Corridas de ejemplo
    tests/                   Suite de verificación
    docs/DOCUMENTACION.md    Documentación completa: matemática, convenciones, límites

Lee `docs/DOCUMENTACION.md` antes de tocar el código, y
`METODOLOGIA_SUPERFICIE_VOL_CALYPSO.md` / `COMPARACION_SUPERFICIES_6_PARES.md`
en la raíz del repo para el respaldo documental de cada convención.
