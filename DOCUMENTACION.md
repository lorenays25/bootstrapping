# Documentación — Sistema de Bootstrapping Multi-Curva

Versión 0.4.0 · Esqueleto funcional para las 28 curvas definidas en el proyecto.

> **Cambios v0.4.0** — interfaz web conectada al motor vía microservicio
> FastAPI (`server.py`): construir bid/mid/ask y ver/descargar las tablas
> desde el navegador (ver sección 12).
>
> **Cambios v0.3.0** — soporte bid/mid/ask (ver sección 11): tipo `mm`
> (depósito) nuevo para el pilar corto; los quotes aceptan la forma
> `{bid, mid, ask}`; pipeline `build_bid_mid_ask` que corre tres bootstraps
> completos (enfoque A) sobre las 28 curvas; conversor de tasa cero anual
> ACT/360 (`zero_rate_annual`); loader de hoja de quotes (`quotes_loader`)
> que parsea el formato de pantalla e inyecta valores en el YAML; generador
> de tabla de output estilo pantalla (`CurveSet.table` / `.to_csv`) con
> columnas Date/Offset/Zero·3/Df·3; y carga de CSV de quotes desde la
> interfaz HTML.
>
> **Cambios v0.2.0** (verificados contra capturas de convenciones de first
> line): se agregaron los instrumentos `fra` y `future` para la parte corta
> de curvas IBOR (Euribor 3M con futuros STIR, Euribor 6M con FRAs); se
> agregó el day count `30E/360` (pata fija EUR, distinto de `30/360` US); se
> generalizó la frecuencia de schedules para aceptar periodicidades
> explícitas tipo `4W` (TIIE 28D paga cada 4 semanas exactas, no mensual
> calendario); y se corrigió la regla de cupón cero (`ZC`, tenor ≤1Y) para
> que aplique solo a OIS y no a curvas IBOR. Detalle completo en las
> secciones 4.7 y 8.

---

## 1. Qué es este sistema

Es un motor de **bootstrapping de curvas de descuento y proyección** parametrizable por configuración. A partir de quotes de mercado (OIS swaps, puntos forward de FX, cross-currency swaps, IBOR swaps, basis swaps) construye curvas de factores de descuento que **reprecian exactamente** cada quote de entrada.

El diseño sigue el enfoque acordado:

- **Híbrido QuantLib + scipy**: QuantLib se usa *solo* para aritmética de fechas (calendarios, day counts, schedules). Todo el motor financiero (repricing, root-finding, solver global) es scipy/numpy propio, transparente y modificable.
- **Parametrización declarativa**: las 28 curvas viven en `config/curves.yaml`. Agregar una curva nueva no requiere tocar código.
- **DAG de dependencias**: el orquestador resuelve automáticamente el orden de construcción (primero USD SOFR, luego las cross que dependen de ella, etc.).

---

## 2. Instalación y ejecución

```bash
pip install -r requirements.txt        # QuantLib, scipy, numpy, pyyaml
python examples/run_example.py         # construye las 28 curvas y muestra diagnósticos
```

Uso desde tu propio código:

```python
import sys, datetime
sys.path.insert(0, "src")
from curvelib.orchestrator import build_from_file

curves = build_from_file("config/curves.yaml")

usd = curves["USD_SOFR"]
usd.df(datetime.date(2031, 7, 2))      # factor de descuento
usd.zero(datetime.date(2031, 7, 2))    # tasa cero continua
usd.fwd(d1, d2, "ACT/360")             # forward simple entre dos fechas
```

---

## 3. Arquitectura: módulos y responsabilidades

```
config/curves.yaml          ← LA parametrización: 28 curvas, quotes, convenciones
ui/parametrizador.html      ← interfaz visual para editar el YAML
src/curvelib/
    dates.py                ← fechas (única capa que toca QuantLib)
    curve.py                ← objeto Curve: nodos DF + interpolación log-lineal
    instruments.py          ← lógica financiera: model_quote() de cada instrumento
    engine.py               ← solvers: secuencial (Brent) y global (Levenberg-Marquardt)
    orchestrator.py         ← lee YAML, ordena el DAG, construye todo
examples/run_example.py     ← corrida end-to-end con diagnósticos
```

**Regla de oro del diseño**: cada módulo tiene UNA responsabilidad.
Si quieres cambiar la interpolación → `curve.py`. Si un quote no reprecia →
`instruments.py`. Si el orden de construcción falla → `orchestrator.py`.
Si una fecha cae mal → `dates.py`.

### 3.1 `dates.py` — fechas

Envuelve QuantLib. Expone: `get_calendar` (acepta calendarios conjuntos como
`[US, PE]`), `year_fraction`, `add_tenor`, `spot_date`, `make_schedule`.

> Perú y Colombia no existen en QuantLib → fallback a "solo fines de semana".
> En producción debes cargar los feriados locales (una línea por feriado con
> `calendar.addHoliday`). Está señalado en el código.

### 3.2 `curve.py` — el objeto curva

Una curva es una lista de nodos `(t_i, DF_i)` con **interpolación lineal en
log(DF)** (equivale a forwards instantáneas constantes por tramo). Ventajas:
DF siempre positivo, estabilidad local del bootstrap, estándar de mercado para
descuento OIS. La extrapolación mantiene constante la última forward.

Métodos clave: `df(fecha)`, `zero(fecha)`, `fwd(d1, d2, day_count)`,
`add_node`, `set_node`.

### 3.3 `instruments.py` — el corazón financiero

Cada instrumento implementa `model_quote(ctx)`: se reprecia con las curvas
actuales y devuelve su quote de modelo. El residual es
`model_quote − market_quote`, y el motor busca el DF que lo anula.

El `CurveContext` es un diccionario de curvas ya construidas + spots FX;
los instrumentos referencian curvas **por nombre** (inyectado desde el YAML),
lo que hace todo parametrizable.

### 3.4 `engine.py` — el solver

- **`sequential`**: instrumentos ordenados por madurez; para cada uno se
  agrega un nodo y se resuelve su log(DF) con Brent (bracket auto-expansivo).
  Al final hay un *repricing check*: si algún residual > 1e-8, lanza error.
- **`global`**: todos los nodos como vector de incógnitas, resuelto con
  `scipy.optimize.least_squares` (LM). Úsalo cuando los instrumentos tienen
  dependencia cruzada entre pilares o solapamiento de tenores.

La variable de optimización es log(DF) ⇒ positividad garantizada.

### 3.5 `orchestrator.py` — el DAG

`topological_order` ordena las curvas según `depends_on` con detección de
ciclos. `build_all` las construye en orden e imprime el progreso.

---

## 4. Matemática de cada instrumento

Notación: `DF(t)` factor de descuento, `τ_i` fracción de año del período i,
`t0` fecha spot, `T` madurez, `annuity = Σ τ_i · DF(t_i)`.

### 4.1 `ois_swap` — OIS (SOFR, ESTR, SONIA, SARON, TONAR, CORRA, TIBO, IBR, Cámara)

Pata fija (anual; **cupón cero si tenor ≤ 1Y** — convención confirmada en
pantalla para SOFR: `Freq: ZC` a 3M vs `Freq: PA` a 6Y) contra flotante
overnight compuesta, self-discounting. Esta regla de cupón cero es
**exclusiva de las OIS**; las curvas IBOR (`ibor_swap`, sección 4.6) pagan
siempre en su periodicidad nativa (`fixed_freq`) sin importar el tenor —
confirmado con TIIE 28D, que paga cada 4 semanas incluso en swaps cortos. La pata flotante compuesta **telescopia**:
su PV es `DF(t0) − DF(T)`. Condición par:

```
R · Σ τ_i DF(t_i) = DF(t0) − DF(T)
⇒ R_model = (DF(t0) − DF(T)) / annuity
```

### 4.2 `fx_forward` — puntos forward

Cotización: `F = spot + puntos / points_factor` (o `quote_type: outright`).
Paridad cubierta en la medida del colateral:

```
F = S · DF_base(T) / DF_quote(T)        (par 'USDPEN': base=USD, quote=PEN)
```

- `solve_for: quote_ccy` → la incógnita es la moneda cotizada
  (curvas PEN/COP/CLP/BRL/MXN/JPY/CHF/CAD/SEK coll. SOFR, CNH offshore).
- `solve_for: base_ccy` → la incógnita es la base
  (**USD implícita TIBO**: se conoce PEN TIBO, se despeja el DF USD; también
  EUR y GBP, cuyos pares se cotizan EURUSD/GBPUSD con la divisa como base).

### 4.3 `xccy_fixed_float` — NDS LatAm (fija local vs USD SOFR)

Con colateral USD y notional constante, la pata USD flotante + intercambios
de notional vale par; entonces la pata fija local debe valer par **en la
curva local colateralizada** `DF_x`:

```
R · Σ τ_i DF_x(t_i) + DF_x(T) = DF_x(t0)
⇒ R_model = (DF_x(t0) − DF_x(T)) / annuity
```

(La misma forma que un bono a la par.) Es la ecuación estándar de los NDS
PEN/COP/CLP/BRL fija-local vs SOFR.

### 4.4 `xccy_basis` — G10 (flotante RFR local + basis vs USD SOFR)

Notional constante. La pata local (con intercambio de notional) a valor par:

```
DF_x(t0) = Σ (fwd_i + b) τ_i DF_x(t_i) + DF_x(T)
⇒ b_model = [DF_x(t0) − DF_x(T) − Σ fwd_i τ_i DF_x(t_i)] / Σ τ_i DF_x(t_i)
```

donde `fwd_i` se proyecta de la curva RFR local **ya construida**
(`projection: EUR_ESTR`, `JPY_TONAR`, etc.) y `DF_x` es la incógnita.

### 4.5 `tenor_basis` — Fed Funds vs SOFR

Incógnita: curva de proyección FF. Descuento y pata base: SOFR.

```
Σ (fwdFF_i + s) τ_i DF_d = Σ fwdSOFR_i τ_i DF_d
⇒ s_model = Σ (fwdSOFR_i − fwdFF_i) τ_i DF_d / Σ τ_i DF_d
```

### 4.6 `ibor_swap` — Euribor 3M/6M, STIBOR 3M, TIIE 28d

Fija vs flotante IBOR. Proyección: la curva incógnita. Descuento: la curva
`discount` del YAML (ESTR para Euribor; self para TIIE y STIBOR).

```
R_model = Σ fwd_j τ_j DF_d(t_j) / Σ τ_i^fijo DF_d(t_i)
```

### 4.7 `fra` / `future` — parte corta de curvas IBOR (Euribor, TIIE, STIBOR...)

Confirmados en pantalla: Euribor 3M arma su parte corta con **futuros STIR**
(ICE 3M Euribor, tiras trimestrales) y Euribor 6M con **FRAs** (notación
`start x end`, p.ej. `1Mx7M`); en ambos casos el swap toma el relevo recién
en tenores largos (2Y-3Y en adelante). Misma ecuación de calibración para
ambos — solo cambia la unidad del quote:

```
fwd_model(start, end) = tasa_mercado − convexidad
```

- **`fra`**: `tenor: "1Mx7M"` (offset de inicio × offset de fin desde spot),
  `quote` en tasa decimal.
- **`future`**: mismo esquema de fechas, `quote_convention: price` por
  defecto (Bloomberg: `100 − tasa`), con `convexity_bp` opcional (default 0)
  para ajustar manualmente la diferencia futuro-forward en tenores largos.

> **Limitación documentada**: el ajuste de convexidad de futuros no se
> calcula analíticamente en este esqueleto (requiere un modelo de tasas
> tipo Hull-White). Se expone como override manual `convexity_bp` para que
> lo alimentes desde tu propio modelo cuando el tenor lo amerite (>2Y).

### 4.8 `uvr_swap` — descuento de flujos UVR

Swap UVR vs IBR: la pata IBR nominal vale par sobre COP_OIS_IBR (construida
antes, por eso `depends_on: [COP_OIS_IBR]`); la pata fija real debe valer par
sobre la curva de descuento UVR incógnita:

```
R_uvr · Σ τ_i DF_uvr(t_i) + DF_uvr(T) = DF_uvr(t0)
```

El spread entre la cero COP nominal y la cero UVR real es la inflación
breakeven implícita.

---

## 5. Referencia del YAML

```yaml
valuation_date: 2026-07-02          # fecha de valuación (ISO)

market_data:
  fx_spots: {USDPEN: 3.55, ...}     # spots outright por par

curves:
  NOMBRE_CURVA:
    mode: sequential | global       # solver
    discount: self | OTRA_CURVA     # semántica de descuento
    projection: self | OTRA_CURVA   # curva de proyección de forwards
    other_leg: OTRA_CURVA           # (fx_forward) curva de la otra moneda
    depends_on: [CURVA_A, ...]      # deben construirse antes (DAG)
    internal_day_count: ACT/365F    # coordenada temporal interna (opcional)
    conventions:                    # heredadas por todos los instrumentos
      calendar: US | [US, PE] | ... # o lista => calendario conjunto
      day_count: ACT/360 | ACT/365 | 30/360 | 30E/360 | ACT/ACT
      float_day_count: ...          # si la pata flotante usa otro day count
      spot_lag: 2                   # días hábiles a spot
      fixed_freq: A | S | Q | M | 4W  # frecuencia pata fija (tenor explícito
                                       # tipo '4W' para periodicidades no
                                       # calendario, p.ej. TIIE 28D)
      float_freq: Q | M | S | 4W    # frecuencia pata flotante
      quote_convention: rate|price  # (fra/future) unidad del quote
      convexity_bp: 0               # (future) ajuste manual de convexidad
      fx_pair: USDPEN               # (fx_forward)
      solve_for: quote_ccy|base_ccy # (fx_forward) cuál moneda es la incógnita
      points_factor: 10000          # (fx_forward) divisor de los puntos
      quote_type: points|outright   # (fx_forward) default: points
    instruments:
      - {type: ois_swap, tenor: 5Y, quote: 0.0348}
      # cualquier clave extra en el instrumento SOBREESCRIBE la convención:
      - {type: fx_forward, tenor: 1M, quote: 24, points_factor: 10000}
```

**Unidades**: tasas y basis en **decimal** (3.48% → `0.0348`; −15 bp →
`-0.0015`). Puntos forward en puntos (se dividen entre `points_factor`).

**Roles de curva por tipo de instrumento**:

| Tipo | target (incógnita) | usa `projection` | usa `discount` | usa `other_leg` |
|---|---|---|---|---|
| ois_swap | la curva misma | — | self | — |
| ibor_swap | proyección IBOR | — | sí (ESTR o self) | — |
| fra / future | proyección IBOR (`target`) | — | — | — |
| fx_forward | según `solve_for` | — | — | sí |
| xccy_fixed_float | curva coll. local | — | — | — |
| xccy_basis | curva coll. local | sí (RFR local) | — | — |
| tenor_basis | proyección FF | sí (SOFR) | sí (SOFR) | — |
| uvr_swap | descuento UVR | — | — | — |

---

## 6. Cómo agregar una curva nueva (ejemplo)

Supón una curva NOK colateralizada en SOFR. En `curves.yaml` (o en la
interfaz) agregas:

```yaml
  NOK_X_SOFR:
    mode: sequential
    discount: self
    projection: NOK_RFR             # si el basis se cotiza vs un RFR local
    other_leg: USD_SOFR
    depends_on: [USD_SOFR, NOK_RFR]
    conventions: {calendar: [US, WEEKENDS], day_count: ACT/360, spot_lag: 2,
                  float_freq: Q, fx_pair: USDNOK, solve_for: quote_ccy,
                  points_factor: 10000}
    instruments:
      - {type: fx_forward, tenor: 3M, quote: ...}
      - {type: xccy_basis, tenor: 5Y, quote: ...}
```

y el spot `USDNOK` en `market_data.fx_spots`. Nada más: el orquestador la
inserta en el DAG automáticamente.

---

## 7. La interfaz de parametrización (`ui/parametrizador.html`)

Abre el archivo en cualquier navegador (doble clic; no necesita servidor).

- **Panel izquierdo**: FX spots editables y las curvas agrupadas por **nivel
  del DAG** (N0 autónomas → N3 implícitas), calculado en vivo desde
  `depends_on`. Es tu mapa de dependencias.
- **Editor central**: modo, descuento, proyección, otra pata, dependencias
  (chips), convenciones y la tabla de instrumentos (tipo, tenor, quote y
  overrides).
- **Botones**: `Importar YAML` (carga tu archivo), `Ver YAML` (previsualiza y
  copia), `Descargar curves.yaml` (exporta el archivo listo para el motor).

Flujo típico: abres la interfaz → editas quotes/convenciones → descargas
`curves.yaml` → lo colocas en `config/` → corres el motor.

> La interfaz edita la *configuración*; no ejecuta el bootstrapping (eso es
> Python). Conectar ambos vía un pequeño servidor Flask es una mejora natural.

---

## 8. Simplificaciones del esqueleto (léelas antes de producción)

Este es un esqueleto arquitectónico correcto pero con simplificaciones
deliberadas y documentadas:

1. **Quotes de ejemplo**: todos los quotes del YAML son ficticios (niveles
   plausibles). Reemplázalos con tu data real.
2. **Calendarios PE/CO**: fallback a fines de semana; cargar feriados reales.
3. **XCCY con notional constante**: sin resets mark-to-market del notional
   (los MtM XCCY requieren un término adicional por período).
4. **OIS pata flotante telescópica**: exacta para composición o/n estándar;
   ignora payment lag ≠ 0 en el compounding y turns de fin de año.
5. **Sin meeting dates / turn-of-year jumps**: la parte corta no modela
   saltos por reuniones de bancos centrales.
6. **TIIE 28d**: frecuencia mensual como aproximación de los 28 días exactos.
7. **UVR**: pata fija real a par contra IBR nominal; sin estacionalidad de
   CPI ni lag de indexación.
8. **Sin convexidad** (futures no incluidos aún como tipo de instrumento).
9. **Interpolación única** (log-lineal DF). Agregar monotone cubic /
   tension splines es un cambio localizado en `curve.py`.
10. **Sensibilidades**: no incluidas. El diseño es compatible con JAX para
    jacobiano analítico y delta ladders (siguiente fase acordada).

---

## 9. Errores comunes y cómo leerlos

| Mensaje | Causa típica | Solución |
|---|---|---|
| `No se pudo encerrar la raíz...` | Quote en unidades equivocadas (% en vez de decimal, factor de puntos mal) | Revisa unidades del quote |
| `Dos instrumentos con el mismo pilar` | Dos tenores caen en la misma fecha ajustada | Elimina uno o usa `mode: global` |
| `La curva 'X' no está construida todavía` | Falta declarar `depends_on` | Agrega la dependencia |
| `Dependencia circular` | A depende de B y B de A | Rompe el ciclo o usa un solve global conjunto (mejora futura) |
| `Repricing check falló` | El solver convergió a tolerancia insuficiente o hay inconsistencia entre instrumentos | Revisa quotes solapados; prueba `mode: global` |
| `Falta el spot FX 'PAR'` | fx_pair sin spot en market_data | Agrega el spot |

---

## 10. Hoja de ruta sugerida (siguientes fases)

1. Cargar feriados reales PE/CO y validar convenciones LatAm contra term sheets.
2. Reemplazar quotes de ejemplo por data real y validar contra tu fuente
   (Bloomberg/Refinitiv) curva por curva.
3. XCCY mark-to-market notional para G10.
4. Turns y meeting dates en la parte corta de las RFR.
5. Sensibilidades: jacobiano analítico con JAX → delta ladder por quote.
6. Módulo de inflación propiamente dicho para UVR (estacionalidad + lag).
7. Conectar la interfaz con el motor vía un microservicio (Flask/FastAPI)
   para bootstrapping en vivo desde el navegador.

---

## 11. Bid / Mid / Ask y hoja de quotes (v0.3.0)

### 11.1 Formato de quote por lado

Un instrumento acepta el quote de dos formas:

```yaml
- {type: ois_swap, tenor: 5Y, quote: 0.0393}                     # escalar (3 lados iguales)
- {type: ois_swap, tenor: 5Y, quote: {bid: 0.0393, mid: 0.0394, ask: 0.0395}}
```

Si falta un lado en la forma dict, cae a `mid` y luego al primer valor
disponible — así los instrumentos sin bid/ask conviven con los que sí tienen.

### 11.2 Tipo `mm` (depósito / money-market)

Pilar corto de la curva (p.ej. el quote `MM.USD.SOFR.ON`). Interés simple:

```
DF(pillar) = DF(start) / (1 + R · τ)
```

`ON` arranca en la fecha de valuación; `TN` en spot−1; el resto en spot.

### 11.3 Pipeline de tres bootstraps (enfoque A)

```python
from curvelib.orchestrator import load_config, build_bid_mid_ask
config = load_config("config/curves.yaml")
cs = build_bid_mid_ask(config)      # corre bid, mid y ask completos
```

`cs` es un `CurveSet`: `cs.sides["bid"]["USD_SOFR"]`, etc. Cada lado es un
bootstrap independiente y completo del DAG — el spread bid/ask se propaga de
forma no lineal por la construcción secuencial, que es lo correcto.

### 11.4 Tabla de output (estilo pantalla)

```python
rows = cs.table("USD_SOFR", zero_day_count="ACT/360")
cs.to_csv("USD_SOFR", "output_usd_sofr.csv")
```

Columnas: `Date, Offset, Zero Bid/Mid/Ask, Df Bid/Mid/Ask`.
- **Offset**: días calendario desde la valuación hasta el pilar.
- **Zero**: tasa cero ANUAL compuesta bajo ACT/360 (método
  `zero_rate_annual`), la convención que muestra la pantalla — distinta de la
  tasa continua interna `zero()`. El DF es invariante; solo cambia la
  representación de la tasa.

### 11.5 Cargar la hoja de quotes (formato pantalla)

El módulo `quotes_loader` separa CONVENCIÓN (YAML) de PRECIO (hoja diaria):

```python
from curvelib.quotes_loader import apply_quotes_sheet
config, avisos = apply_quotes_sheet(
    config, open("quotes.csv").read(),
    curve_map={"SOFR": "USD_SOFR"},   # índice -> curva del YAML
    rate_scale=0.01,                  # la hoja viene en %
)
```

Formato del CSV:

```
Quote Name,Type,BID,MID,ASK
MM.USD.SOFR.ON.LIBOR01,Yield,3.66000,3.66000,3.66000
Swap.5Y.USD.SOFR.1D/1Y.LIBOR01,Yield,3.93579,3.93925,3.94271
```

El `Quote Name` se parsea `Tipo.Tenor.Divisa.Índice…` (MM se lee
`MM.Divisa.Índice.Tenor`). Prefijos: MM→mm, Swap/IRS→ois_swap|ibor_swap,
FRA→fra, Fut→future, FX/Fwd→fx_forward, XCCY→xccy_basis. El emparejamiento
con el YAML es por (curva destino vía índice, tenor). Los quotes no
emparejados se reportan en `avisos` sin abortar (usa `strict=True` para que
falle).

### 11.6 En la interfaz HTML

Botón **"Cargar quotes (CSV)"**: pega o sube el CSV, se muestra el formato
esperado, y "Aplicar al YAML" inyecta los bid/mid/ask en los instrumentos
(reporta cuántos emparejaron). Luego "Descargar curves.yaml" exporta el YAML
ya con los quotes por lado, listo para `build_bid_mid_ask`.

> Nota: la interfaz edita/inyecta la configuración; el pipeline de tres
> bootstraps corre en Python. Conectar ambos vía un microservicio para ver
> las tablas de output en el navegador es la mejora natural siguiente.

---

## 12. Interfaz web conectada al motor (v0.4.0)

El microservicio `server.py` (FastAPI) conecta la interfaz HTML con el motor
de bootstrapping, cerrando el ciclo completo en el navegador: cargar quotes
→ construir bid/mid/ask → ver y descargar las tablas.

### 12.1 Arrancar

```bash
pip install -r requirements.txt
python server.py
# http://127.0.0.1:8000
```

El servidor sirve la propia interfaz en la raíz (mismo origen: sin problemas
de CORS ni de archivos locales) e inyecta una marca que habilita el botón
"▶ Construir curvas".

### 12.2 Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| GET | `/` | Sirve la interfaz (con server-mode activado) |
| GET | `/config` | Devuelve el `curves.yaml` por defecto como JSON |
| POST | `/apply-quotes` | Inyecta una hoja de quotes CSV en una config |
| POST | `/build` | Construye las 28 curvas bid/mid/ask; devuelve tablas |
| POST | `/export-csv` | Devuelve la tabla de una curva como CSV descargable |

`/build` construye cada curva de forma tolerante: si una falla (p.ej. por un
quote inconsistente que editaste), reporta su error en `errors` sin abortar
las demás — así ves qué curva quedó mal sin perder el resto.

### 12.3 Flujo en la interfaz

1. Se carga la configuración (del servidor si está disponible, o la
   embebida). Editas curvas, convenciones y quotes como siempre.
2. Opcional: "Cargar quotes (CSV)" inyecta bid/mid/ask desde tu hoja.
3. "▶ Construir curvas" envía la config al motor y abre el panel de
   resultados con la tabla de la curva (Date/Offset/Zero·3/Df·3), con
   selector de curva y descarga a CSV. La columna MID va resaltada.

### 12.4 Nota de arranque en entornos restringidos

La lógica del servidor se valida con el `TestClient` de FastAPI (no requiere
red). En una máquina local `python server.py` levanta uvicorn normalmente;
en algunos sandboxes sin binding de red persistente, usa el `TestClient` o
los scripts de `examples/` para ejecutar el pipeline.

---

## 13. Construir un subconjunto de curvas (una, dos, las que sean)

No hace falta construir siempre las 28. Puedes construir cualquier
subconjunto con `select_curves`, que agrega automáticamente las
dependencias necesarias:

```python
from curvelib.orchestrator import load_config, select_curves, build_all

cfg = load_config("config/curves.yaml")

# dos curvas independientes
sub = select_curves(cfg, ["USD_SOFR", "EUR_ESTR"])
curves = build_all(sub)

# una curva con dependencia: incluye USD_SOFR automáticamente
sub = select_curves(cfg, ["PEN_X_SOFR"])
curves = build_all(sub)          # construye {USD_SOFR, PEN_X_SOFR}
```

O desde la línea de comandos con el script de ejemplo:

```bash
python examples/run_subset.py USD_SOFR EUR_ESTR
python examples/run_subset.py PEN_X_SOFR        # traerá USD_SOFR sola
```

**Regla clave**: una curva no se puede construir sin sus dependencias. Si
pides `PEN_X_SOFR`, necesitas `USD_SOFR` — `select_curves` lo resuelve por
ti. Si construyes un YAML a mano y omites una dependencia, el orquestador
lo detecta y avisa:

```
ConfigError: 'USD_SOFR' aparece en depends_on pero no está definida en curves.
```

Esto también aplica a bid/mid/ask: `build_bid_mid_ask(select_curves(cfg, [...]))`.
