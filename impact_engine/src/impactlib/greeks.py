"""
OBSOLETO — este archivo ya no forma parte del Módulo 3.

Las griegas ahora las calcula cada producto, en
`impactlib.products.base.Product.greeks`, por diferencias finitas sobre su
propio `pv`. Los tamaños de choque están en `impactlib.products.base` como
SHOCK_SPOT, SHOCK_VOL y SHOCK_RATE.

El motivo del cambio: un forward no tiene gamma ni vega, y con una única función
de griegas había que publicarlas como cero. Ahora cada producto declara cuáles
reporta, y las que no declara no aparecen.

Se puede borrar sin consecuencias.
"""
raise ImportError(
    "impactlib.greeks quedó obsoleto. Cada producto calcula sus griegas: usa "
    "impactlib.products.fx_option.FXOptionVanilla.greeks(op, factores) o el "
    "producto que corresponda, que sale de impactlib.products.identificar().")
