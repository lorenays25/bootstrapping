# Superficies de Volatilidad FX — Comparación de los 6 pares

Versión 0.2 · 02/09/2026 · **Set completo**: parámetros, underlyings, quotes y pantalla
Definition de los seis pares.

Complemento de `superficie-volatilidad-metodologia-calypso.md` (metodología, documentada a
partir de USD/MXN). Aquí se contrastan las seis superficies entre sí y contra el manual
Calypso "FX Volatility Surfaces" v16.1.

Datos: exports del 01/09/2026 16:00, quote set `LAST`, para USD/MXN, USD/PEN, USD/BRL,
USD/CLP, USD/COP y EUR/USD.

---

## 1. Pestaña Definition — idéntica en los seis (riesgo de diseño cerrado)

| Campo | USDMXN | USDPEN | USDBRL | USDCLP | USDCOP | EURUSD |
|---|---|---|---|---|---|---|
| DateRoll | MOD_FOLLOW | MOD_FOLLOW | MOD_FOLLOW | MOD_FOLLOW | MOD_FOLLOW | — |
| Pricing Env | Risk-EOD With SOFR | Risk-EOD With SOFR | Risk-EOD With SOFR | Risk-EOD With SOFR | Risk-EOD With SOFR | — |
| Strike Spread | Delta | Delta | Delta | Delta | Delta | — |
| **Interpolator** | **3DSpline1D** | **3DSpline1D** | **3DSpline1D** | **3DSpline1D** | **3DSpline1D** | — |
| Derived | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| **Generator** | **FXOption** | **FXOption** | **FXOption** | **FXOption** | **FXOption** | — |
| Holidays | NYC,MEX | NYC,LIM | NYC,SPO | NYC,SAN | BOG,NYC | — |
| Quote set | LAST | LAST | LAST | LAST | LAST | LAST |

(EUR/USD: el pantallazo enviado es de la pestaña Quotes, no Definition — falta ese.)

**Esto cierra el principal riesgo de diseño que quedaba abierto**: ningún par usa
`FXOptionSABR` ni `Interpolator3DSplineL1DMulti`. Los cinco verificados usan el mismo
generador (`FXOption`) y el mismo interpolador (`Interpolator3DSpline1D`, spline cúbica
natural con extrapolación plana). **Un solo motor sirve para todos los pares.**

Los `Holidays` sí varían por par (NYC + la plaza local), lo que explica que las fechas de
expiry de un mismo tenor difieran entre pares. Nota: el manual dice que *"The Holidays
field is not used at this time"* y que se usan los calendarios de Currency Defaults — o
sea que este campo, aunque esté poblado coherentemente, podría no ser el que manda.
Conviene confirmarlo contra los Currency Defaults, porque de él dependen las fechas de
expiry.

### Nombres de las superficies — tres patrones distintos

| Par | Nombre de la superficie |
|---|---|
| USD/MXN | `USD/MXN FWD VOL` |
| USD/BRL | `USD/BRL FWD VOL` |
| USD/CLP | `USD/CLP VOL SURFACE` |
| USD/COP | `USD/COP VOL SURFACE` |
| EUR/USD | `EUR/USD VOL SURFACE` |
| **USD/PEN** | **`USD/PEN OTCDD Strategies`** |

Tres convenciones de nombre para seis superficies del mismo tipo. USD/PEN además tiene un
nombre de otra familia (`OTCDD Strategies`). Sugiere que se crearon en momentos distintos
o por equipos distintos — consistente con el Hallazgo 1 de abajo.

---

## 2. Tabla comparativa de parámetros

| Parámetro | USDMXN | USDPEN | USDBRL | USDCLP | USDCOP | EURUSD |
|---|---|---|---|---|---|---|
| Volatility Day Count | ACT/365 | ACT/365 | ACT/365 | ACT/365 | ACT/365 | **(vacío)** ⚠ |
| Quotes are Delta with Premium | true | true | true | true | true | **true** ⚠ |
| **Spot Delta Last Tenor** | 1Y | **0D** ⚠ | 1Y | 1Y | 1Y | 1Y |
| **ATM Zero Straddle Last Tenor** | 10Y | **0D** ⚠ | 10Y | 10Y | 10Y | 10Y |
| Strangle/Fly Quotes | 2vol (CP Avg) | 2vol (CP Avg) | 2vol (CP Avg) | 2vol (CP Avg) | 2vol (CP Avg) | 2vol (CP Avg) |
| Interpolate Outright Variance | true | true | true | true | true | **false** ⚠ |
| Up Extrap 1.0 Delta | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Down Extrap 1.0 Delta | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Interpolate on Trading Time | false | false | false | false | false | false |
| Weighting | true | true | true | true | true | **false** ⚠ |
| Roll Method | Forward Vol | Forward Vol | Forward Vol | Forward Vol | Forward Vol | **Calendar Vol Const** ⚠ |
| Granularity | Continuous | Continuous | Continuous | Continuous | Continuous | Continuous |
| DateCut of Expiries | Lima 17:00 | Lima 17:00 | Lima 17:00 | Lima 17:00 | Lima 17:00 | Lima 17:00 |
| FXDate (panel) | 01/09/2026 | 02/09/2026 | 02/09/2026 | 02/09/2026 | 02/09/2026 | 01/09/2026 |

Dos pares se salen del patrón, por razones distintas: **USD/PEN** en la convención de
delta, y **EUR/USD** en cinco parámetros de cálculo.

---

## 3. Hallazgo 1 (corregido) — USD/PEN es el único con convención de mercado emergente

Con el archivo de USD/PEN a la vista, la conclusión anterior cambia: **no es una política
uniforme de la casa**. USD/PEN está en `0D / 0D` y los otros cuatro pares emergentes en
`1Y / 10Y`.

| Par | Spot Delta Last Tenor | ATM Zero Straddle Last Tenor | Lo que dice el manual |
|---|---|---|---|
| **USD/PEN** | **0D** | **0D** | ✔ consistente con "0D para pares emergentes" |
| USD/MXN | 1Y | 10Y | ⚠ patrón G10 |
| USD/BRL | 1Y | 10Y | ⚠ el manual da **0D/0D a USDBRL por nombre** |
| USD/CLP | 1Y | 10Y | ⚠ patrón G10 |
| USD/COP | 1Y | 10Y | ⚠ patrón G10 |
| EUR/USD | 1Y | 10Y | ✔ correcto para G10 |

El manual:

> *"Spot Delta Last Tenor... Typically this is 1Y for G10 pairs and **0D (zero days) for
> emerging market pairs**"* · *"ATM Zero Straddle Last Tenor... 10Y for G10 pairs and **0D
> for emerging market pairs**"*

y su tabla asigna explícitamente **`USDBRL → 0D / 0D`**.

**La lectura es incómoda pero clara**: el único par configurado según la recomendación del
manual para emergentes es **USD/PEN — el mercado local**, el que la mesa de Lima conoce
mejor. El par que el manual nombra literalmente, **USD/BRL, está en el patrón contrario**.
Junto con el nombre distinto de la superficie de PEN (`OTCDD Strategies` vs `FWD VOL` /
`VOL SURFACE`), la hipótesis más simple es que USD/PEN se configuró con cuidado y los
otros cuatro emergentes se armaron desde una plantilla G10.

**Efecto**: con 0D/0D todos los expiries usan forward delta y el ATM es ATM-forward; con
1Y/10Y usan spot delta hasta 1Y y zero-delta straddle en todo el rango. **Cambia el strike
de cada punto del smile**, es decir el nivel de toda la superficie en el eje de strikes,
y por lo tanto la vol que se le asigna a cada opción del portafolio.

**Prueba concreta que el Módulo 2 puede correr**: construir USD/MXN con 1Y/10Y y con
0D/0D, y medir la diferencia en NPV y Griegas del portafolio real. Eso convierte el
hallazgo de "una configuración se aparta de la documentación" en "se aparta y el impacto
es de X dólares", que es lo que hace falta para el informe de validación.

Lo mismo aplica, ahora sí uniformemente en los seis pares, a `Strangle/Fly Quotes = 2vol
(CP Avg)` (el manual dice *"almost always 1vol (Broker)"*) y a `Up`/`Down Extrap = 1.0 /
1.0` (recomendados 2.0 / 0.0). Esas dos sí son decisiones transversales.

---

## 4. Hallazgo 5 — EUR/USD tiene cinco desviaciones

### 4.1 `Quotes are Delta with Premium = true` — contradice al manual explícitamente

> *"...'false' for pairs where the premium is conventionally paid in the second currency
> (e.g., INRJPY, where premium is paid in yen; or **EURUSD, where premium is paid in
> USD**)."*

Y la tabla: **`EURUSD, GBPUSD → Delta w/Premium = False`**. La superficie está en `true`.

En los otros cinco el `true` **sí es correcto**: en USD/MXN, USD/PEN, USD/BRL, USD/CLP y
USD/COP la prima se paga en USD, que ahí es la **primera** divisa. La regla se cumple en
cinco y falla solo donde USD es la segunda.

**Por qué importa**: el delta premium-adjusted descuenta la prima del delta. Usarlo donde
no corresponde corre el strike de cada punto de delta, y el efecto crece con la vol y el
plazo.

### 4.2 `Volatility Day Count` vacío

Los otros cinco tienen `ACT/365`. El manual lo define como *"the daycount used to convert
the points for interpolating volatility"*. Un campo vacío es un hueco: hay que averiguar
qué aplica el motor por defecto, porque de eso depende el tiempo a expiry de toda la
superficie.

### 4.3 `Interpolate Outright Variance = false`

Los otros cinco en `true` (valor recomendado). EUR/USD interpola sobre **volatilidad** en
vez de sobre varianza total σ²·t. No es cosmético: interpolar vol linealmente entre dos
tenores no da lo mismo que interpolar varianza, y puede introducir arbitraje de calendario.

### 4.4 `Weighting = false`, y el archivo sin columnas de trading time

Los otros cinco en `true`. Coherentemente, el export de EUR/USD **no trae `Trade Days` ni
`Trade Vol`** — 9 columnas contra 11:

```
EUR/USD  : Term;Exp;Day;Cal Days;RR25;RR10;ATM;BF25;BF10
los otros: Term;Exp;Day;Cal Days;Trade Days;Trade Vol;RR25;RR10;ATM;BF25;BF10
```

No afecta el cálculo (los seis tienen `Interpolate on Trading Time = false`), pero **el
loader debe aceptar las dos cabeceras**.

### 4.5 `Roll Method = Calendar Volatility Constant`

Los otros cinco en `Forward Volatility`, que es lo que recomienda el manual:
*"Calendar Vol Constant doesn't adequately reflect how the market behaves when the date
rolls, especially in the short end."*

### 4.6 Interpretación

Las cinco desviaciones apuntan en la misma dirección: EUR/USD parece **configurada a
medias** respecto de las otras cinco. Vale confirmar si tiene exposición material; si la
tiene, revisar las cinco empezando por `Delta with Premium`, que es la que el manual
contradice por nombre.

---

## 5. Hallazgo 7 — cerrado: `xBF10` / `xRR10` no se consideran

El pantallazo de la pestaña Quotes de EUR/USD muestra dos columnas que no están en el CSV
exportado (`xBF10`, `xRR10`), con valores de otro orden de magnitud que los `BF10`/`RR10`
normales. **Confirmado con el usuario: no entran al cálculo y no hay que considerarlas.**
Los campos exportados en los CSV de cotizaciones son exactamente los que alimentan la
superficie:

```
Term ; Exp ; Day ; Cal Days ; [Trade Days ; Trade Vol ;] RR25 ; RR10 ; ATM ; BF25 ; BF10
```

El loader del Módulo 2 ignora cualquier columna fuera de esa lista.

---

## 6. Hallazgo 8 — el selector de lado está en la pestaña Quotes (resuelve la duda del §8.3)

El mismo pantallazo aclara algo que había quedado abierto en la metodología: la pantalla
tiene **dos selectores distintos**.

- El de arriba, junto al Name, es el **quote set**: `LAST` en las seis superficies.
- El de la pestaña Quotes tiene su propio par de desplegables: el date cut (`Lima 17:…`) y
  **el lado: `MID`**, más un botón `RR Call`.

O sea que el `LAST` de arriba no contradice que los quotes sean MID: son cosas distintas,
y el desplegable de la pestaña Quotes es exactamente el mecanismo con el que se exportan
BID y ASK. Esto confirma el diseño de bid/mid/ask de la sección 8 de la metodología: un
archivo por lado, misma cabecera.

Queda una pregunta menor: qué significa el botón **`RR Call`** (probablemente alterna la
convención de signo del risk reversal entre "call menos put" y su inversa). Si alterna el
signo, es crítico saber en qué estado se exportó, porque invertiría el skew completo.

---

## 7. Hallazgo 6 — los exports no son un snapshot único

| Par | Fecha implícita en los quotes (`Exp` − `Cal Days`) | `FXDate` del panel | Título de la ventana |
|---|---|---|---|
| USDMXN | 01/09/2026 | 01/09/2026 ✔ | 01/09/26 16:00 |
| USDPEN | 01/09/2026 | **02/09/2026** ✗ | 01/09/26 16:00 |
| USDBRL | 01/09/2026 | **02/09/2026** ✗ | 01/09/26 16:00 |
| USDCLP | 01/09/2026 | **02/09/2026** ✗ | 01/09/26 16:00 |
| USDCOP | 01/09/2026 | **02/09/2026** ✗ | 01/09/26 16:00 |
| EURUSD | 01/09/2026 | 01/09/2026 ✔ | 01/09/26 16:00 |

Los seis archivos de quotes están anclados a **01/09/2026** (verificado tenor por tenor), y
los seis títulos de ventana dicen `01/09/26 16:00:00`. El `FXDate` del panel de cuatro de
ellos dice 02/09 — consistente con que *"The FX Date defaults to the current calendar
date"* y esos se exportaron el 02/09.

Es benigno, pero **obliga a fijar la fecha de valuación en el YAML** (01/09/2026) en vez de
leerla del `FXDate` de cada archivo.

---

## 8. Grillas de tenores — no son iguales entre pares

| Par | # | Tenores |
|---|---:|---|
| USDMXN | 12 | 1W, 2W, 3W, 1M, 2M, 3M, 4M, 6M, 9M, 1Y, 18M, 2Y |
| USDPEN | 10 | 1W, 2W, 3W, 1M, 2M, 3M, 6M, 9M, 1Y, 2Y |
| USDBRL | 13 | **O/N**, 1W, 2W, 3W, 1M, 2M, 3M, 4M, 6M, 9M, 1Y, 18M, 2Y |
| USDCLP | 13 | **1D**, 1W, 2W, 3W, 1M, 2M, 3M, 4M, 6M, 9M, 1Y, 18M, 2Y |
| USDCOP | 13 | **1D**, 1W, 2W, 3W, 1M, 2M, 3M, 4M, 6M, 9M, 1Y, 18M, 2Y |
| EURUSD | 13 | **1D**, 1W, 2W, 3W, 1M, 2M, 3M, 4M, 6M, 9M, 1Y, 18M, 2Y |

1. **El overnight se escribe distinto**: `O/N` en BRL, `1D` en CLP/COP/EUR. Hay que
   normalizar (el Módulo 1 ya tropezó con algo parecido: el YAML convierte `ON` sin
   comillas en booleano — ver `norm_tenor` en `quotes_loader.py`).
2. **USD/MXN y USD/PEN no tienen punto overnight**, y USD/PEN tampoco 4M ni 18M. El manual
   pide como mínimo *"O/N, 1W, 1M, 2M, 3M, 6M, and 1Y points in every currency pair"*. Sin
   punto O/N, **el tramo muy corto queda gobernado por el 1W**, que es donde la vol se
   mueve más.
3. **Las fechas de expiry difieren entre pares para el mismo tenor** (2M: 29/10 en
   MXN/BRL/COP, 30/10 en PEN/CLP; 4M: 30/12 en MXN/BRL/COP, 29/12 en CLP) — consistente con
   los distintos `Holidays` de cada superficie. El calendario tiene que ser configurable
   por superficie, igual que en el Módulo 1.

---

## 9. Chequeos de sanidad — los seis pares pasan

| Par | Tenores | Varianza total creciente | BF25 > 0 | BF10 > BF25 | Signo del RR | σ mínima |
|---|---:|---|---|---|---|---:|
| USDMXN | 12 | ✔ | ✔ | ✔ | todos + | 4.603 |
| USDPEN | 10 | ✔ | ✔ | ✔ | todos + | 6.178 |
| USDBRL | 13 | ✔ | ✔ | ✔ | todos + | 9.360 |
| USDCLP | 13 | ✔ | ✔ | ✔ | todos + | 9.646 |
| USDCOP | 13 | ✔ | ✔ | ✔ | todos + | 13.313 |
| EURUSD | 13 | ✔ | ✔ | ✔ | **todos −** | 3.812 |

- Sin arbitraje de calendario en el pilar ATM en ningún par.
- Convexidad bien formada en los seis (BF positivos, BF10 > BF25 siempre).
- **EUR/USD es el único con RR negativo**, y es lo correcto: el mercado paga por puts de
  EUR. La fórmula `σ_c = ATM + BF + RR/2` maneja el signo sola, sin casos especiales.
- Ninguna σ se acerca a cero: el riesgo que advierte el manual sobre `Down Extrap ≠ 0` no
  se materializa en ningún par.
- **Underlyings**: los seis cuadran, 5 instrumentos por tenor (60, 50, 65, 65, 65, 65), y
  el número de tenores coincide con el de los quotes. Los Ids no son correlativos en
  ninguno — el emparejamiento tiene que ser por descripción.

### Desfase de trading time, consistente entre pares

Desfase `Trade Days` − `Cal Days`, en horas (`·` sin desfase, `–` tenor inexistente):

| Par | O/N | 1D | 1W | 2W | 3W | 1M | 2M | 3M | 4M | 6M | 9M | 1Y | 18M | 2Y |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| USDMXN | – | – | · | · | · | · | · | 1.01 | 1.01 | 1.01 | · | · | 1.01 | 1.01 |
| USDPEN | – | – | · | · | · | · | 1.01 | 1.01 | – | 1.01 | · | · | – | 1.01 |
| USDBRL | · | – | · | · | · | · | · | 1.01 | 1.01 | 1.01 | · | · | 1.01 | 1.01 |
| USDCLP | – | · | · | · | · | · | 1.01 | 1.01 | 1.01 | 1.01 | · | · | 1.01 | 1.01 |
| USDCOP | – | · | · | · | · | · | · | 1.01 | 1.01 | 1.01 | · | · | 1.01 | 1.01 |

El desfase aparece en todos los expiries **a partir del 30/10/2026** y en ninguno anterior
— por eso PEN y CLP lo tienen en 2M (expiry 30/10) y MXN, BRL y COP no (expiry 29/10). Es
el mismo cruce de cambio de horario en todos, como describe el manual §1.1.

> Detalle abierto: el desfase es **0.042 días** exactos según el CSV, mientras que una hora
> son 1/24 = 0.041667. Puede ser redondeo a 5 decimales o una cantidad genuinamente
> distinta. Como los seis tienen `Interpolate on Trading Time = false`, **no entra a la
> interpolación** y no bloquea el Módulo 2.

---

## 10. Consecuencias para el diseño de `vollib`

1. **Un motor sirve para los seis pares**: mismo generador (`FXOption`) y mismo
   interpolador (`Interpolator3DSpline1D`). No hay que implementar SABR ni extrapolación
   lineal.
2. **Un YAML por superficie, con todos los parámetros por par** — USD/PEN y EUR/USD
   prueban que no se puede asumir uniformidad. Parametrizar: `delta_with_premium`,
   `spot_delta_last_tenor`, `atm_zero_straddle_last_tenor`, `strangle_fly_convention`,
   `interpolate_outright_variance`, `up_extrap`, `down_extrap`, `vol_day_count`,
   `roll_method`, `holidays`.
3. **El loader acepta las dos cabeceras** (con y sin `Trade Days`/`Trade Vol`) y normaliza
   `O/N` ≡ `1D`.
4. **La fecha de valuación se fija en el YAML**, no se lee del `FXDate` (§7).
5. **El calendario de expiries es por par**, y hay que confirmar si manda el campo
   `Holidays` de la superficie o los Currency Defaults (§1).
6. **El signo del RR no necesita caso especial**: la misma álgebra sirve para EUR/USD
   (RR < 0) y para los cinco USD/EM (RR > 0).
7. **Validación de entrada obligatoria** en el loader, replicando los chequeos de §9:
   varianza total creciente, BF > 0, BF10 > BF25, y coincidencia de la grilla de tenores
   entre underlyings y quotes. Los seis pasan hoy; el punto es que el motor avise el día
   que no.
8. **Poder correr el mismo par con dos convenciones** (`0D/0D` vs `1Y/10Y`) para cuantificar
   el Hallazgo 1 en dólares sobre el portafolio real. Es la prueba que convierte el
   hallazgo en un resultado de validación.

---

## 11. Qué falta para construir el módulo

### Bloqueante — una sola cosa

**Spot FX y curvas de descuento al 01/09/2026**, en el entorno `Risk - EOD With SOFR`.
Sin esto no se puede hacer la conversión delta→strike, que es el corazón del módulo: cada
punto del smile viene dado en delta, y para saber a qué strike corresponde hace falta el
forward, que sale del spot y de las dos curvas de descuento del par.

Concretamente:

| Insumo | Detalle |
|---|---|
| Spots al 01/09/2026 16:00 | USDMXN, USDPEN, USDBRL, USDCLP, USDCOP, EURUSD |
| Curva USD | `USD_SOFR` |
| Curvas de la divisa local | MXN, PEN, BRL, CLP y COP colateralizadas en USD SOFR |
| Curva EUR | la que use la superficie EUR/USD en ese entorno |

Dos caminos, y **conviene el primero para arrancar**:

1. **Exportarlas de Calypso** (mismo formato de `Df` que ya se usó para validar curvas).
   Ventaja: la conversión delta→strike usa exactamente los mismos descuentos que usó
   Calypso, así que cualquier diferencia en la superficie es atribuible al smile y no a la
   curva. Es el mismo aislamiento de variables que se hizo en la validación de opciones.
2. **Construirlas con el Módulo 1** (`curvelib`) para el 01/09/2026 — requiere las hojas de
   quotes de curvas de esa fecha. Es el paso siguiente natural, una vez que (1) valide el
   smile.

### No bloquea la construcción, pero sí la validación

**Pestañas `Points` y `Surface`** de al menos un par (idealmente USD/MXN). Es el
equivalente al `Df Mid` que se usó para validar las curvas pilar por pilar: sin ellas se
puede reproducir los quotes de entrada, pero no demostrar que la superficie interpolada
coincide con la de Calypso, ni confirmar la fórmula exacta de los puntos ad-hoc de
extrapolación (§Hallazgo 3 de la metodología).

### Aclaraciones menores

1. **Cuál archivo de EUR/USD salió mal.** Si fue el CSV de parámetros, cuatro de las cinco
   desviaciones del §4 (day count vacío, `Interpolate Outright Variance`, `Weighting`,
   `Roll Method`) podrían ser artefacto del export y no configuración real. La quinta
   —`Quotes are Delta with Premium = true`— se ve también en el pantallazo, así que esa se
   sostiene igual.
2. **Pantalla Definition de EUR/USD** (`Generator`, `Interpolator`, `Holidays`) — para
   confirmar que también usa `FXOption` + `Interpolator3DSpline1D` como los otros cinco.
3. **Botón `RR Call`** de la pestaña Quotes: probablemente alterna la convención de signo
   del RR. Riesgo bajo — los datos exportados ya son consistentes con la convención
   estándar (RR = call − put: negativo en EUR/USD, positivo en los cinco USD/EM, que es lo
   que corresponde a cada mercado). Solo conviene confirmarlo para dejarlo documentado.

### Lo que NO hace falta

- **Las fechas de expiry** vienen dadas en la columna `Exp` de cada archivo de quotes, así
  que para construir estas seis superficies a esta fecha **no hace falta resolver si manda
  el campo `Holidays` o los Currency Defaults**. Ese punto solo se vuelve necesario el día
  que haya que generar expiries para una fecha nueva sin export de Calypso.
