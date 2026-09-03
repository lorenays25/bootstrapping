"""Corre las tres comparaciones del Modulo 3 sobre un export de Calypso.

    python impact_engine/examples/valorizar.py <portafolio.csv> [YYYY-MM-DD]

Sin fecha usa la de hoy, igual que la interfaz.
"""
import os, sys, csv, datetime as dt
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT,"src"))
from impactlib.portfolio import load
from impactlib import escenarios as esc, report
from impactlib.core import spot_date
from impactlib.products import REGISTRO

CSV = sys.argv[1] if len(sys.argv)>1 else None
if not CSV or not os.path.exists(CSV):
    print(__doc__); sys.exit(1)
VAL = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv)>2 else dt.date.today()
SD  = spot_date(VAL)

# Spots calibrados contra el propio reporte. El export de Calypso trae el tipo
# de cambio con dos decimales y eso se propaga a todo.
SPOTS = {"USD/PEN":3.36564,"USD/MXN":17.0033,"USD/BRL":5.14525,
         "USD/CLP":936.60,"USD/COP":3167.45}
SPOTS_VOL = {"USDPEN":3.365,"USDMXN":16.993,"USDBRL":5.1550,
             "USDCLP":937.0,"USDCOP":3167.0}

rows, descartes = load(CSV)
print(f"{len(rows)} operaciones | valorizacion {VAL} | fecha spot {SD}")
print("productos soportados: " + ", ".join(p.etiqueta for p in REGISTRO))
if descartes:
    print("descartadas: " + "; ".join(f"{k} ({v})" for k,v in descartes.items()))

CAMPOS = report.CAMPOS
res = {}
for clave in esc.CLAVES:
    feed, avisos = esc.armar(clave, rows, VAL, SD, SPOTS, spots_vol=SPOTS_VOL)
    filas = report.run(rows, feed)
    res[clave] = (report.resumen(filas), filas, feed, list(avisos)+list(feed.avisos()))

for clave in esc.CLAVES:
    resumen, filas, feed, avisos = res[clave]
    titulo, detalle = esc.DESCRIPCION[clave]
    print("\n" + "="*94)
    print(f"{titulo}   [{len(filas)} operaciones, factores: {feed.nombre}]")
    print(detalle)
    print("="*94)
    for a in avisos: print(f"  ! {a}")
    print(f"{'par':9}" + "".join(f"{c:>13}" for c in CAMPOS))
    for s in resumen:
        print(f"{s['pair']:9}" + "".join(
            (f"{s[c]['mediana']:12.3f}%" if s[c] else f"{'-':>13}") for c in CAMPOS))

# El detalle se exporta del escenario mas completo que haya corrido.
filas = res[esc.CLAVES[-1]][1]
if filas:
    out=os.path.join(ROOT,"examples","output"); os.makedirs(out,exist_ok=True)
    dest=os.path.join(out,"detalle_por_operacion.csv")
    with open(dest,"w",newline="",encoding="utf-8-sig") as fh:
        w=csv.DictWriter(fh,fieldnames=list(filas[0]),delimiter=";")
        w.writeheader(); w.writerows(filas)
    print(f"\ndetalle por operacion -> {dest} ({len(filas)} filas)")
print("\nMediana del error relativo contra Calypso, sobre operaciones materiales:")
print(f"  |PV| >= {report.MIN_PV:.0f}, |delta| y |gamma| >= {report.MIN_DELTA_PCT:.0%} "
      f"del nocional, resto >= {report.MIN_GRIEGA:.0f}.")
