# Superficie de Volatilidad FX — Metodología de Calypso (hallazgo de referencia)

Módulo 2 del motor propio. Versión 0.3 · 02/09/2026 · **Metodología cerrada para USD/MXN**

**Fuentes:**
- Pantalla `FX Vol Qt Entry: USD/MXN FWD VOL LAST 01/09/26 16:00:00` (pestaña Definition)
  + export completo del panel de parámetros (`usdmxn.csv`).
- Manual Calypso **"FX Volatility Surfaces", Version 16.1, March 2021 — Fifth edition**
  (Adenza Proprietary and Confidential), 20 págs.
- Exports `underlying_usdmxn.csv` (60 underlyings) y `quotes_usdmxn.csv` (12 tenores).

Mismo rol que `curvas-mxn-metodologia.md` cumplió para el Módulo 1: fijar la metodología
verificada contra Calypso ANTES de escribir código.

## Conclusión central

La superficie **no usa un modelo paramétrico**. El generador `FXOption` —según el manual
§1.3.1— *"Derives the vol surface by interpolating from vanillas and strategies (ATMs,
risk reversals, and butterflies)"*: **spline cúbica natural sobre el eje de delta**,
alimentada por 5 quotes por tenor, e interpolación en **varianza total sobre tiempo
calendario ACT/365** en el eje de plazos. (Calypso ofrece un generador SABR —
`FXOptionSABR` — pero esta superficie no lo usa.)

Con `Strangle/Fly Quotes = 2vol (CP Avg)`, las alas salen por **álgebra directa**, sin
calibración iterativa. El manual (§1.2) define exactamente:

```
V_butterfly = 0.5 · (V_call + V_put) − V_atm
V_reversal  = |V_call − V_put|

⇒  σ_call(Δ) = ATM + BF(Δ) + RR(Δ)/2
   σ_put(Δ)  = ATM + BF(Δ) − RR(Δ)/2
```

Lo no trivial queda en (a) la conversión delta→strike (premium-adjusted, spot vs. forward
según tenor, ATM zero-delta straddle), que **requiere las curvas del Módulo 1**, y (b) los
dos puntos ad-hoc de extrapolación en 1.0 y 99.0 call delta.

---

## 1. Parametrización completa verificada

Pricing Env `Risk - EOD With SOFR` · FXDate `01/09/2026` · quote set `LAST`

### Definition

| Campo | Valor | Nota |
|---|---|---|
| Currency Pair | `USD/MXN` | — |
| DateRoll | `MOD_FOLLOW` | mismo BDC que ya usa `curvelib.dates` por defecto |
| Holidays | `NYC,MEX` | **el manual: "The Holidays field is not used at this time"** — se usan los calendarios de Currency Defaults |
| Pricing Env | `Risk - EOD With SOFR` | mismo entorno SOFR de las curvas ya validadas |
| Strike Spread | `Delta` | el eje del smile es DELTA |
| Interpolator | `Interpolator3DSpline1D` | spline cúbica natural + extrapolación plana más allá de los puntos de 1 delta |
| Derived | ✓ | correcto: el manual exige `Derived` para todos los generadores |
| Generator | `FXOption` | interpola desde ATM/RR/BF — el más común según el manual |

### Surface config

| Parámetro | Valor | vs. recomendación del manual |
|---|---|---|
| Granularity | `Continuous` | ✔ *"always set to Continuous"* |
| Spread Method | *(vacío)* | ✔ solo aplica a superficies `Spread_Default` |

### Quote conventions

| Parámetro | Valor | vs. recomendación |
|---|---|---|
| DateCut of Expiries | `Lima 17:00` | debe coincidir con el cut de los quotes importados |
| Volatility Day Count | `ACT/365` | ✔ *"almost always ACT/365"* |
| Quotes are Delta with Premium | `true` | ✔ correcto para USD/MXN (prima en USD, primera divisa) |
| Spot Delta Last Tenor | `1Y` | ⚠ **Hallazgo 1** — el manual sugiere `0D` para pares emergentes |
| ATM Zero Straddle Last Tenor | `10Y` | ⚠ **Hallazgo 1** — el manual sugiere `0D` para pares emergentes |
| Strangle/Fly Quotes | `2vol (CP Avg)` | ⚠ **Hallazgo 2** — el manual dice *"almost always 1vol (Broker)"* |

### Interpolation config

| Parámetro | Valor | vs. recomendación |
|---|---|---|
| Interpolate Outright Variance | `true` | ✔ recomendado. Interpola sobre **σ²·t con t en tiempo CALENDARIO** |
| Interpolate on Trading Time | `false` | ⚠ **Hallazgo 4** — el manual sugiere `true` |
| Up Extrap 1.0 Delta | `1.0` | ⚠ **Hallazgo 3** — Calypso recomienda `2.0` |
| Down Extrap 1.0 Delta | `1.0` | ⚠ **Hallazgo 3** — Calypso recomienda `0.0` |
| Weighting | `true` | ⚠ **Hallazgo 4** — activo, pero los datos prueban que no hay pesos configurados |

### Rolling config

| Parámetro | Valor | vs. recomendación |
|---|---|---|
| Roll Method | `Forward Volatility` | ✔ *"generally, Calypso recommends Forward Volatility"* |

---

## 2. Underlyings y quotes

**60 underlyings** = 12 tenores × 5 instrumentos (`ATM`, `Butterfly 25-delta`,
`Risk Reversal 25-delta`, `Butterfly 10-delta`, `Risk Reversal 10-delta`), todos `FXOpt`.
Coincide con lo que el manual (§1.3.2) llama el caso normal: *"In the vast majority of
cases, your FX vol surface underlyings will be as follows: ATMs, 25- and 10-delta risk
reversals, 25- and 10-delta butterflies"*.

Los Ids de Calypso no son correlativos por tenor (bloques 279xx/359xx, y 3051741-45 para
el 4M agregado después) — **el emparejamiento debe ser por descripción, no por Id**.

Tenores: `1W, 2W, 3W, 1M, 2M, 3M, 4M, 6M, 9M, 1Y, 18M, 2Y` (12).

> El manual pide *"at a bare minimum... O/N, 1W, 1M, 2M, 3M, 6M, and 1Y points in every
> currency pair"*. Esta superficie **no tiene O/N**. No es error (la selección de tenores
> la define la mesa según liquidez), pero queda anotado.

Formato de `quotes_usdmxn.csv` (delimitador `;`, valores en **puntos de vol**):

```
Term;Exp;Day;Cal Days;Trade Days;Trade Vol;RR25;RR10;ATM;BF25;BF10
1W;08/09/2026;TUE;7.00000;7.00000;4.72000;0.72500;1.29000;4.72000;0.24500;0.87500
```

Los expiries se cuentan **desde la fecha de valuación, no desde spot**: `Cal Days` =
`Exp` − 01/09/2026 exacto. Distinto de las curvas, donde los pilares arrancan en T+2.

### El desfase `Trade Days` − `Cal Days` queda explicado

`Trade Days` excede a `Cal Days` en **+0.042 días = +1.008 horas exactas** en 3M, 4M, 6M,
18M y 2Y. El manual (§1.1) lo explica literalmente:

> *"The raw time unit, a raw day, is a lapse of 86400 seconds... due to daylight saving
> adjustments, some 'calendar day' could have a raw time in defect or excess of 1 raw
> days... The raw time between 28 Oct 2000 at 10:00 in NYC, and 29 Oct 2000 at 10:00 in
> NYC, is 1.04166 raw days, as 90000 seconds elapsed."*

1.04166 − 1 = 0.041666 = exactamente el desfase observado. Es el cruce de un cambio de
horario entre valuación y expiry.

### Relación calendar vol ↔ trading vol, verificada numéricamente

El manual (§1.3.3) dice que *"the calendar days and calendar ATM volatility are held
constant. The system updates the trading days, and then calculates the trading day
volatility"*, pero la fórmula viene como imagen y no se extrae del PDF. La hipótesis de
**varianza total invariante** se cumple en los 12 tenores dentro del redondeo a 3
decimales:

```
σ_trading = σ_calendar · √(CalDays / TradeDays)
```

| Term | Cal | Trade | ATM (cal) | Trade Vol | Predicho | Dif |
|---|---:|---:|---:|---:|---:|---:|
| 3M | 91.000 | 91.042 | 7.540 | 7.538 | 7.5383 | −0.0003 |
| 4M | 120.000 | 120.042 | 8.037 | 8.036 | 8.0356 | +0.0004 |
| 18M | 547.000 | 547.042 | 10.068 | 10.067 | 10.0676 | −0.0006 |
| 2Y | 731.000 | 731.042 | 10.232 | 10.232 | 10.2317 | +0.0003 |

(En los 7 tenores sin desfase DST ambas columnas coinciden. Quedan residuos de ±0.001 en
2W, 3W y 6M, justo en el límite del redondeo — pendiente menor.)

---

## 3. Smile derivado de los quotes

Aplicando `σ_c = ATM + BF + RR/2` y `σ_p = ATM + BF − RR/2`:

| Term | Exp | Trade Days | ATM | σ 10P | σ 25P | σ 25C | σ 10C | ATM²·t |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1W | 08/09/2026 | 7.000 | 4.720 | 4.950 | 4.603 | 5.327 | 6.240 | 0.000043 |
| 2W | 15/09/2026 | 14.000 | 5.567 | 5.641 | 5.332 | 6.332 | 7.404 | 0.000119 |
| 3W | 22/09/2026 | 21.000 | 6.062 | 5.971 | 5.731 | 6.949 | 8.129 | 0.000211 |
| 1M | 01/10/2026 | 30.000 | 6.435 | 6.255 | 6.045 | 7.405 | 8.665 | 0.000340 |
| 2M | 29/10/2026 | 58.000 | 7.070 | 6.543 | 6.488 | 8.303 | 9.837 | 0.000794 |
| 3M | 01/12/2026 | 91.042 | 7.540 | 6.836 | 6.862 | 8.907 | 10.625 | 0.001418 |
| 4M | 30/12/2026 | 120.042 | 8.037 | 7.285 | 7.351 | 9.449 | 11.185 | 0.002124 |
| 6M | 01/03/2027 | 181.042 | 8.482 | 7.690 | 7.792 | 9.932 | 11.694 | 0.003568 |
| 9M | 01/06/2027 | 273.000 | 9.215 | 8.365 | 8.510 | 10.710 | 12.534 | 0.006351 |
| 1Y | 01/09/2027 | 365.000 | 9.720 | 8.831 | 9.001 | 11.289 | 13.139 | 0.009448 |
| 18M | 01/03/2028 | 547.042 | 10.068 | 9.223 | 9.387 | 11.655 | 13.553 | 0.015192 |
| 2Y | 01/09/2028 | 731.042 | 10.232 | 9.412 | 9.575 | 11.835 | 13.765 | 0.020969 |

Chequeos de sanidad (todos pasan): varianza total ATM estrictamente creciente (sin
arbitraje de calendario); BF25>0 y BF10>BF25 en los 12 tenores (convexidad bien formada);
RR>0 en los 12 (skew hacia USD call / MXN put). El mínimo del smile cae del lado put —
forma normal con RR fuertemente positivo, no una anomalía.

---

## 4. Las 8 preguntas de metodología — todas cerradas

| # | Pregunta | Respuesta |
|---|---|---|
| 1 | Modelo de smile | Spline cúbica natural sobre eje **delta** (`FXOption` + `Interpolator3DSpline1D`). No SABR |
| 2 | Definición de ATM | **Zero-delta straddle** en todos los tenores de esta superficie (`ATM Zero Straddle Last Tenor: 10Y` > 2Y) |
| 3 | Niveles de delta | **25D y 10D**, call y put (5 underlyings × 12 tenores = 60) |
| 4 | Convención de delta | **Premium-adjusted**; **spot delta ≤ 1Y**, **forward delta > 1Y** (18M y 2Y usan forward delta) |
| 5 | Interpolación en tenor | **Varianza total `σ²·t` sobre tiempo CALENDARIO** (`Interpolate Outright Variance = true`, `Interpolate on Trading Time = false`) |
| 6 | Extrapolación | Puntos ad-hoc en **1.0 y 99.0 call delta**, construidos extendiendo la pendiente de los dos puntos extremos × el multiplicador (`Up`/`Down Extrap = 1.0`); más allá, plana |
| 7 | Day count de expiries | **ACT/365**, con `DateCut Lima 17:00` |
| 8 | Convención de butterfly | **`2vol (CP Avg)`**: `BF = 0.5·(σc+σp) − ATM`. Álgebra directa, sin solver |

**Estructura resultante de cada slice**: 7 puntos en el eje de call delta —
`1.0` (ad-hoc), `10` (10D call), `25` (25D call), `~50` (ATM zero-delta straddle),
`75` (25D put), `90` (10D put), `99.0` (ad-hoc) — unidos por spline cúbica natural.

---

## 5. Hallazgos para revisar con la mesa / primera línea

Los cuatro salen de contrastar la configuración real contra las recomendaciones del propio
manual de Calypso. Ninguno es necesariamente un error —el manual insiste en que estas
convenciones las define la mesa— pero todos afectan niveles de la superficie, y el manual
advierte: *"You should always confirm these settings with your trading desk or your market
sources before implementing, because these inputs will significantly affect your vol
surface levels."*

### Hallazgo 1 — USD/MXN configurado con convención G10, no de mercado emergente

El manual (§1.3.1, "Recommended Settings"):

> *"Spot Delta Last Tenor... Typically this is **1Y for G10 pairs and 0D (zero days) for
> emerging market pairs**"*
> *"ATM Zero Straddle Last Tenor... Typically this is **10Y for G10 pairs and 0D for
> emerging market pairs**"*

Su tabla de ejemplo:

| CCY Pair | Spot Delta Last Tenor | ATM Zero Last Tenor | Delta w/Premium |
|---|---|---|---|
| EURUSD, GBPUSD | 1Y | 10Y | False |
| USDJPY, EURJPY | 1Y | 10Y | True |
| USDNOK, USDSEK, EURNOK | 1Y | 10Y | True |
| **USDKRW, EURKRW, EURBRL, USDBRL** | **0D** | **0D** | True |
| INRJPY | 0D | 0D | False |

La superficie USD/MXN está en **1Y / 10Y / true** — patrón G10, mientras que los pares
emergentes comparables de la tabla (KRW, BRL) están en **0D / 0D / true**. Con 0D/0D todos
los expiries usarían forward delta y el ATM sería ATM-forward; con la config actual, todos
usan spot delta hasta 1Y y zero-delta straddle en todo el rango.

**Esto cambia los strikes de todos los puntos del smile**, o sea toda la superficie.
Confirmar con la mesa si es deliberado (hay pares EM líquidos que sí se cotizan con
convención G10) o si es un default heredado. El `Delta w/Premium = true` sí es consistente
con la tabla. Es el mismo tipo de hallazgo que en curvas fue "los swaps MXN F-TIIE están
colateralizados en USD": una convención que no se ve hasta contrastarla contra la
documentación.

**Prueba directa disponible**: comparar contra las superficies de USD/BRL, USD/COP,
USD/CLP y USD/PEN. Si esas están en 0D/0D y solo MXN en 1Y/10Y, es probable que sea un
default heredado; si todas están en 1Y/10Y, es una decisión de la casa.

### Hallazgo 2 — `Strangle/Fly Quotes = 2vol (CP Avg)` vs. la recomendación

El manual (§1.3.1):

> *"Strangle/Fly quotes: this will almost always be '1vol (Broker)'. **Only set this to
> '2vol (CP Avg)' if you are sure your market data supplier uses the 2-vol convention**
> for butterflies/strangles."*

**Confirmar con la mesa o con el proveedor de data que los BF que llegan son
efectivamente 2-vol.** Si el proveedor entrega broker strangle y la superficie lo
interpreta como 2-vol, las alas quedan sistemáticamente mal, y el error crece con la
convexidad (más en 10D que en 25D).

Nota de diseño: si algún día se configura `1vol (Broker)`, el Módulo 2 sí necesitaría un
solver (el manual describe un proceso iterativo: buscar las vols call/put a los deltas
indicados que reproduzcan la prima del strangle broker), y el resultado *depende del
interpolador y de la extrapolación* — el propio manual admite que *"there is no unique
answer for the Put/Call smile given Broker strangle quotes"*.

### Hallazgo 3 — `Up`/`Down Extrap 1.0 Delta` ambos en 1.0

Calypso recomienda **Up = 2.0** (*"so the smile steepens beyond the 1-delta point"*) y
**Down = 0.0** (*"so the surface flattens out if it's heading downward, which avoids the
possibility of the vol reaching zero at very-low-delta points"*). Esta superficie tiene
**1.0 y 1.0**.

Cuantifiqué el efecto reconstruyendo los dos puntos ad-hoc según la descripción del manual
(*"multiplies the slope of the first two points... to find the volatility of the 1.0 call
delta"*):

| Term | v@1.0 (actual) | v@1.0 (recomendado) | Dif | v@99.0 (actual) | v@99.0 (recom.) | Dif |
|---|---:|---:|---:|---:|---:|---:|
| 1M | 9.421 | 10.177 | −0.756 | 6.381 | 6.507 | −0.126 |
| 3M | 11.655 | 12.685 | −1.030 | 6.819 | 6.836 | −0.017 |
| 1Y | 14.249 | 15.359 | −1.110 | 8.729 | 8.831 | −0.102 |
| 2Y | 14.924 | 16.082 | −1.158 | 9.315 | 9.412 | −0.097 |

Dos lecturas:

- **El riesgo que advierte el manual con `Down` ≠ 0 no se materializa**: la vol en 99.0
  call delta se queda en 6.8–9.3%, lejos de cero. El ala put es poco pronunciada.
- **El efecto real está en el ala call**: con `Up = 1.0` en vez de 2.0, la vol en 1.0 call
  delta queda entre **0.76 y 1.16 puntos de vol por debajo** de la configuración
  recomendada. O sea, el ala de USD calls muy OTM es más plana.

**Conexión con la validación anterior**: en `validacion-opciones-fx-usdmxn.md`, las peores
diferencias relativas contra Calypso se concentraban consistentemente en *"el mismo puñado
de opciones muy OTM (strikes 20.9–23.2 vs. spot ~17.07)"*. Esos strikes están 22–36% por
encima del spot — es decir, **en la zona extrapolada del ala call**, justo donde este
parámetro manda. Vale la pena testear si hay relación cuando el Módulo 2 esté corriendo.

> Advertencia metodológica: la fórmula exacta de los puntos ad-hoc la inferí de la prosa
> del manual (no viene como ecuación), asumiendo extensión lineal de la pendiente en el
> eje de call delta y el ATM en 50. La pestaña `Points` de Calypso lo confirmaría exacto.

### Hallazgo 4 — `Weighting = true` pero sin pesos configurados

El parámetro está en `true`, pero los datos prueban que **no se está aplicando ningún
peso**: `Trade Days` = `Cal Days` salvo el ajuste de DST. Con pesos de fin de semana
activos, 7 días calendario darían ~5 días de trading, no 7.000. El manual explica el
mecanismo: *"An empty weight will be assumed to be 0"*, y con peso 0 un fin de semana
cuenta como 2×(1−0) = 2 días completos, o sea no descuenta nada.

Es decir: **el flag está encendido pero la tabla de pesos de USD/MXN está vacía**. Alguien
que lea solo el parámetro puede creer que se están ponderando fines de semana y feriados
cuando no es así.

Se combina con `Interpolate on Trading Time = false` (el manual sugiere `true`): la
interpolación usa tiempo calendario, no trading time. Hoy da igual porque ambos coinciden;
**pero si algún día se configuran pesos reales, el trading time afectaría la columna de
trading vol y NO la interpolación** — una inconsistencia latente que conviene dejar
documentada.

Para el Módulo 2 esto es una simplificación grande: **no hay que implementar el cálculo
ponderado de trading time del §1.1** (feriados, eventos, multiplicadores de cut), que es
la parte más pesada del manual. Basta tiempo calendario ACT/365 con ajuste de DST.

---

## 6. Qué falta para empezar a codear

1. **Pestañas `Points` y `Surface`** de la ventana: son el equivalente al `Df Mid` que se
   usó para validar las curvas pilar por pilar. Sin ellas solo podemos reproducir los
   quotes de entrada, no validar la superficie interpolada ni confirmar la fórmula exacta
   de los puntos ad-hoc de extrapolación (Hallazgo 3).
2. **Spot USD/MXN y curvas del 01/09/2026** (entorno `Risk - EOD With SOFR`): dependencia
   dura del Módulo 2 sobre el Módulo 1 para la conversión delta→strike.
3. **Confirmar qué significa `FWD VOL`** en el nombre de la superficie.
4. Las otras cinco superficies (USD/PEN, USD/BRL, USD/CLP, USD/COP, EUR/USD) — no son
   bloqueantes para USD/MXN, pero permiten juzgar el Hallazgo 1.

## 7. Implicaciones de diseño para `vollib`

- El `SmileSlice` guarda 7 puntos `(call_delta, σ)` por tenor: 1.0, 10, 25, ~50, 75, 90,
  99.0 — los cinco de mercado más los dos ad-hoc de extrapolación.
- **La calibración por tenor no necesita solver** (con `2vol (CP Avg)`): es álgebra
  directa. El `least_squares` que preveía el plan no hace falta — el motor del Módulo 2
  es mucho más liviano que el del Módulo 1. Si algún día se configura `1vol (Broker)`,
  ahí sí entra un solver (Hallazgo 2).
- Interpolación: spline cúbica natural en el eje de delta; **varianza total `σ²·t` sobre
  tiempo calendario ACT/365** en el eje de plazos.
- El trabajo fino está en la **conversión delta→strike**: premium-adjusted, con el corte
  spot→forward delta en 1Y y el ATM zero-delta straddle. Es donde se concentran los
  errores típicos de replicación, y es lo que ata el Módulo 2 al Módulo 1.
- **No hace falta implementar el trading time ponderado** (Hallazgo 4); sí conviene dejar
  el gancho en el diseño por si algún día se cargan pesos.
- Los parámetros del Hallazgo 1, 2 y 3 deben ser **configurables en el YAML**, no
  hardcodeados: son exactamente los que cambian entre pares y los que habrá que mover para
  hacer análisis de sensibilidad frente a Calypso.
- El `quotes_loader` del Módulo 2 parsea `Term;Exp;Day;Cal Days;Trade Days;Trade Vol;
  RR25;RR10;ATM;BF25;BF10` (delimitador `;`, valores en puntos de vol) y el panel de
  parámetros `Parameter;Value` — formatos distintos del `Quote Name,Type,BID,MID,ASK` del
  Módulo 1. Son loaders separados.

---

## 8. Soporte bid / mid / ask

Los quotes recibidos hasta ahora (`quotes_usdmxn.csv`) son de **un solo lado (mid)**, pero
el módulo debe aceptar bid y ask y correr el proceso completo igual — mismo requisito que
ya cumple el Módulo 1.

### 8.1 Diseño: tres pasadas completas (mismo "enfoque A" del Módulo 1)

`curvelib` corre el pipeline entero tres veces (`build_bid_mid_ask` → `CurveSet`), porque
el spread bid/ask se propaga de forma no lineal por la construcción. En la superficie pasa
lo mismo: el spread atraviesa la conversión delta→strike y la spline, así que **no se puede
interpolar el mid y sumarle medio spread al final**. El espejo directo:

| Módulo 1 (`curvelib`) | Módulo 2 (`vollib`) |
|---|---|
| `quote: 0.0393` o `quote: {bid, mid, ask}` | `atm: 6.435` o `atm: {bid, mid, ask}` (ídem `rr25`, `rr10`, `bf25`, `bf10`) |
| `build_bid_mid_ask(config)` → `CurveSet` | `build_bid_mid_ask(config)` → `VolSurfaceSet` |
| `cs.sides["bid"]["USD_SOFR"]` | `vs.sides["bid"]["USDMXN"]` |
| `cs.table(name)` → Zero/Df × 3 lados | `vs.table(pair)` → σ por delta × 3 lados |

Igual que en el Módulo 1: si falta un lado, cae a `mid` y luego al primer valor disponible,
para que tenores con y sin bid/ask conviyan sin romper nada.

### 8.2 ⚠ El Risk Reversal entra con signo negativo en el ala put

Este es el punto que hay que decidir explícitamente, porque produce errores silenciosos.
En `σ_put = ATM + BF − RR/2` el RR resta. Entonces **una "superficie bid" construida con
todos los insumos en bid NO da la vol más baja en el ala put**: el bid del RR, al restar,
la sube.

Ilustración con el 1Y real y spreads hipotéticos (ATM ±0.10, BF25 ±0.05, RR25 ±0.15):

| Construcción | σ 25C | σ 25P |
|---|---:|---:|
| **(A)** lado consistente — bid con bid | 11.064 | 8.926 |
| **(A)** lado consistente — mid | 11.289 | 9.001 |
| **(A)** lado consistente — ask con ask | 11.514 | 9.076 |
| **(B)** envolvente real — piso | 11.064 | **8.776** |
| **(B)** envolvente real — techo | 11.514 | **9.226** |

El ala put de la "superficie bid" bajo (A) da 8.926, pero el piso real es 8.776: **0.15
puntos de vol de diferencia**, exactamente el spread del RR.

Las dos lecturas son legítimas y responden preguntas distintas:

- **(A) Lado consistente**: "la superficie implícita en las cotizaciones bid". Es lo que
  hace `curvelib` y lo que replicaría Calypso al cargar el quote set BID. **Es la que
  recomiendo por defecto**, por consistencia con el Módulo 1 y porque es lo comparable
  contra Calypso.
- **(B) Envolvente**: "el rango de vol posible en cada strike". Sirve para reservas de
  valorización o cotas de incertidumbre, no para replicar Calypso.

Propuesta: implementar (A) como comportamiento por defecto, y dejar (B) disponible como
un modo aparte (`envelope=True`) claramente documentado, en vez de esconder la decisión.
En el Módulo 3 esto importa: una banda de NPV construida con (A) es más angosta que con
(B), y hay que saber cuál se está reportando.

### 8.3 ⚠ La pantalla dice `LAST`, no `MID`

El selector de quote set de la pantalla (junto al Name) muestra **`LAST`**, y los quotes
enviados son mid. Puede que en esta instalación `LAST` sea el valor marcado que se usa
como mid, pero **hay que confirmarlo**: si la superficie de producción se genera con
`LAST` y nosotros construimos con `MID`, estaríamos comparando dos cosas distintas y
cualquier diferencia contra Calypso sería un artefacto del quote set, no del motor. Es
el mismo tipo de detalle que en curvas fue usar `PV` y no `NPV` de Calypso.

### 8.4 Decisión pendiente: ¿qué lado de las curvas usa cada lado de la superficie?

La conversión delta→strike necesita el forward, que sale de las curvas del Módulo 1 — que
también tienen tres lados. Dos opciones:

- **Lado consistente extremo a extremo**: superficie bid + curvas bid. Es el escenario
  "todo bid" coherente, y sigue el precedente del Módulo 1.
- **Curvas mid siempre**: aísla el efecto del spread de volatilidad puro, sin mezclarlo
  con el spread de tasas.

Recomiendo que sea **un parámetro del YAML** (`curve_side: same | mid`), con `same` por
defecto, porque las dos preguntas se van a hacer en algún momento y no cuesta soportarlas.

### 8.5 Formato del export por lado — confirmado

BID y ASK salen de Calypso con **exactamente la misma cabecera** que el export mid:

```
Term;Exp;Day;Cal Days;Trade Days;Trade Vol;RR25;RR10;ATM;BF25;BF10
```

O sea: **un archivo por lado**, mismo layout. El loader recibe hasta tres rutas
(`quotes_bid`, `quotes_mid`, `quotes_ask`) y arma internamente la forma
`{bid, mid, ask}` por instrumento y tenor. Un solo archivo se lee como "los tres lados
iguales", igual que un `quote:` escalar en el YAML del Módulo 1.

Consecuencia de diseño: como cada lado viene en su propio archivo, el loader debe
**validar que los tres comparten la misma grilla de tenores y las mismas fechas de
expiry** antes de combinarlos. Si un lado trae un tenor de más o de menos (o un expiry
corrido), combinarlos en silencio produciría un smile mezclando fechas distintas. Es el
mismo tipo de control que en el Módulo 1 hace `apply_quotes_sheet` al reportar los quotes
sin emparejar.
