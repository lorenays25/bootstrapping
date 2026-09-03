# Módulo 3 — Impactos

Valorización y griegas de la cartera de derivados, y comparación contra el
reporte de Calypso. Consume los factores de riesgo de los otros dos módulos: las
curvas del Módulo 1 y la superficie de volatilidad del Módulo 2.

## Arquitectura

Tres capas, y la frontera entre ellas es lo que hace que el motor sea comparable
consigo mismo:

```
src/impactlib/
  core/          convenciones de fecha y descuento, y el contrato `Factors`
    conventions.py   τ, factores de depósito, fecha spot
    market.py        Factors: spot, descuentos, forward, vol, plazo
    numerics.py      normal, densidad, inversa
  feeds/         de dónde salen los factores
    calypso.py       del propio reporte
    propio.py        curvas del Módulo 1 y superficie del Módulo 2
    base.py          la interfaz y el feed compuesto
  products/      qué se valoriza
    fx_option.py     opción FX europea (Garman-Kohlhagen)
    fx_forward.py    forward FX / NDF
    base.py          la interfaz y las griegas por diferencias finitas
    __init__.py      el REGISTRO
  escenarios.py  arma las tres comparaciones
  portfolio.py   lee el export y despacha cada fila a su producto
  report.py      resumen y detalle por operación
```

**Un producto no sabe de dónde vinieron sus factores y un feed no sabe qué se va
a valorizar con ellos.** Por eso cambiar de escenario no toca ninguna fórmula, y
por eso restar dos escenarios atribuye la diferencia a una pieza concreta.

### Un archivo, un producto

Calypso no exporta las opciones y los forwards con las mismas columnas: un
forward no tiene strike ni volatilidad implícita, tiene tasa pactada y fecha de
liquidación. Por eso **el mapeo de columnas vive en cada producto**, no en un
lector único que intente adivinar, y **cada producto tiene su propia carga** en
la interfaz.

Cada producto declara sus columnas con ALIAS: `Strike`, `Fwd Rate`,
`Forward Rate`, `Agreed Rate`… se aceptan todas. Si ninguna aparece, el lector
dice qué columna no encontró en vez de descartar la fila en silencio. Y si
cargas el export de forwards en la pestaña de opciones, cuenta las filas, dice
de qué producto son y no las mezcla.

### Agregar un producto

Escribir su módulo en `products/` con cinco cosas —`COLUMNAS` con sus alias,
`OBLIGATORIAS`, `reconoce`, `leer` y `pv`, más la tupla de griegas que declara—
y sumarlo a `REGISTRO`. La interfaz se arma sola: la sub-pestaña, el botón de
carga, las columnas de la tabla y el panel del riel salen de lo que el producto
declara. Las griegas también: la clase base las calcula por diferencias finitas
sobre el `pv` del propio producto, así que nunca pueden contradecir a la
valorización.

El orden del registro manda dos cosas: `identificar` devuelve el primero que
reconoce la fila, y la interfaz muestra las pestañas en ese orden.

Un forward declara `("pv", "delta", "theta", "rho", "rho2")` y no gamma ni vega,
porque son cero por construcción. Publicarlas como `0.0000` al lado de las de una
opción invita a leer un cero calculado donde solo hay un cero estructural. La
tabla tampoco muestra esas columnas, ni la de volatilidad, y llama a la columna
del precio pactado «Tasa pactada» en vez de «Strike».

### Agregar una fuente de factores

Escribir una clase en `feeds/` con un método `factores(fila) -> Factors`. Para
combinar dos fuentes está `FeedCompuesto`, que es lo que arma la comparación 2:
curvas propias con volatilidad de Calypso.

## Cómo se alimenta de los otros módulos

| Pieza | De dónde sale | Cómo |
|---|---|---|
| Descuento | Módulo 1 | `USD_SOFR` para USD y la curva **cross-currency** de la moneda local (`PEN_X_SOFR`, `MXN_X_SOFR`, `BRL_X_SOFR`, `CLP_X_SOFR`, `COP_X_SOFR`) |
| Forward | Módulo 1 | `F(T) = Spot · DF_base(T) / DF_cotizada(T)` con esas dos curvas |
| Volatilidad | Módulo 2 | `vs.vol(par, vencimiento, strike)`, lado mid |
| Spot | Interfaz | Editable por par; el export de Calypso trae dos decimales |

La curva de la moneda local es la **cross-currency**, no la local pura
(`PEN_OIS_TIBO`, `MXN_TIIE_28D`). La diferencia no es cosmética: la cross se
calibra contra los forwards FX del mercado, así que lleva incorporada la base
cross-currency; la local pura no. Medido contra el reporte, el forward armado con
tasas de depósito sin base se aparta hasta 2.5 pb, y la brecha crece con el
plazo — la forma característica de una base. El mapeo vive en
`feeds/propio.py::CURVA_POR_MONEDA`.

Las curvas devuelven factores desde la fecha de valorización y Calypso descuenta
desde la fecha spot; la conversión es el cociente `DF(val→T) / DF(val→spot)`.

## Cómo se usa

Interfaz, pestaña **Impactos** del parametrizador:

```
python curve_bootstrapper/server.py
```

Línea de comandos:

```
python impact_engine/examples/valorizar.py <portafolio.csv> [YYYY-MM-DD]
```

Pruebas:

```
PORTAFOLIO=<portafolio.csv> python impact_engine/tests/test_impactlib.py
```

## Convenciones, y cómo se verificaron

Ninguna se supuso: todas se identificaron contra el reporte del 01/09/2026.

| Qué | Convención | Cómo se verificó |
|---|---|---|
| Plazo de volatilidad | ACT/365, valorización → vencimiento | Resolviendo (F, τ) en 20 grupos de operaciones con el mismo vencimiento: el τ implícito reproduce los días calendario con error ≤ 0.06 días |
| Descuento | Depósito simple ACT/365, fecha spot → entrega | Contra `DELTA/FWD_DELTA`, que es exactamente el Df de la divisa base: error mediano 0.44 pb, máximo 0.81 pb; las otras siete combinaciones dan máximos de 2.5 a 16 pb |
| Forward | De la curva de forwards FX | La paridad con las dos tasas de depósito se aparta hasta 2.5 pb del forward implícito en el reporte |
| Prima pendiente | No entra al PV | En operaciones con `Premium Date` futura, el PV de Calypso es el valor de la opción sola |
| Moneda del PV | La de la pata cotizada | El cociente PV / `PV [USD]` vale 3.365 en las 820 filas de USD/PEN y 1 en el resto |

### Griegas

| Griega | Definición de Calypso | Error mediano |
|---|---|---|
| DELTA | dPV/dS, choque de 1 % aplicado como ±0.5 %; en divisa base | 0.004 % |
| GAMMA | Δ(S·1.005) − Δ(S·0.995) | 0.365 % |
| VEGA | PV(σ+0.5 pt) − PV(σ−0.5 pt), central | 0.049 % |
| THETA | PV(un día después) − PV | 1.219 % |
| RHO / RHO2 | PV(tasa ±50 pb), central | 1.70 % / 3.03 % |

**El choque partido en dos importa.** Aplicar el 1 % hacia adelante en vez de
±0.5 % lleva el error de gamma de 0.37 % a 8.65 %, y la diferencia adelantada en
vega lo lleva de 0.05 % a 2.20 %.

## Los tres escenarios, medidos

Portafolio del 01/09/2026, 973 operaciones. Error mediano en PV:

| Par | 1 · Calypso | 2 · curvas propias | 3 · cadena completa |
|---|---:|---:|---:|
| USD/BRL | 0.012 % | 0.740 % | 0.396 % |
| USD/PEN | 0.025 % | 3.528 % | 3.470 % |
| USD/MXN | 0.000 % | 4.676 % | 5.087 % |
| USD/CLP | 0.000 % | 11.98 % | 12.04 % |
| USD/COP | 0.000 % | 11.99 % | 12.01 % |

El salto entre la columna 1 y la 2 **no mide el motor de curvas**: mide que las
curvas se construyeron con las cotizaciones del 12/08 y el portafolio es del
01/09. Veinte días de mercado. Es exactamente el costo del insumo que falta, y
ahora está cuantificado.

## Lo que falta

- **Cotizaciones de curva del 01/09/2026.** Es lo único que separa la columna 2
  de ser una medición real del motor de curvas.
- **Un export de Calypso con forwards.** La fórmula está probada contra la
  paridad put-call, y la ruta completa —lectura, valorización, griegas,
  interfaz— contra un archivo sintético. Lo que falta confirmar son los NOMBRES
  DE COLUMNA: `reconoce` y `COLUMNAS` en `fx_forward.py` están escritos sobre
  supuestos de cómo nombra Calypso el producto y la tasa pactada. Con un archivo
  real, ajustar los alias es una línea, y mientras tanto el lector dice cuál no
  encontró.
- **Tipo de cambio con todos los decimales.**
