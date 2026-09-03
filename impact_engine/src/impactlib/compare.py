"""
OBSOLETO — este archivo ya no forma parte del Módulo 3.

    calypso_forwards()  -> impactlib.feeds.calypso.CalypsoFeed
    price_row()         -> impactlib.report.run(filas, feed)
    _norm_ppf()         -> impactlib.core.numerics.norm_ppf

La comparación ya no es una función con banderas: es la misma valorización
corrida con un feed distinto. Los tres escenarios se arman en
`impactlib.escenarios.armar()`.

Se puede borrar sin consecuencias.
"""
raise ImportError(
    "impactlib.compare quedó obsoleto. Arma el escenario con "
    "impactlib.escenarios.armar(clave, filas, ...) y valorízalo con "
    "impactlib.report.run(filas, feed).")
