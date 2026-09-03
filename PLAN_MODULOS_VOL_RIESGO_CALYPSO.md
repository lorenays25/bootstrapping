# Plan de arquitectura — Módulo 1 (Curvas), Módulo 2 (Superficies de Volatilidad) y Módulo 3 (Impactos: Valorización y Griegas)

Versión 0.2 · Borrador de planeación (sin código todavía) · 02/09/2026
Reordenado según numeración del usuario: Módulo 1 = curvas (ya existe), Módulo 2 =
superficies de volatilidad (nuevo), Módulo 3 = impactos — valorización y Griegas de
forwards/opciones, incluida la comparación contra los factores de riesgo de Calypso
(nuevo).

## 0. Contexto

`curve_bootstrapper` (paquete `curvelib`) ya construye 28+ curvas de descuento/proyección
de forma declarativa (YAML → DAG → bootstrap secuencial/global/simultáneo), con UI web,
carga de hojas de quotes estilo Calypso, y salida bid/mid/ask. Aparte, en el proyecto de
validación de opciones FX (`claude/validacion-opciones-fx-usdmxn.md`) ya existen, como
notebooks/scripts sueltos, una fórmula de Garman-Kohlhagen replicada de un Excel de front
office y una auditoría de `calculate_delta_gamma`/`calculate_vega` contra el manual de
Calypso "Model Validation Notes — FX Option Greeks V9.4".

Con la numeración que pediste, el mapa queda así:

| Módulo | Qué construye | Estado |
|---|---|---|
| **1 — Curvas** | Curvas de descuento/proyección (`curve_bootstrapper` / `curvelib`) | Ya existe, en producción de facto |
| **2 — Superficies de Volatilidad** | `σ(tenor, strike)` por par FX, calibrada de ATM/RR/BF | Nuevo — este documento lo diseña |
| **3 — Impactos (Valorización y Griegas)** | NPV + Delta/Gamma/Vega de forwards/NDF/opciones FX, usando (1) y (2); incluye comparar factores propios vs. Calypso | Nuevo — consolida notebooks existentes + agrega el comparador |

Este documento describe los Módulos 2 y 3 en detalle, cómo dependen del Módulo 1, y qué
falta confirmar contigo (sobre todo la metodología exacta de Calypso para la superficie)
antes de empezar a escribir código.

---

## 1. Principios de diseño (heredados del Módulo 1, para mantener consistencia)

Revisé `curve_bootstrapper/src/curvelib/*` y `docs/DOCUMENTACION.md` a fondo. Los Módulos
2 y 3 deberían seguir los mismos principios, porque son los que ya probaron funcionar en
este repo:

- **Híbrido QuantLib + scipy/numpy**: QuantLib solo para fechas/calendarios; todo el
  motor financiero (interpolación, calibración, root-finding) en scipy/numpy propio.
- **Parametrización declarativa (YAML)**: nada de niveles/quotes hardcodeados en código;
  igual que `curves.yaml`, cada módulo nuevo tendrá su propio YAML.
- **Objeto de contexto compartido**: `CurveContext` hoy da acceso a curvas + spots FX por
  nombre. Lo natural es extenderlo para que también dé acceso a superficies de
  volatilidad por par — así un instrumento de opción pide `ctx.curve("USD_SOFR")`,
  `ctx.curve("MXN_X_SOFR")` y `ctx.surface("USDMXN")` con la misma API.
- **Patrón instrumento con `model_quote()`/`residual()`**: la calibración de la
  superficie (ATM/RR/BF → smile) se modela con el mismo patrón que los instrumentos de
  curva del Módulo 1: cada punto de mercado es un "instrumento" que se reprecia con el
  modelo de smile vigente, y el residual se anula por calibración.
- **DAG de dependencias explícito**: la superficie de USDMXN (Módulo 2) depende de que
  `MXN_X_SOFR` y `USD_SOFR` (Módulo 1) ya estén construidas. El Módulo 3 depende de
  Módulos 1 y 2. Es el mismo tipo de DAG que ya resuelve `orchestrator.py`, solo que
  ahora cruza paquetes.
- **Documentación exhaustiva tipo `DOCUMENTACION.md`**: cada módulo nuevo trae su propio
  doc con la matemática de cada pieza, igual que la sección 4 del Módulo 1.
- **Repricing check / umbral de tolerancia**: la superficie calibrada debe reproducir
  exactamente sus propios quotes de entrada (ATM/RR/BF), igual que `BootstrapEngine._check`
  exige para curvas.

---

## 2. Módulo 1 — Curvas (`curve_bootstrapper` / `curvelib`) — recordatorio

Ya existe y no cambia. Los Módulos 2 y 3 lo consumen como dependencia (nunca duplican
`dates.py` ni `curve.py`): acceso vía `curvelib.orchestrator.build_from_file` /
`build_all` y el objeto `Curve` (`.df()`, `.zero()`, `.fwd()`).

---

## 3. Módulo 2 — Superficies de Volatilidad (paquete `vollib`)

### 3.1 Qué construye

Una superficie de volatilidad implícita por par FX: `σ(tenor, strike)` o `σ(tenor, delta)`,
calibrada a partir de quotes de mercado tipo **ATM, Risk Reversal (RR) y Butterfly (BF)**
por tenor y por nivel de delta (25D, 10D — a confirmar cuáles usa Calypso), análogo a como
el Módulo 1 calibra `DF(t)` a partir de OIS/FX forward/XCCY basis.

### 3.2 Objeto de datos: `VolSurface` (análogo a `Curve`)

Propuesta de interfaz, espejo de `curve.py`:

```python
@dataclass
class VolSurface:
    pair: str                      # "USDMXN"
    valuation_date: date
    tenors: List[str]              # ["1W","1M","3M","6M","1Y","2Y",...]
    pillar_dates: List[date]
    # por cada pilar de tenor: parámetros de smile calibrados (ver 3.4)
    smiles: List[SmileSlice]

    def vol(self, expiry: date, strike: float, forward: float) -> float: ...
    def vol_by_delta(self, expiry: date, delta: float, option_type: str) -> float: ...
```

`SmileSlice` guarda, por tenor, lo que el modelo de smile elegido necesite (ver 3.4):
p.ej. ATM vol, RR, BF, y los parámetros derivados (strikes de 25D/10D call-put, o los
coeficientes de SABR/Vanna-Volga si ese es el método).

Interpolación entre tenores (varianza total vs. tenor, no vol directa — es el estándar de
mercado para que no haya arbitraje de calendario) — **a confirmar contra Calypso**, igual
que `curve.py` documenta por qué usa log-lineal en DF y no interpolación directa de tasas.

### 3.3 Instrumentos de calibración

Mismo patrón que `instruments.py` del Módulo 1: cada quote de mercado (ATM de un tenor,
25D RR, 25D BF) es un objeto con `model_quote(ctx)` y `residual()`. La particularidad
frente a curvas es que la calibración de un `SmileSlice` normalmente es **simultánea**
dentro del tenor (3 incógnitas — ATM/skew/curvatura — con 3 quotes: ATM, RR, BF), así que
el "engine" aquí se parece más al modo `global`/`build_group` del Módulo 1 que al
secuencial.

### 3.4 Metodología — ⚠️ ABIERTO, necesito verificarlo contigo

Esta es la pieza que en los Módulos 1 y 3(Griegas) siempre se resolvió con evidencia
directa de Calypso (manual "Yield Curves Generation" + capturas de pantalla para curvas;
PDF "Model Validation Notes — FX Option Greeks V9.4" para las Griegas). Para la
superficie de vol **no tengo ese insumo todavía**. Antes de fijar la metodología en el
YAML/código necesito confirmar, idealmente con capturas de la pantalla de configuración
de la superficie en Calypso (análogas a las de "SIM ZC Cross MXN..." que ya usamos) o el
manual correspondiente (algo como "FX Volatility Surface" / "Volatility Curves
Generation"):

1. **Método de construcción del smile**: ¿Vanna-Volga, SABR, Malz, spline en delta,
   polinomial? Calypso soporta varios; necesito saber cuál está parametrizado para los
   pares que te interesan.
2. **Definición de ATM**: ¿ATM forward (strike = forward) o delta-neutral straddle
   (0-delta)? Cambia el strike de referencia.
3. **Niveles de delta cotizados**: ¿25D y 10D, o solo 25D? ¿Convención de delta "spot" o
   "forward", "premium-adjusted" o no (relevante en EM FX, donde suele ser
   premium-adjusted)?
4. **Interpolación en el eje de tenor**: ¿varianza total lineal, vol lineal, u otra?
5. **Extrapolación** más allá del tenor más largo o más corto que el que Calypso soporta.
6. **Fuente de datos**: ¿la hoja de quotes trae ATM/RR/BF directamente (formato pantalla,
   como los `Swap.5Y...` de `quotes_loader.py`), o hay que derivarlos de un export
   distinto?

Mientras no tenga esto confirmado, el Módulo 2 puede avanzar en **andamiaje** (YAML
schema, objeto `VolSurface`, orquestador, tests con un método de smile simple y
documentado como provisional — p.ej. spline cúbica en delta con ATM/RR/BF, el mínimo
común denominador de casi cualquier motor) y quedar marcado igual que el Módulo 1 marca
sus simplificaciones en la sección 8 de `DOCUMENTACION.md`, para reemplazarlo en cuanto
confirmemos la metodología real de Calypso.

### 3.5 Dependencia del Módulo 1

El Módulo 2 importa `curvelib` (no lo duplica): necesita `Curve.df()` de las curvas
domésticas y foráneas (para pasar de delta a strike) y el spot/forward FX (mismo
`insert_spot_node`/paridad cubierta que ya usan las curvas cross del Módulo 1). Esto
sugiere que `vollib` declare `curvelib` como dependencia de paquete (o se instale en el
mismo entorno vía `pyproject.toml`), nunca copiar código de fechas/curvas.

### 3.6 YAML propuesto (`config/surfaces.yaml`, espejo de `curves.yaml`)

```yaml
valuation_date: 2026-08-18

surfaces:
  USDMXN:
    fx_pair: USDMXN
    discount_curve: USD_SOFR          # para DF doméstica/foránea en delta->strike
    foreign_curve: MXN_X_SOFR
    depends_on: [USD_SOFR, MXN_X_SOFR]   # curvas del Módulo 1
    smile_model: vanna_volga           # o sabr / malz / spline_delta -- a confirmar
    delta_convention: forward_premium_adjusted   # a confirmar
    tenors:
      - {tenor: 1W,  atm: 0.0812, rr25: -0.0041, bf25: 0.0028}
      - {tenor: 1M,  atm: 0.0855, rr25: -0.0052, bf25: 0.0031}
      - {tenor: 3M,  atm: 0.0901, rr25: -0.0060, bf25: 0.0035}
      # ...
```

### 3.7 Estructura de carpetas propuesta

```
vol_surface_builder/               # Módulo 2
    pyproject.toml
    README.md
    config/surfaces.yaml
    docs/DOCUMENTACION.md
    src/vollib/
        __init__.py
        surface.py          # VolSurface, SmileSlice (análogo a curve.py del Módulo 1)
        smile_models.py     # vanna_volga.py / sabr.py / etc. -- intercambiables
        instruments.py      # ATMQuote, RiskReversalQuote, ButterflyQuote (model_quote/residual)
        engine.py           # calibración por tenor (análogo a engine.py del Módulo 1)
        orchestrator.py      # YAML -> DAG -> superficies (reusa curvelib para dependencias)
        quotes_loader.py     # parseo de hoja ATM/RR/BF estilo pantalla Calypso
    examples/
    tests/
```

---

## 4. Módulo 3 — Impactos: Valorización y Griegas (paquete `impactlib`)

### 4.1 Qué hace

Valorización de **forwards/NDF** y **opciones FX**, y sus **Griegas**, consumiendo
`curvelib.Curve` (Módulo 1) + `vollib.VolSurface` (Módulo 2), consolidando en un módulo
reusable e importable lo que hoy son notebooks/scripts independientes:

- `valoracion_fwd_ndf_usd_mxn.ipynb` / `valoracion_fwd_ndf_usd_mxn_curvas_propias.ipynb`
  → NPV de forwards/NDF (dos regímenes: NDF fijada vs. cross-currency).
- `valoracion_opciones_fx_usdmxn.ipynb` → Garman-Kohlhagen con spot referido a spot y
  doble tenor (Expiry para d1/d2, Settlement para descuento) — ya documentado y
  reconciliado contra Calypso (56/56 trades, diferencia mediana ~0%).
- `analisis_griegas_usdmxn.py` / `calculate_delta_gamma` / `calculate_vega` → Delta,
  Gamma, Vega — **ya auditados** contra el PDF de Calypso, con dos hallazgos concretos
  pendientes de decisión (ver `claude/validacion-opciones-fx-usdmxn.md`, sección
  "Pendiente"): el shock de spot debería ser la mitad (`spotShiftAmount/2`, efecto
  práctico nulo pero correcto por documentación) y la diferencia central en Vega
  (**sí importa**: reduce el error mediano de 0.447% a 0.001%).

Este módulo NO reinventa la fórmula: la toma tal cual está validada y la empaqueta, con
la corrección de Vega ya incorporada por defecto (y la de shock de spot como parámetro,
dado que no cambia el resultado pero sí la consistencia documental).

### 4.2 Inputs / Outputs del pricer

- **Input**: portafolio de trades (forward/NDF/opción FX; mismo esquema de campos que se
  usó en la solicitud a front office — ver `claude/solicitud-trades-opc-mxn-frontoffice.md`:
  par, tipo, posición, nominal, strike, fechas, prima, spot de referencia) + un
  `CurveContext` del Módulo 1 + un objeto de superficie del Módulo 2.
- **Output**: NPV, Delta, Gamma, Vega por trade y agregados por par/libro, en el mismo
  formato de tabla que ya se comparó contra `PV [USD]`/`DELTA [USD]`/`GAMMA [USD]`/
  `VEGA [USD]` de Calypso.

### 4.3 Comparación factores propios vs. Calypso (la pieza que pediste explícitamente)

Tu pedido original — "usar inputs de propio Calypso para verificar el impacto factores
de riesgo calypso vs factores construidos por nosotros mismos" — se resuelve **dentro**
del Módulo 3, no como un cuarto módulo aparte: es el **mismo pricer**, corrido dos veces
con dos fuentes de factores de riesgo distintas, y comparado:

```
                     ┌─ Factores PROPIOS (Módulo 1 + Módulo 2) ─┐
Portafolio (trades) ─┤                                          ├─→ comparador → reporte
                     └─ Factores CALYPSO (curvas + superficie   ┘     de diferencias
                        exportadas de Calypso)                       (NPV, Delta,
                                                                       Gamma, Vega)
```

Esto generaliza el patrón que ya se ejecutó dos veces de forma ad hoc en el proyecto
(curvas propias vs. Calypso en `curvas-mxn-metodologia.md`; Greeks propias vs. Calypso en
`validacion-opciones-fx-usdmxn.md`, Parte A/B), con los mismos umbrales ya validados:
diferencia relativa mediana/máxima, umbral de revisión 1% (relativo) y US$50 (absoluto)
para NPV, mismo criterio para Delta/Gamma/Vega.

**Inputs de Calypso que esto requiere:**
- Curvas: ya se tiene el formato (`USD_SOFR_propia_18_08_2026.csv` /
  `MXN_X_SOFR_propia_18_08_2026.csv`, y su contraparte extraída de Calypso).
- Portafolio con Greeks/NPV oficiales: ya se tiene el formato (export de 833 operaciones
  con `PV`, `PV [USD]`, `DELTA`, `VEGA`, etc.).
- **Superficie de volatilidad de Calypso**: ⚠️ formato aún no definido — necesito saber
  si Calypso puede exportar la superficie completa (ATM/RR/BF por tenor, o la malla
  strike×tenor) o si seguimos tomando `IMPLIEDVOLATILITY` por trade como en la
  validación anterior (que fue la decisión explícita para no construir superficie en esa
  validación puntual — ahora que el Módulo 2 sí la construye, esto se puede revisar).

### 4.4 Estructura de carpetas propuesta

```
impact_engine/                      # Módulo 3
    pyproject.toml
    README.md
    docs/DOCUMENTACION.md      # fórmulas: Garman-Kohlhagen, NPV fwd/NDF, Griegas (shock=mitad, vega central)
    config/                     # mapeo de curvas/superficie por par, umbrales de comparación
    src/impactlib/
        __init__.py
        forwards.py            # NPV forward/NDF (dos regímenes)
        options.py             # Garman-Kohlhagen + Delta/Gamma/Vega
        portfolio.py           # loaders de portafolio (reusa el parser ya validado del Excel/Calypso)
        factor_sources.py      # fuente PROPIA (Módulo 1 + 2) vs. fuente CALYPSO (exports)
        compare.py              # corre el pricer con ambas fuentes y arma el reporte de diferencias
        report.py               # tabla de comparación con umbrales (relativo 1% / absoluto US$50)
    examples/
    notebooks/                  # una corrida por fecha/portafolio, llamando a impactlib en vez de reimplementar
    tests/
```

---

## 5. Cómo se integran los 3 módulos (dependencias)

```
Módulo 1 (curve_bootstrapper)          Módulo 2 (vol_surface_builder)
   curvelib.Curve, CurveContext           vollib.VolSurface, calibración ATM/RR/BF
        \                                    /
         \                                  /
          v                                v
              Módulo 3 (impact_engine)
     NPV fwd/NDF, Garman-Kohlhagen, Griegas
     + comparador de factores propios vs. Calypso (§4.3)
```

El Módulo 2 depende del Módulo 1 (para curvas de descuento/forward). El Módulo 3 depende
de ambos, y además sabe cargar factores exportados de Calypso como fuente alternativa
para el comparador. Ninguno duplica código de fechas/calendarios/curvas: todo pasa por
`curvelib.dates` y `curvelib.curve`.

---

## 6. Roadmap por fases

1. **Fase 0 (este documento)**: acordar arquitectura, nombres, estructura de carpetas.
2. **Fase 1 — Módulo 2 andamiaje**: `VolSurface`, YAML schema, orquestador con
   dependencia al Módulo 1, un modelo de smile provisional documentado como tal (spline
   en delta), tests de repricing exacto de ATM/RR/BF. **Bloqueante real**: necesito la
   metodología de Calypso (§3.4) para no construir dos veces.
3. **Fase 2 — Módulo 3, pricer con factores propios**: portar Garman-Kohlhagen + NPV
   fwd/NDF + Griegas (con el fix de Vega ya incorporado) desde los notebooks a
   `impactlib`, con los mismos tests de reconciliación ya usados (56/56 trades, 78 trades
   de Griegas), usando por ahora `IMPLIEDVOLATILITY` de Calypso directo (como hoy) mientras
   el Módulo 2 no esté validado.
4. **Fase 3 — integración Módulo 2 → Módulo 3**: el pricer consumiendo
   `vollib.VolSurface` en vez de `IMPLIEDVOLATILITY` por trade — repetir la comparación
   Parte A/B (¿la superficie propia introduce diferencia material frente a usar el vol de
   Calypso directo, igual que se hizo para curvas?).
5. **Fase 4 — comparador vs. Calypso (§4.3)**: generalizar el harness de comparación
   ad hoc en `impactlib.compare`, correrlo sobre USD/MXN primero (ya hay todo el insumo)
   y después extender a USD/PEN, USD/BRL, USD/COP, USD/CLP (pendiente ya identificado en
   la sección "próximos pasos" del doc de validación de opciones).

---

## 7. Todo lo que necesito de ti para construir el Módulo 2 (checklist completo)

Organizado igual que se armó el Módulo 1 (metodología primero, luego datos, luego
insumos de validación) — ahí el patrón fue: manual BCP + manual Calypso + capturas de
pantalla → confirmar la metodología → conseguir data real → validar contra Calypso. Para
la superficie de vol necesito el mismo tipo de insumo, en este orden de prioridad:

### 7.1 Metodología — lo más importante, es lo único genuinamente bloqueante

Sin esto no puedo fijar la fórmula del smile sin arriesgarme a construir algo que luego
haya que rehacer (como se evitó con curvas, gracias a que sí tuvimos el manual desde el
principio). Necesito, en cualquiera de estas formas:

- **Manual de Calypso de superficie de volatilidad FX** (el equivalente a "Yield Curves
  Generation" que usamos para curvas). Suele llamarse algo como "FX Volatility Surface",
  "Volatility Curves Generation" o similar dentro de la documentación de Calypso — si no
  sabes el nombre exacto, cualquier PDF de Calypso que hable de "Volatility Surface",
  "Smile Construction" o "FX Options Volatility" sirve.
- **Capturas de pantalla de la ventana de configuración de la superficie** en Calypso
  para al menos un par (idealmente USD/MXN) — el equivalente a las capturas de "SIM ZC
  Cross MXN vs USD with SOFR" que usamos para curvas. Ahí normalmente se ve: método de
  interpolación/construcción, definición de ATM, niveles de delta, convención de delta.
- Si tienes algún **notebook o script propio** donde ya hayas intentado construir o
  interpolar una superficie (como compartiste `Opciones_OM_VOL_CLP_1.ipynb` para las
  Griegas) — aunque esté incompleto, ayuda muchísimo a inferir la convención real usada.

De ahí necesito puntualmente responder (§3.4 del documento):

1. Modelo de smile: Vanna-Volga / SABR / Malz / spline en delta / polinomial.
2. ATM: forward (strike = forward) o delta-neutral straddle (0-delta).
3. Niveles de delta cotizados: ¿25D y 10D, o solo 25D?
4. Convención de delta: spot vs. forward, premium-adjusted o no.
5. Interpolación en el eje de tenor: varianza total vs. vol directa.
6. Extrapolación fuera del tenor más corto/largo cotizado.
7. Day count / convención de tiempo a expiry usada en la superficie (¿la misma ACT/365
   que ya identificamos para Garman-Kohlhagen en `validacion-opciones-fx-usdmxn.md`, o
   distinta?).

### 7.2 Datos de mercado (quotes de entrada)

- **Hoja de vol de mercado**, idealmente en formato pantalla (como los `Swap.5Y...` /
  `MM.USD.SOFR...` que ya parsea `quotes_loader.py`), con ATM/RR/BF por tenor, para
  USD/MXN de al menos una fecha (recomiendo reusar el 18/08/2026, porque ya tenemos
  curvas + portafolio + Griegas de esa fecha reconciliados).
- Tenores estándar que cotiza tu mesa/Calypso para vol (ON, 1W, 1M, 2M, 3M, 6M, 9M, 1Y,
  2Y... — dime cuáles aplican, no asumo que sean los mismos de las curvas).
- Spot FX del día (ya sabemos la fuente para curvas — `tc_calypso.csv` — probablemente
  sea la misma, pero confírmalo).

### 7.3 Datos para validar la calibración (repricing check + reconciliación)

- **Superficie ya calibrada por Calypso** para la misma fecha/par, si se puede exportar
  (ATM/RR/BF por tenor, o la malla completa strike×tenor×vol). Es el equivalente al `Df
  Mid` de Calypso que usamos para validar curvas pilar por pilar.
- Si no se puede exportar la superficie completa, con el **`IMPLIEDVOLATILITY` por trade**
  que ya tenemos en el portafolio de 833 operaciones alcanza para una validación indirecta:
  para cada trade (strike, tenor conocidos) comparamos la vol que devuelve nuestra
  superficie interpolada contra la que Calypso le asignó a ese trade específico — es una
  prueba más débil que tener la malla completa, pero no requiere ningún export nuevo.

### 7.4 Decisiones de alcance (no bloquean el andamiaje, pero sí el orden de trabajo)

- **Pares y prioridad**: confirmar que USD/MXN va primero (ya tenemos curvas +
  portafolio + Griegas validados ahí) y el orden de extensión a PEN/BRL/COP/CLP.
- **Nombre y ubicación de los paquetes**: propuse `vol_surface_builder` (Módulo 2) e
  `impact_engine` (Módulo 3) como carpetas hermanas de `curve_bootstrapper` (Módulo 1)
  dentro del mismo repo `bootstrapping` — confírmalo o dime si prefieres otra
  organización (p.ej. todo dentro de `curve_bootstrapper/src/` como subpaquetes en vez
  de proyectos hermanos).

### 7.5 Cómo avanzamos si algo de esto tarda en conseguirse

§3.4 y la Fase 1 del roadmap ya contemplan esto: puedo empezar el andamiaje del Módulo 2
(YAML schema, objeto `VolSurface`, orquestador, integración con el Módulo 1) usando un
modelo de smile provisional y explícitamente marcado como tal — spline cúbica en delta a
partir de ATM/RR/BF, el mínimo común denominador de casi cualquier motor — para no
bloquear todo el trabajo en la metodología exacta de Calypso. Lo que si es cierto es que
§7.2 y §7.3 (datos de mercado y de validación de un caso real) sí hacen falta en algún
momento antes de dar por buena la superficie, igual que pasó con curvas y con Griegas.
