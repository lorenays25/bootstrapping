"""
OBSOLETO — este archivo ya no forma parte del Módulo 3.

Su contenido se repartió cuando el módulo se separó en capas:

    premium(), pv()          -> impactlib.products.fx_option
    depo_df(), tau           -> impactlib.core.conventions
    Market                   -> impactlib.core.market.Factors
    Option                   -> impactlib.portfolio.Trade

Se deja el archivo con este aviso, en vez de borrarlo, para que cualquier script
viejo que lo importe falle en el import con una explicación en lugar de correr
con código muerto y devolver números que nadie sabría de dónde salieron.

Se puede borrar sin consecuencias.
"""
raise ImportError(
    "impactlib.pricing quedó obsoleto. Usa impactlib.products.fx_option para la "
    "valorización, impactlib.core.conventions para las convenciones de fecha y "
    "descuento, e impactlib.core.market.Factors para los factores de riesgo.")
