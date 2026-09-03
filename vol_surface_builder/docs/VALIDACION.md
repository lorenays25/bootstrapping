# Validación del Módulo 2 contra los resultados de Calypso — USD/MXN y USD/BRL

Fecha de valorización: **01/09/2026**. Entorno: `Risk - EOD With SOFR`.
Documento de referencia metodológica: `claude/superficie-volatilidad-metodologia-calypso.md`.

Insumos usados (exports de Calypso, 6 archivos por par):
`surface_pillars_call_put`, `surface_pillars_rr_bf`, `daily_call_put`, `daily_rr_bf`,
`point_bid`, `point_ask`.

---

## 0. Resumen ejecutivo

| Componente de la metodología | Estado | Evidencia |
|---|---|---|
| Álgebra `2vol (CP Avg)` en los pilares | **Cerrado** | error ≤ 1.25e-3, atribuible al redondeo del export de parámetros |
| Convención ATM (zero-delta straddle, premium-adjusted) | **Cerrado** | ATM exacto en las 487/485 fechas de la grilla diaria |
| Interpolación en plazo (varianza total, tiempo calendario) | **Cerrado** | ≤ 1.1e-5 vol pts en todo el tramo ≤ 1Y |
| Eje sobre el que se interpola en plazo | **Cerrado — corrección pendiente en el código** | etiqueta fija, no posición del eje de delta |
| Extrapolación antes del primer pilar | **Cerrado** | fórmula con δ = 1/24 día, error ≤ 4.5e-6 |
| Propagación bid/ask | **Cerrado** | identidad de spreads exacta en 13 tenores × 4 puntos |
| Calendario de la grilla diaria | **Cerrado** | 36 (MXN) y 38 (BRL) festivos identificados uno a uno |
| Parametrización de esos calendarios en Calypso | **1 defecto encontrado** | 31/12/2027 (§3-bis) |
| Corte spot → forward delta en 1Y | **Mecanismo identificado; magnitud no reproducida** | residuo estructural ≈ 15% del salto |
| Interpolador de smile (familia y frontera) | **Corregido** | not-a-knot sin nodos sintéticos; eje = delta plano (§6-bis) |
| Residuo final del interpolador | **Abierto, acotado** | 0.027 interior / 0.079 en 5 delta (§7) |
| **Estado del motor tras las correcciones** | **Validado** | pilares y bid/ask exactos; grilla diaria ≤ 0.0125 vol pts (§7) |

---

## 1. Precisión de los insumos

El **export del panel de parámetros trae las cotizaciones redondeadas a 3 decimales**, mientras que
la superficie usa 4. Ejemplo USD/MXN 2W: el panel reporta `ATM 5.56700` / `Trade Vol 5.56800`;
la superficie revela `5.5675`. Todas las diferencias del round-trip son exactamente ±0.0005 y
aparecen solo en los tenores cuyo 4º decimal es 5.

- USD/MXN: máx |diferencia| round-trip = 1.00e-3 vol pts
- USD/BRL: máx |diferencia| round-trip = 1.00e-3 vol pts
- Álgebra `2vol (CP Avg)` sobre los pilares: máx 1.25e-3 en ambos pares

**Acción**: la fuente de cotizaciones para el motor debe ser la pestaña **Surface RR/BF**
(7 decimales), no el panel de parámetros. Es el mismo problema que el tipo de cambio a
2 decimales, en otra variable.

## 2. Propagación bid/ask — confirmada la regla de "lado consistente"

`(bid + ask)/2 = mid` con error ≤ 3.6e-15 en ambos pares.

Al convertir los puntos bid y ask a cotizaciones, el vector de spreads resulta proporcional,
con **el mismo vector de proporciones en los dos pares y en todos los tenores**:

| | sATM | sRR25 | sRR10 | sBF25 | sBF10 |
|---|---|---|---|---|---|
| ratio vs sATM | 1.000 | 0.700 | 1.200 | 0.500 | 0.800 |

Solo escala el nivel `sATM(T)`: en USD/MXN va de 2.00 (1W) a ≈0.60 (≥3M); en USD/BRL de
9.675 (O/N) y 4.00 (1W) a ≈1.60 (≥2M), cayendo a 1.195 en 2Y.

La verificación decisiva es la identidad de propagación, que se cumple de forma **exacta**
(residuo 0.00000) en los 13 tenores × 4 puntos de USD/BRL y en los 12 × 4 de USD/MXN:

```
spread(C25) = sATM + sBF25 + sRR25/2
spread(P25) = sATM + sBF25 − sRR25/2
spread(C10) = sATM + sBF10 + sRR10/2
spread(P10) = sATM + sBF10 − sRR10/2
```

Es decir: Calypso aplica el spread **a nivel de cotización** y lo propaga por la misma álgebra
`2vol (CP Avg)` usando **el mismo lado del risk reversal en ambas alas**. Por eso el ala put
queda sistemáticamente más angosta que la call. **No es un enfoque de envolvente** (min/max
sobre los dos lados), que es el diseño alternativo que se había considerado.

Esto valida el "enfoque A" del Módulo 2: construir la superficie tres veces, una por lado,
alimentando cada corrida con las cotizaciones de ese lado.

## 3. Calendario de la grilla diaria — el campo `Holidays` sí se usa

El manual (`FXVolatilitySurfaces.pdf`, v16.1) afirma que el campo `Holidays` "is not used at
this time". **Es falso.** La grilla DAILY excluye exactamente los días hábiles del calendario
conjunto de las dos plazas:

- **USD/MXN** (`Holidays = NYC,MEX`): 36 días hábiles ausentes, entre ellos 07/09/2026 Labor Day,
  16/09/2026 Independencia de México, 12/10 Columbus, 02/11 y 16/11 (Revolución), 26/11
  Thanksgiving, 01/02/2027 Constitución, 15/03/2027 Benito Juárez, 25–26/03/2027 Jueves y
  Viernes Santo, 18/06/2027 Juneteenth, 01/05/2028 Día del Trabajo.
- **USD/BRL**: 38 días hábiles ausentes, entre ellos 08–09/02/2027 y 28–29/02/2028 **Carnaval**,
  27/05/2027 y 15/06/2028 **Corpus Christi**, 20/11/2026 Consciência Negra, 21/04/2027 y
  21/04/2028 Tiradentes, 15/11/2027 Proclamação da República, más los festivos NYC.
  El par 11/10/2027 + 12/10/2027 (Columbus Day y N. Sra. Aparecida en días consecutivos) es
  el discriminador limpio frente a un calendario solo-NYC.

Cero fechas de fin de semana en ambas grillas.

**Acción**: el Módulo 2 debe cargar calendarios reales por plaza. La simplificación actual
(`advance_business_days` solo con fines de semana) no reproduce la grilla ni las fechas de
entrega.

## 3-bis. ¿Están bien parametrizados esos calendarios? — contraste contra reglas oficiales

Se reconstruyó el calendario oficial NYC / MEX / BRA por reglas (fechas fijas, n-ésimo día de
la semana, y las móviles derivadas de Pascua) y se contrastó contra el calendario que la
superficie efectivamente aplica, deducido de la grilla DAILY.

| | días hábiles en la ventana | excluidos por Calypso | feriados por regla | solo Calypso | solo oficial |
|---|---|---|---|---|---|
| USD/MXN | 523 | 36 | 37 | **0** | **1** |
| USD/BRL | 523 | 38 | 39 | **0** | **1** |

**Cero falsos positivos**: Calypso no excluye ningún día que no sea feriado real. Los 36 y 38
días excluidos se explican uno a uno por una regla oficial.

### El tratamiento de feriados que caen en fin de semana es correcto… con una excepción

Feriados **locales** (MEX y BRA) que caen sábado o domingo: Calypso **no los traslada**, que es
lo correcto — ni México ni Brasil tienen regla de traslado. Verificado en 15/11/2026
(Proclamação, domingo), 12/12/2026 y 12/12/2027 (Guadalupe), 01/05/2027 (Trabajo, sábado),
20/11/2027 (Consciência Negra, sábado): en los cinco casos ni el viernes previo ni el lunes
siguiente quedan excluidos.

Feriados **de Nueva York** que caen en fin de semana: Calypso sí aplica la regla de mercado
(sábado → viernes previo; domingo → lunes siguiente):

| feriado | día | observancia esperada | Calypso |
|---|---|---|---|
| 19/06/2027 Juneteenth | sábado | viernes 18/06/2027 | **excluido** ✔ |
| 04/07/2027 Independencia EEUU | domingo | lunes 05/07/2027 | **excluido** ✔ |
| 25/12/2027 Navidad | sábado | viernes 24/12/2027 | **excluido** ✔ |
| 01/01/2028 Año Nuevo | sábado | viernes 31/12/2027 | **hábil** ✘ |

### Observación 1 — inconsistencia en el cierre de año 2027/2028

`31/12/2027` es el **único** día hábil del universo de dos años en el que los dos calendarios
discrepan, y la discrepancia es **interna a Calypso**, no una cuestión de convención:

- Bajo la convención de mercado (NYSE/SIFMA), tanto 24/12/2027 como 31/12/2027 deberían ser
  feriados. Calypso excluye el primero y no el segundo.
- Bajo la convención de la Reserva Federal (los feriados en sábado no se observan), **ninguno**
  de los dos debería ser feriado. Calypso excluye el primero.

Sea cual sea la convención que se quiera adoptar, los dos días deben recibir el mismo
tratamiento y hoy no lo reciben. El patrón —la observancia cae en el año calendario anterior—
sugiere un defecto de frontera de año en el generador del calendario, que se repetiría cada vez
que el 1 de enero cae sábado: **2028, 2034, 2039, 2045**.

*Impacto*: 31/12/2027 aparece como fecha de vencimiento en la grilla diaria de las seis
superficies, y desplaza en un día hábil las fechas de entrega (T+2) de cualquier vencimiento
del 29 y 30/12/2027. Es acotado, pero es una diferencia de valorización real contra un
calendario correcto.

### Observación 2 — dos reglas del calendario MEX no son verificables en esta ventana

- **12 de diciembre (Virgen de Guadalupe)**, feriado bancario CNBV: cae sábado en 2026 y
  domingo en 2027, así que la grilla no permite confirmar si el calendario `MEX` de Calypso lo
  incluye. Requiere una superficie construida en un año en que caiga entre semana.
- **1 de octubre (Transmisión del Poder Ejecutivo)**, vigente desde 2024 y con periodicidad de
  seis años: la próxima ocurrencia es 2030, fuera de la ventana. El análisis previo del
  calendario de curvas (`claude/holidays-mxn-calypso-vs-propio.md`) ya encontró que el
  calendario `MXN_MEXICO` de Calypso **no** contiene 01/10/2030, 01/10/2036 ni 01/10/2042,
  mientras que el propio sí. Si el calendario `MEX` que usa la superficie es el mismo objeto,
  el defecto se propaga a las superficies para vencimientos largos.

### Observación 3 — las fechas pilar sí usan el calendario conjunto

Los vencimientos cotizados confirman el uso del calendario conjunto y de un desfase de entrega
de dos días hábiles. Ejemplo USD/MXN 2M: entrega 03/11/2026, y como 02/11 es feriado en México
el vencimiento retrocede a 29/10/2026 (`Cal Days = 58`), no a 30/10. Con calendario solo-NYC el
resultado habría sido 30/10. Análogo en 4M: entrega 04/01/2027 (01/01 feriado) y vencimiento
30/12/2026.

El motor toma las fechas pilar del export, así que esto no es un riesgo de cálculo hoy; sí lo es
la **fecha de entrega** usada para el forward, que el motor genera con `expiry + 2 días hábiles`
sobre un calendario de solo fines de semana.

## 4. Interpolación en el eje de plazos

Confirmado: **varianza total lineal en días calendario ACT/365**
(`Interpolate Outright Variance = true`, `Interpolate on Trading Time = false`).

Pero el objeto que se mantiene fijo al moverse en el tiempo **no es la posición en el eje de
call delta, sino la etiqueta de la cotización** (la vol del put de 25 delta, del call de 10
delta, etc.). Los dos esquemas coinciden en los puntos call — para una call la posición del
eje *es* su delta — pero difieren en las puts y en el ATM, cuya posición en el eje se mueve
con el tenor por el ajuste de prima.

Contraste sobre USD/MXN, tramo 1W–1Y (239 fechas):

| esquema | máx \|error\| P25 | P10 | ATM |
|---|---|---|---|
| (A) posición fija del eje de delta | 5.8e-3 | 6.6e-3 | 3.6e-3 |
| (B) etiqueta fija | **4.9e-6** | **5.0e-6** | **5.0e-6** |

4.9e-6 es exactamente el redondeo del export a 6 decimales. En USD/BRL el esquema (B) da
≤ 1.1e-5 sobre 242 fechas (el mayor residuo viene de reconstruir C/P desde RR/BF, ambos
redondeados).

**Acción**: corregir `VolSurface._total_variance_at` para interpolar por etiqueta.
El error del esquema actual es pequeño pero sistemático y unidireccional.

## 5. Extrapolación antes del primer pilar — regla identificada

Aplica solo a superficies cuyo primer pilar no es O/N (USD/MXN y USD/PEN lo tienen en 1W;
**USD/BRL tiene pilar O/N y por tanto no tiene región de extrapolación corta**).

Regla, con `t` en días calendario desde la fecha de valorización y `t₁` el primer pilar:

```
σ(t) = σ(t₁) · sqrt[  t₁·(t + δ)  /  ( t·(t₁ + δ) )  ]        δ = 1/24 día = 1 hora
```

Equivalente: la varianza total es lineal desde el origen medida sobre `(t + δ)`, pero se
reporta dividiendo por el conteo entero ACT/365.

Verificación en USD/MXN (3 fechas × 5 columnas): error ≤ **4.5e-6** vol pts, y el δ implícito
resuelto punto por punto da 0.041658 – 0.041672 días en las 15 celdas. En USD/PEN el mismo
ajuste da δ = 0.041665 – 0.041670. El valor 0.0416667 = 1/24 exacto.

El mismo 0.042 aparece en la columna `Trade Days` del panel de parámetros, en **los mismos
cinco tenores para ambos pares** (3M 01/12/2026, 4M 30/12/2026, 6M 01/03/2027, 18M 01/03/2028,
2Y 01/09/2028), lo que indica un artefacto de reloj global (`DateCut of Expiries = Lima 17:00`)
y no algo específico del par. El origen exacto del desfase de una hora no está derivado.

Impacto: a 1 día es ~8 pb de vol; a 3 días, ~2 pb. Cosmético en el pilar, pero necesario para
cerrar contra Calypso en el tramo corto, donde vence buena parte de la cartera de opciones.

## 6. Corte spot delta → forward delta en 1Y

Ambos pares tienen `Spot Delta Last Tenor = 1Y` y `ATM Zero Straddle Last Tenor = 10Y`.

El error de la interpolación por etiqueta (sin implementar el corte) está **perfectamente
segmentado por fecha**, lo que confirma que el corte se resuelve por **fecha de vencimiento**
y no por tenor cotizado:

| par | tramo | P10 | P25 | ATM | C25 | C10 |
|---|---|---|---|---|---|---|
| USD/MXN | ≤ 1Y (239 fechas) | 5e-6 | 5e-6 | 5e-6 | 5e-6 | 5e-6 |
| USD/MXN | > 1Y (244 fechas) | 4.3e-3 | 1.9e-2 | **4e-6** | 9.5e-2 | 6.2e-2 |
| USD/BRL | ≤ 1Y (242 fechas) | 1.1e-5 | 1.1e-5 | 5e-6 | 1.1e-5 | 1.1e-5 |
| USD/BRL | > 1Y (242 fechas) | 1.3e-2 | 2.9e-2 | **5e-6** | 9.2e-2 | 5.9e-2 |

El ATM sigue exacto en todo el rango, coherente con `ATM Zero Straddle Last Tenor = 10Y`, que
no corta en 1Y. Solo saltan las alas, entre el 01/09/2027 y el 02/09/2027, y el valor DAILY
vuelve a coincidir exacto en los pilares 18M y 2Y.

Salto puro observado (Calypso en t=366 menos la interpolación sin corte) frente al salto que
implica reexpresar el mismo smile 1Y en delta forward:

| | USD/MXN Calypso | USD/MXN propio | ratio | USD/BRL Calypso | USD/BRL propio | ratio |
|---|---|---|---|---|---|---|
| C10 | +0.06197 | +0.05200 | 0.839 | +0.05891 | +0.04979 | **0.845** |
| C25 | +0.09497 | +0.10883 | 1.146 | +0.09216 | +0.10594 | **1.150** |
| P25 | −0.01901 | −0.01815 | 0.955 | −0.02902 | −0.03285 | 1.132 |
| P10 | −0.00429 | −0.00312 | 0.727 | −0.01290 | −0.00810 | 0.628 |

El signo es correcto en los ocho puntos, y **los ratios de C10 y C25 son prácticamente
idénticos entre los dos pares** (0.839/0.845 y 1.146/1.150) pese a que los pares tienen niveles
de vol muy distintos (9.72 vs 14.52) y curvas de descuento locales incomparables. Eso descarta
que el residuo venga del corte en sí y lo localiza en **la forma del smile en el eje de delta**:
el nuestro es demasiado empinado en x≈24 y demasiado plano en x≈9.6.

Se verificó además que el salto es **invariante al spot** (idéntico con 16.99 y con
17.07181955), por lo que la precisión del tipo de cambio no interviene aquí.

### Qué se descartó como explicación

- Un solo valor del nodo sintético de 1 delta no reproduce C10 y C25 a la vez: el barrido
  muestra que subirlo aumenta C10 pero **reduce** C25, y los dos objetivos requieren valores
  distintos (14.60 vs 14.85 para USD/MXN).
- Las condiciones de frontera alternativas del spline mueven los residuos pero no los cierran:
  `clamped` reproduce C25 casi exacto (0.09488 vs 0.09497) pero deja C10 en 0.06914 vs 0.06197;
  `not-a-knot` queda cerca de `natural`.
- Sin nodos sintéticos (extrapolación plana más allá de 10 delta) el salto de C10 es
  exactamente 0, lo que confirma que Calypso sí extiende el ala.

### Qué hace falta para cerrarlo

Ver §6-bis: la malla de deltas de 5 en 5 permitió aislar la forma del smile, y con la
construcción corregida el residuo del corte debe re-medirse.

## 6-bis. La malla de 5 en 5 deltas: el interpolador de smile queda acotado

Export adicional de USD/BRL: `surface_pillars` y `daily` con **delta cada 5** (P5…P45, ATM,
C45…C5), 19 puntos por pilar. Los puntos de 5 delta caen **fuera** de los nodos cotizados, así
que muestrean directamente el ala extrapolada — que ningún otro export exhibía.

### Lo que queda confirmado

- **Los 5 nodos cotizados se reproducen exactos** (0.0000) en los 13 pilares. Confirma otra vez
  convención, álgebra y colocación de nodos.
- **El eje es el delta real, no la etiqueta.** Se probó la hipótesis de que Calypso coloque el
  put de d delta en la posición `100 − d` del eje (implementación habitual): da errores de hasta
  0.80 vol pts en C5 y 0.10 en el interior, contra 0.04 y 0.02 del eje real. Descartada.
- **El smile se reconstruye en cada fecha a partir de las 5 cotizaciones interpoladas**, no se
  interpola toda la malla de deltas. La señal es nítida: en el tramo ≤ 1Y, interpolar en varianza
  total a etiqueta fija reproduce las columnas **cotizadas** con error 0.0000 y las **derivadas**
  con error ≤ 0.0032. Si Calypso interpolase la malla completa, las derivadas también darían
  0.0000. Es la arquitectura que ya asume el motor.

### Lo que estaba mal en nuestra construcción

| construcción del smile | interior (17 pts × 13 pilares) | C5 | P5 |
|---|---|---|---|
| **actual**: 7 nodos (con los sintéticos de 1 y 99 delta dentro del ajuste), spline natural | 0.0873 | 0.2044 | 0.1270 |
| 5 nodos cotizados, spline natural, extrapolación plana | 0.0711 | 0.8195 | 0.1910 |
| 5 nodos cotizados, spline **not-a-knot**, extrapolación plana | **0.0228** | 0.8195 | 0.1910 |
| 5 nodos **not-a-knot** + extensión lineal con pendiente tangente | 0.0228 | 0.0685 | 0.1256 |
| **5 nodos not-a-knot + extensión lineal con pendiente tangente × 1.04** | **0.0228** | **0.0394** | **0.1243** |

Tres conclusiones:

1. **Los nodos sintéticos de 1 y 99 delta no deben entrar en el ajuste del spline.** Incluirlos
   deforma el interior (0.0873 vs 0.0228) porque arrastran las derivadas en los nodos cotizados.
2. **La condición de frontera es `not-a-knot`, no `natural`** — 3× mejor en el interior. Se probó
   también interpolar en σ², en ln σ y con frontera `clamped`: ninguna mejora
   (σ² empeora a 0.0305; `clamped` a 0.41).
3. **El ala sí se extiende, no es plana.** Con extrapolación plana el error en C5 es 0.82 vol pts.
   Una extensión lineal con la pendiente tangente del spline en el nodo de 10 delta lo baja a
   0.069, y con esa pendiente escalada por 1.04 a 0.039.

Mejora neta frente al código actual: **interior 0.087 → 0.023** (3.8×) y **ala call 0.204 → 0.039**
(5×).

### Confirmación cruzada con USD/MXN

Se repitió el ejercicio con la malla `delta every 5` de USD/MXN (12 pilares más, smile de nivel
y asimetría muy distintos: ATM 4.72–10.23 contra 9.43–16.75 en BRL). Sobre los **26 pilares
juntos** (494 puntos) ninguna de las tres métricas empeora respecto de USD/BRL solo:

| eje del spline | frontera | k del ala | interior | C5 | P5 | peor |
|---|---|---|---|---|---|---|
| **actual**: delta ajustado por prima, 7 nodos, natural | — | — | 0.0873 | 0.2044 | 0.1270 | 0.2044 |
| delta ajustado por prima | natural | 1.00 | 0.0781 | 0.1630 | 0.1270 | 0.1630 |
| delta ajustado por prima | **not-a-knot** | 1.00 | **0.0228** | 0.0685 | 0.1256 | 0.1256 |
| delta ajustado por prima | not-a-knot | 1.04 | **0.0228** | **0.0394** | 0.1243 | 0.1243 |
| **delta sin ajuste de prima** | **not-a-knot** | **1.00** | 0.0255 | 0.0744 | **0.0788** | **0.0788** |

Es decir: `not-a-knot` y el óptimo del ala en k ≈ 1.04 se reproducen idénticos al agregar
USD/MXN, así que no son un artefacto de USD/BRL.

**Resultado colateral: spot delta y forward delta son indistinguibles como eje.** Cambiar el eje
de spot a forward multiplica todas las posiciones por 1/Df; como el punto consultado se escala
por el mismo factor, un spline cúbico es invariante a esa transformación. Los dos ejes dan
exactamente los mismos números. El motor es por tanto robusto a esa elección — lo que sí importa
es el **ajuste por prima**, que no es una transformación afín.

### Lo que sigue abierto

- **Queda un residuo de 0.02–0.08 vol pts que no cierra con ninguna variante probada.** Hay una
  tensión: el eje con ajuste por prima da el mejor interior (0.0228) pero el peor ala put
  (0.1243); el eje sin ajuste da el mejor ala put (0.0788) a costa de un interior levemente peor
  (0.0255). Ninguno es exacto, de modo que la colocación de los nodos put en el eje todavía no
  es la de Calypso. La configuración con **menor error máximo** es *sin ajuste de prima +
  not-a-knot + ala tangente (k = 1.00)*: **0.0788 vol pts** sobre 494 puntos, contra 0.2044 del
  código actual.
- **El factor k ≈ 1.04 de la pendiente del ala** mejora el ala call a 0.039 pero solo bajo el eje
  ajustado por prima; bajo el otro eje el óptimo se corre a ≈1.05 y empeora el ala put. Dado que
  no está derivado del parámetro `Up/Down Extrap 1.0 Delta = 1.0`, la recomendación conservadora
  es **k = 1.00** (pendiente tangente pura).

### Otras dos verificaciones sobre la malla de USD/MXN

- La **regla de extrapolación corta** con δ = 1/24 día (§5) reproduce las 3 fechas pre-pilar en
  las **19 columnas** con error ≤ 6.2e-4 — exacto (≤4.5e-6) en las 5 cotizadas y del orden de
  1e-3 en las derivadas, que es justamente el residuo del interpolador de smile.
- La señal de "el smile se reconstruye en cada fecha" se repite: en el tramo 1W–1Y, interpolar
  por etiqueta reproduce las columnas cotizadas con 0.0000 y las derivadas con ≤0.0029.

### El corte de 1Y visto en toda la malla

Con las 19 columnas, el error de la interpolación en plazo sin implementar el corte (§6) se
distribuye así en el tramo > 1Y: **máximo en C45 (0.108)**, decreciendo hacia el ala
(C25 0.092, C5 0.033) y hacia el lado put (P45 0.069, P25 0.029, P5 0.008). Es decir, el efecto
del cambio de convención **es máximo cerca del ATM del lado call**, no en las alas. El ATM
mismo permanece exacto (0.0000).

## 7. Resultado tras aplicar las correcciones

Las cinco correcciones se aplicaron al motor y se volvió a correr la validación completa.

### Qué cambió en el código

| archivo | cambio |
|---|---|
| `dates.py` | clase `Calendar` con calendarios generados por regla para NYC, MEX, BRA, LIM, TGT, SCL y BOG, y sus alias del campo `Holidays`. `advance_business_days` acepta calendario. |
| `smile.py` | el eje del interpolador pasa a ser el **call delta plano**; spline **not-a-knot** sobre los 5 nodos cotizados únicamente; ala lineal con la pendiente tangente; `vol_at_strike` y `strike_at_delta` con caída a Brent cuando el punto fijo no converge. |
| `surface.py` | la interpolación en plazo pasa a ser **sobre las 5 cotizaciones, a etiqueta fija**, y el smile se **reconstruye** en cada fecha; extrapolación corta con δ = 1/24 día; **reexpresión de convención** en el corte `Spot Delta Last Tenor`; nueva API `vol(expiry, strike)`. |
| `orchestrator.py` | pasa el calendario del par y el factor de pendiente del ala. |
| `config/surfaces.yaml` | `holidays:` por par; fuente de cotizaciones = export Surface RR/BF (7 decimales); archivos de quotes separados por lado bid/mid/ask. |

### Errores contra Calypso, después

Grilla diaria **completa** (487 y 485 fechas), 5 puntos cotizados, en vol pts:

| par | n | antes del 1er pilar | 1er pilar … 1Y | > 1Y (corte de convención) | total |
|---|---|---|---|---|---|
| USD/MXN | 487 | **0.0000** | **0.0000** | 0.0090 | **0.0090** |
| USD/BRL | 485 | — (tiene pilar O/N) | 0.0050 | 0.0125 | **0.0125** |

El 0.0050 de USD/BRL es exactamente el redondeo de su export `daily_call_put`, que trae
2 decimales.

Pilares y bid/ask, en vol pts:

| comparación | USD/MXN | USD/BRL |
|---|---|---|
| pilares, 5 puntos cotizados | **0.0000** | **0.0000** |
| puntos BID (13 tenores × 5) | **0.0000** | **0.0000** |
| puntos ASK (13 tenores × 5) | **0.0000** | **0.0000** |
| pilares, malla de 19 deltas | 0.0788 | 0.0744 |
| grilla diaria, malla de 19 deltas | 0.0798 | 0.0744 |

Y el desglose del residuo de 19 deltas por columna: ≤ 0.0000 en los 5 nodos cotizados,
≤ 0.027 en los 12 puntos interiores derivados, y 0.062–0.079 solo en los dos puntos de 5 delta.

### Comparación antes / después

| métrica | antes | después |
|---|---|---|
| pilares cotizados | 0.0013 | **0.0000** |
| grilla diaria ≤ 1Y, 5 puntos | ~0.005 | **0.0000** |
| grilla diaria > 1Y, 5 puntos (corte) | 0.0950 | **0.0090** |
| malla de 19 deltas, puntos interiores | 0.0873 | **0.0270** |
| malla de 19 deltas, puntos de 5 delta | 0.2044 | **0.0788** |
| antes del primer pilar | 0.1140 | **0.0000** |

### La consulta que importa: `vol(expiry, strike)`

Es la API que consume el Módulo 3, y funciona en cualquier fecha, sea pilar o no.

```
USDMXN  vencimiento 15/03/2027 (no es pilar)  forward = 17.254799  entrega 17/03/2027

    strike     K/F  vol bid %  vol mid %  vol ask %   eje (Δc plano)   Δc prem-adj
  13.80384  0.8000    7.55217    7.88218    8.21238          97.8534       78.2817 *
  15.52932  0.9000    7.53242    7.86846    8.20793          94.7861       84.9300 *
  16.39206  0.9500    7.48074    7.85715    8.23124          80.4100       74.9602
  17.25480  1.0000    8.38651    8.69362    9.00090          50.1692       47.6889
  18.11754  1.0500    9.62948   10.10786   10.57284          26.0786       24.9477
  18.98028  1.1000   10.92274   11.46983   12.01030          13.3839       12.8356
  20.90000  1.2113   12.33937   13.03439   13.71333           2.4230        2.3386 *
  22.00000  1.2750   12.53098   13.28453   14.02501           0.6921        0.6708 *
  23.20000  1.3446   12.58248   13.36430   14.13954           0.1401        0.1363 *

* fuera del rango de nodos (10.42 a 88.13 de call delta plano): actúa el ala lineal
```

La columna de la derecha ilustra el hallazgo del §6-bis: el delta ajustado por prima **no es
monótono en K** (78.28 → 84.93 → 74.96 al subir el strike), razón por la cual el eje del
interpolador usa el delta plano.

**Advertencia para el Módulo 3**: los tres strikes de la validación de Griegas anterior
(20.90, 22.00, 23.20 con spot ≈ 17.07) caen **fuera** del rango de nodos, en la región del ala
lineal — que es justamente donde queda el residuo de 0.06–0.08 vol pts. Cualquier diferencia
de valorización contra Calypso en opciones tan fuera del dinero hay que leerla con esa cota
en mente, no como error de la fórmula de pricing.

### Batería de pruebas

`tests/test_vollib.py` pasa completa e incorpora tres grupos nuevos: round-trip
`vol(expiry, strike)` sobre los nodos calibrados de los 6 pares (< 1e-10), consistencia de
`strike_at_delta` con `vol(expiry, strike)` en fechas fuera de pilar (< 1e-10), y verificación
del calendario contra feriados concretos de NYC+MEX y NYC+BRA.

## 8. Otros

- `FXDate` del panel: 01/09/2026 en USD/MXN, **02/09/2026 en USD/BRL**, mientras que el ancla
  de las cotizaciones es 01/09/2026 en ambos. Confirma el Hallazgo 6: la fecha de valorización
  debe fijarse explícitamente en el YAML y no leerse del panel.
- El export `daily_call_put` de USD/BRL trae solo **2 decimales**, mientras que `daily_rr_bf`
  trae 6. Para validar contra USD/BRL hay que reconstruir C/P desde RR/BF.
- USD/BRL tiene un pilar **O/N** que USD/MXN y USD/PEN no tienen, y un salto de nivel muy
  marcado entre 1M (11.24) y 2M (16.75) que luego revierte a 15.38 en 3M. No es un error de
  datos: la grilla diaria lo interpola de forma monótona en varianza y cierra en ambos pilares.

---

## 9. Correcciones aplicadas

Las cinco están implementadas (§7). Enunciado original, para trazabilidad:

1. `VolSurface._total_variance_at` — interpolar por etiqueta de cotización, no por posición del
   eje de delta (§4).
2. `VolSurface._total_variance_at` — implementar la extrapolación con δ = 1/24 día antes del
   primer pilar, en lugar de vol plana (§5).
3. `dates.advance_business_days` — calendarios reales por plaza (NYC, MEX, BRA, LIM, …) en
   lugar de solo fines de semana (§3). El calendario propio debe corregir el 31/12/2027 y
   documentar la diferencia frente a Calypso (§3-bis).
4. `quotes_loader` — preferir el export Surface RR/BF como fuente de cotizaciones (§1).
5. `smile.build_slice` — ajustar el spline **solo sobre los 5 nodos cotizados** y con frontera
   `not-a-knot`; los nodos sintéticos de 1 y 99 delta salen del ajuste y pasan a ser una
   extensión lineal con la pendiente tangente en los nodos de 10 delta (§6-bis).
6. Pendiente de datos: la colocación exacta de los nodos put en el eje (§6-bis).
