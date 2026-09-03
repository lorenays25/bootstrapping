# Documentación — Módulo 2: Superficies de Volatilidad FX

Versión 0.1.0 · Motor funcional para las 6 superficies del proyecto.

Respaldo documental de cada convención: `METODOLOGIA_SUPERFICIE_VOL_CALYPSO.md`
(metodología verificada contra la pantalla y el manual Calypso "FX Volatility
Surfaces" v16.1) y `COMPARACION_SUPERFICIES_6_PARES.md` (contraste entre pares),
ambos en la raíz del repo.

---

## 1. Qué hace este módulo

A partir de las cotizaciones de mercado de un par FX —ATM, Risk Reversal y
Butterfly a 25 y 10 delta, por tenor— construye una superficie
`σ(expiración, strike)` que **reprecia exactamente** sus propios quotes de
entrada, replicando la metodología del generador `FXOption` de Calypso.

Sigue los mismos principios de diseño que el Módulo 1 (`curvelib`):

- **Parametrización declarativa**: las 6 superficies viven en
  `config/surfaces.yaml`; agregar un par no requiere tocar código.
- **La convención es dato, no código**: los parámetros se leen del panel
  exportado de Calypso, que es la fuente de verdad. El YAML solo puede
  sobreescribirlos vía `overrides:`, pensado para análisis de sensibilidad.
- **Motor propio en numpy/scipy**, transparente y modificable.
- **Chequeo de repricing**: la superficie tiene que devolver la vol de entrada en
  cada strike calibrado (test 7 y 8 de la suite, residual < 1e-9).

**No construye curvas**: las consume. Hoy corre contra las curvas exportadas de
Calypso para aislar variables; `curves.CurvelibAdapter` deja listo el cambio a
las curvas propias del Módulo 1.

---

## 2. Arquitectura

```
config/surfaces.yaml      <- LA parametrización: 6 superficies y sus archivos
src/vollib/
    dates.py              <- fechas (ACT/365, días hábiles, tenores)
    curves.py             <- curvas de descuento + spots FX
    deltas.py             <- convenciones de delta; delta <-> strike
    smile.py              <- SmileSlice: el smile de UN tenor
    surface.py            <- VolSurface: interpolación entre tenores
    quotes_loader.py      <- lectura y validación de los exports
    orchestrator.py       <- YAML -> superficies; pipeline bid/mid/ask
```

**Regla de oro**: cada módulo tiene una responsabilidad. Si una convención de
delta sale mal → `deltas.py`. Si el smile de un tenor no reprecia →`smile.py`.
Si la interpolación entre tenores falla → `surface.py`. Si una fecha cae mal →
`dates.py`.

---

## 3. La matemática, paso a paso

### 3.1 De los quotes a las cinco volatilidades

El manual Calypso §1.2 define los instrumentos:

```
V_butterfly = 0.5 · (V_call + V_put) − V_atm
V_reversal  = V_call − V_put
```

Con `Strangle/Fly Quotes = 2vol (CP Avg)` —las 6 superficies— el sistema se
invierte por **álgebra directa**, sin solver:

```
σ_call(Δ) = ATM + BF(Δ) + RR(Δ)/2
σ_put(Δ)  = ATM + BF(Δ) − RR(Δ)/2
```

El signo del RR no necesita caso especial: sirve igual para EUR/USD (RR < 0) que
para los cinco pares USD/EM (RR > 0).

### 3.2 De cada (delta, vol) al strike

Notación: `F` forward a la ENTREGA, `τ` plazo a la EXPIRACIÓN (ACT/365),
`Df` descuento de la divisa BASE a la entrega.

```
d1 = [ln(F/K) + σ²τ/2] / (σ√τ)          d2 = d1 − σ√τ
```

Las cuatro convenciones (delta de la call, positivo):

| | sin ajuste por prima | ajustado por prima |
|---|---|---|
| **forward delta** | `N(d1)` | `(K/F)·N(d2)` |
| **spot delta** | `Df·N(d1)` | `Df·(K/F)·N(d2)` |

El delta ajustado por prima aparece cuando la prima se paga en la divisa base
(USD en USD/MXN). Se deriva restando la prima expresada en divisa base:

```
Δ_pa = Df·N(d1) − Df_dom·(F·N(d1) − K·N(d2))/S = Df·(K/F)·N(d2)
```

usando `S = F·Df_dom/Df_base`.

**Inversión**: sin ajuste hay forma cerrada. Con ajuste hace falta root-finding, y
para la **call** con una precaución: `Δ_pa,call(K) = (K/F)·N(d2)` **no es
monótona** (vale 0 en K→0, sube, alcanza un máximo y vuelve a 0 en K→∞). Para un
delta objetivo hay DOS strikes; el que corresponde a una call OTM es el de la
rama de strikes altos. `deltas._pa_call_delta_peak_d2` localiza el máximo
resolviendo `N(d2)·σ√τ = n(d2)` y acota la búsqueda a esa rama. Sin esa
precaución, el root-finder puede devolver el strike equivocado **sin ningún
síntoma visible**. La put ajustada sí es estrictamente creciente (derivada
`[N(−d2) + n(d2)/(σ√τ)]/F > 0`) y tiene raíz única.

### 3.3 El strike del ATM — donde más se equivoca

Se pide `Δ_call + Δ_put = 0`. El exponente **cambia de signo** según la convención:

```
sin ajuste por prima :  N(d1) − N(−d1) = 0  ⟹ d1 = 0 ⟹ K = F·exp(+σ²τ/2)
ajustado por prima   :  (K/F)[N(d2) − N(−d2)] = 0 ⟹ d2 = 0 ⟹ K = F·exp(−σ²τ/2)
```

Las 6 superficies tienen `Quotes are Delta with Premium = true`, así que aplica
la segunda: **el ATM cae por debajo del forward**. Usar la primera lo pondría del
lado equivocado. Cuando `ATM Zero Straddle Last Tenor` ya venció (USD/PEN, en
`0D`), el ATM es simplemente `K = F`.

### 3.4 El eje de la superficie: call delta, no delta del quote

Los cinco puntos se colocan en el eje por su **call delta calculado**, no por su
etiqueta. Con delta ajustado por prima

```
Δ_call − Δ_put = K/F ≠ 1
```

así que un put de 25 delta **no** cae en 75. En USD/MXN a 1 año cae en 65.5, y en
18M/2Y (donde ya rige forward delta) en ~67. Asumir 75 desplazaría el punto y
deformaría la spline — y además cambiaría los puntos de extrapolación, porque la
pendiente de la punta se mide sobre las posiciones reales.

### 3.5 Extrapolación en el eje de delta

Dos puntos ad-hoc en **1.0 y 99.0 call delta**. El manual: *"multiplies the slope
of the first two points on the surface to find the volatility of the 1.0 call
delta, and/or multiplies the slope of the last two points to find the volatility
of the 99.0 call delta"*. Se usa `Up Extrap 1.0 Delta` cuando el smile sube en esa
punta y `Down Extrap 1.0 Delta` cuando baja. Más allá de 1 y 99, extrapolación
**plana** (`Interpolator3DSpline1D`).

### 3.6 Interpolación

- **En delta**: spline cúbica natural sobre los 7 puntos.
- **En plazo**: varianza total `w = σ²·t` lineal en `t`, a **delta constante**
  (`Interpolate Outright Variance = true`, `Interpolate on Trading Time = false`,
  con `t` en tiempo calendario ACT/365).
- **Consulta por strike**: es un punto fijo — el delta del strike depende de la
  vol y la vol depende del delta. Se itera desde la ATM; converge en 3-5 vueltas.

---

## 4. Referencia del YAML

```yaml
valuation_date: 2026-09-01

market_data:
  fx_spots_file: data/curves/tc.csv
  curves: {USD: ..., MXN: ..., ...}      # una por divisa que aparezca en los pares

surfaces:
  USDMXN:
    base_ccy: USD                        # base del par (la del numerador del forward)
    quote_ccy: MXN
    delivery_lag: 2                      # días hábiles de expiry a entrega
    parameters:  data/vol_quotes/par_USDMXN.csv     # panel de Calypso = fuente de verdad
    underlyings: data/vol_quotes/und_USDMXN.csv     # solo para validar la grilla
    quotes: {mid: ...}                   # o {bid: .., mid: .., ask: ..}
    overrides: {}                        # fuerza un parámetro (análisis de sensibilidad)
    vol_day_count_fallback: ACT/365      # si el panel lo trae vacío (caso EUR/USD)
```

Convención del forward, idéntica a la del Módulo 1:
`F = S · Df_base / Df_quote` (para 'USDMXN', base = USD, quote = MXN).

---

## 5. Bid / mid / ask

Pipeline completo tres veces (`build_bid_mid_ask` → `VolSurfaceSet`), el mismo
"enfoque A" del Módulo 1: el spread atraviesa la conversión delta→strike y la
spline, así que **no se puede interpolar el mid y sumarle medio spread**.

Calypso exporta un archivo por lado con la misma cabecera. El loader valida que
los tres compartan grilla de tenores y fechas de expiración antes de combinarlos.

**Advertencia sobre el ala put**: en `σ_put = ATM + BF − RR/2` el RR **resta**, así
que una superficie construida con todos los insumos en bid **no da la vol más
baja en el ala put**. Este motor implementa el *lado consistente* (todo bid con
bid), que es lo que hace Calypso al cargar el quote set BID y lo comparable contra
él. La *envolvente* (piso y techo reales por strike) es una pregunta distinta y no
está implementada — ver §7.

---

## 6. Validaciones de entrada

`quotes_loader.validate_quotes` corre antes de construir nada:

| Chequeo | Qué detecta |
|---|---|
| varianza total ATM creciente | arbitraje de calendario |
| `BF25 > 0`, `BF10 > BF25` | convexidad invertida |
| `ATM > 0` | quote corrupto o mal escalado |
| `Cal Days` == `Exp` − valuación | fecha de valuación equivocada |
| grilla de underlyings == grilla de quotes | export incompleto |
| tenores y expiries iguales entre lados | mezclar bid/ask de días distintos |

Además `curves.report_spot_precision` avisa cuando un spot llega redondeado: el
spot multiplica al forward y por tanto **fija todos los strikes**. Con el export
actual, EUR/USD (1.16) arrastra ~43 pb de incertidumbre relativa y USD/PEN (3.37)
~15 pb — ver §7.

---

## 7. Limitaciones y supuestos (leer antes de usar en validación formal)

1. **Precisión del spot.** El export de tipos de cambio viene con 2 decimales.
   Es la limitación más seria hoy: EUR/USD ±43 pb, USD/PEN ±15 pb, USD/BRL ±10 pb,
   USD/MXN ±3 pb, todo trasladado a los strikes. Para replicar a Calypso al nivel
   que se logró con curvas hace falta el spot con la precisión completa (en la
   validación de opciones anterior se usó 17.07181955, con 8 decimales).
2. **Fórmula de los puntos de extrapolación**: inferida de la prosa del manual
   (no viene como ecuación). Se valida contra la pestaña `Points` de Calypso,
   que todavía no tenemos.
3. **Posición de los puntos en el eje**: se asume que el eje es el call delta en
   la convención de la superficie. Pendiente de confirmar contra `Points`.
4. **Extrapolación en el eje de plazos**: antes del primer tenor, varianza lineal
   desde el origen (equivale a vol plana); después del último, vol plana. Supuesto
   explícito, no verificado contra Calypso.
5. **`1vol (Broker)` no implementado**: el motor exige `2vol (CP Avg)` y falla con
   mensaje claro si el panel dice otra cosa. La convención broker requiere
   calibración iterativa y —según el propio manual— *"there is no unique answer for
   the Put/Call smile given Broker strangle quotes"*.
6. **`Interpolate on Trading Time = true` no implementado**: requiere el cálculo
   ponderado de trading time (feriados, eventos, multiplicadores de cut, manual
   §1.1). Las 6 superficies están en `false`, así que no hace falta hoy.
7. **`Interpolate Outright Variance = false` (EUR/USD)**: el motor siempre
   interpola varianza total. En los tenores cotizados coincide; en fechas
   intermedias no. Se avisa al construir.
8. **Calendario de feriados**: `dates.advance_business_days` solo excluye fines de
   semana. Se usa para la fecha de entrega (expiry + lag). Un feriado no
   considerado corre la entrega un día. Enchufar `curvelib.dates` es un cambio de
   una línea.
9. **`Roll Method` no se usa**: la superficie se construye a la fecha de
   valuación; el rolling intradía (`Forward Volatility` / `Calendar Vol Constant`)
   no está implementado.
10. **BUS/252** (que el manual menciona para BRL onshore) no está soportado.

---

## 8. Hoja de ruta

1. Validar contra las pestañas `Points` y `Surface` de Calypso — cierra los
   supuestos 2, 3 y 4.
2. Conseguir los spots con precisión completa — cierra el supuesto 1.
3. Cambiar la fuente de curvas a `curvelib` vía `CurvelibAdapter` y medir el
   impacto de la curva propia sobre la superficie (mismo ejercicio Parte A/B que
   se hizo con FX Forward/NDF).
4. Enchufar el Módulo 3 (impactos) para llevar los hallazgos de configuración a
   dólares sobre el portafolio real.
5. Soporte `1vol (Broker)` si alguna superficie llega a usarlo.
