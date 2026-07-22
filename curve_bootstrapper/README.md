# curvelib — Bootstrapping Multi-Curva / Multi-Colateral

Motor parametrizable de construcción de curvas de descuento y proyección
(28 curvas: RFR OIS, IBOR, cross-currency colateralizadas en USD SOFR,
implícitas FX y UVR). Soporta bid/mid/ask, hoja de quotes estilo pantalla,
e interfaz web conectada al motor. Enfoque híbrido: QuantLib solo para
fechas, scipy/numpy para el motor financiero.

## Inicio rápido

    pip install -r requirements.txt

### Opción A — interfaz web conectada al motor (recomendado)

    python server.py
    # abre http://127.0.0.1:8000

En la interfaz: edita curvas/quotes, pulsa "▶ Construir curvas" y ve las
tablas bid/mid/ask directamente en el navegador; descárgalas como CSV.

### Opción B — línea de comandos

    python examples/run_example.py        # 28 curvas (mid), diagnósticos
    python examples/run_bid_mid_ask.py    # pipeline bid/mid/ask + tabla + CSV

### Opción C — interfaz sin servidor

Abre `ui/parametrizador.html` con doble clic. Funciona para editar la
configuración y cargar quotes, pero el botón "Construir" requiere el
servidor (opción A), porque el bootstrapping corre en Python.

## Estructura

    server.py                FastAPI: conecta la interfaz con el motor
    config/curves.yaml       Parametrización de las 28 curvas
    ui/parametrizador.html   Interfaz web (edición + construcción + tablas)
    src/curvelib/            Librería (dates, curve, instruments, engine,
                             orchestrator, quotes_loader)
    examples/                Scripts de ejemplo + hoja de quotes de muestra
    docs/DOCUMENTACION.md    Documentación completa

Lee `docs/DOCUMENTACION.md`: arquitectura, fórmulas de cada instrumento,
referencia del YAML, bid/mid/ask, y las simplificaciones del esqueleto que
debes conocer antes de producción.
