"""Pruebas del Modulo 3. Ejecutar:  python impact_engine/tests/test_impactlib.py"""
import os, sys, math, datetime as dt
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT,"src"))
from impactlib.core import Factors, depo_df, depo_rate, spot_date, tau_vol
from impactlib.products import identificar, REGISTRO, SHOCK_SPOT, SHOCK_VOL
from impactlib.products.fx_option import FXOptionVanilla, premium
from impactlib.products.fx_forward import FXForward
from impactlib.portfolio import Trade

FALLOS=[]
def check(nombre, cond, extra=""):
    print(f"  {'ok ' if cond else 'FALLA'}  {nombre}" + (f"   [{extra}]" if extra and not cond else ""))
    if not cond: FALLOS.append(nombre)

VAL=dt.date(2026,9,1); SPOTD=dt.date(2026,9,3)
EXP=dt.date(2027,3,1); DEL=dt.date(2027,3,3)
S,K,vol,rb,rq = 17.0, 17.5, 0.12, 0.037, 0.065
days=(DEL-SPOTD).days
def mercado(**kw):
    d=dict(spot=S, df_base=depo_df(rb,days), df_quote=depo_df(rq,days),
           tau=tau_vol(VAL,EXP), days=days, rate_base=rb, rate_quote=rq, vol=vol)
    d["forward"]=d["spot"]*d["df_base"]/d["df_quote"]
    d.update(kw); return Factors(**d)
mkt = mercado()
def OPT(call, q, k=K): return Trade("t","USD/MXN",call,q,k,EXP,DEL)

print("1. Factor de descuento y forward")
check("depo simple ACT/365", abs(depo_df(0.05,365)-1/1.05)<1e-15)
check("depo a plazo cero = 1", depo_df(0.05,0)==1.0 and depo_df(0.05,-5)==1.0)
check("depo_rate es la inversa de depo_df", abs(depo_rate(depo_df(0.0374,97),97)-0.0374)<1e-12)
check("fecha spot = valorizacion + 2 dias habiles", spot_date(dt.date(2026,9,1))==dt.date(2026,9,3))
check("la fecha spot salta el fin de semana", spot_date(dt.date(2026,9,3))==dt.date(2026,9,7))
check("F = S * Df_base / Df_cotizada",
      abs(mkt.forward - S*depo_df(rb,days)/depo_df(rq,days))<1e-14)
check("tau = ACT/365 valorizacion -> vencimiento",
      abs(mkt.tau - (EXP-VAL).days/365.0)<1e-15)
check("basis = 1 cuando el forward es la paridad", abs(mkt.basis-1.0)<1e-14)
_mb = mercado(forward=mkt.forward*1.00025)
check("bump conserva la base del forward",
      abs(_mb.bump(drate_base=0.01).basis - _mb.basis) < 1e-12)

print("\n2. Paridad put-call")
c=premium(mkt.forward,K,mkt.tau,vol,mkt.df_quote,True)
p=premium(mkt.forward,K,mkt.tau,vol,mkt.df_quote,False)
check("c - p = Df*(F-K)", abs((c-p)-mkt.df_quote*(mkt.forward-K))<1e-13,
      f"{c-p:.12f} vs {mkt.df_quote*(mkt.forward-K):.12f}")

print("\n3. Casos limite")
check("vol -> 0 da el intrinseco descontado",
      abs(premium(mkt.forward,K,mkt.tau,0.0,mkt.df_quote,True)
          - mkt.df_quote*max(mkt.forward-K,0))<1e-15)
check("tau -> 0 da el intrinseco",
      abs(premium(mkt.forward,K,0.0,vol,1.0,False) - max(K-mkt.forward,0))<1e-15)
check("call muy dentro del dinero ~ Df*(F-K)",
      abs(premium(mkt.forward,1.0,mkt.tau,vol,mkt.df_quote,True)
          - mkt.df_quote*(mkt.forward-1.0))<1e-9)
check("call fuera del dinero > 0 y < Df*F",
      0 < premium(mkt.forward,40.0,mkt.tau,vol,mkt.df_quote,True) < mkt.df_quote*mkt.forward)
check("la prima crece con la volatilidad",
      premium(mkt.forward,K,mkt.tau,0.20,mkt.df_quote,True)
      > premium(mkt.forward,K,mkt.tau,0.10,mkt.df_quote,True))

print("\n4. Signo y escala del nocional")
oc, ov = OPT(True, 1_000_000), OPT(True, -1_000_000)
PVO = FXOptionVanilla.pv
check("la venta es el negativo de la compra", abs(PVO(oc,mkt)+PVO(ov,mkt))<1e-9)
check("el PV escala con el nocional",
      abs(PVO(oc,mkt)*2 - PVO(OPT(True,2_000_000),mkt))<1e-9)

print("\n5. Griegas: coherencia con las formulas cerradas")
g=FXOptionVanilla.greeks(oc,mkt)
st=vol*math.sqrt(mkt.tau)
d1=(math.log(mkt.forward/K)+0.5*st*st)/st; d2=d1-st
Nd1=0.5*math.erfc(-d1/math.sqrt(2.0))
an_delta=oc.quantity*mkt.df_base*Nd1
check("delta ~ Q*Df_base*N(d1) (dif. finita con choque de 1%)",
      abs(g["delta"]-an_delta)/abs(an_delta) < 2e-4, f"{g['delta']:.4f} vs {an_delta:.4f}")
an_vega=oc.quantity*mkt.df_quote*mkt.forward*math.exp(-0.5*d1*d1)/math.sqrt(2*math.pi)*math.sqrt(mkt.tau)
check("vega ~ Q*Df*F*n(d1)*sqrt(tau) por punto de vol",
      abs(g["vega"]-an_vega*SHOCK_VOL)/abs(an_vega*SHOCK_VOL) < 2e-3,
      f"{g['vega']:.5f} vs {an_vega*SHOCK_VOL:.5f}")
check("gamma de una call comprada es positiva", g["gamma"]>0)
check("theta de una call comprada es negativa", g["theta"]<0)
check("vega de una call comprada es positiva", g["vega"]>0)
check("PV de la griega coincide con pv()", abs(g["pv"]-PVO(oc,mkt))<1e-12)

print("\n6. Griegas: simetria compra/venta")
gv=FXOptionVanilla.greeks(ov,mkt)
check("todas las griegas invierten el signo",
      all(abs(g[k]+gv[k])<1e-8 for k in ("pv","delta","gamma","vega","theta","rho","rho2")))

print("\n7. Griegas: la paridad put-call se traslada")
op=OPT(False,1_000_000)
gp=FXOptionVanilla.greeks(op,mkt)
check("delta_call - delta_put = Q*Df_base",
      abs((g["delta"]-gp["delta"]) - oc.quantity*mkt.df_base) < 1e-3,
      f"{g['delta']-gp['delta']:.4f} vs {oc.quantity*mkt.df_base:.4f}")
check("gamma_call = gamma_put", abs(g["gamma"]-gp["gamma"])/abs(g["gamma"])<1e-10)
check("vega_call = vega_put", abs(g["vega"]-gp["vega"])/abs(g["vega"])<1e-10)

print("\n8. El choque partido en dos importa")
def delta_adelante():
    up = mkt.bump(spot_mult=1+SHOCK_SPOT)
    return (PVO(oc,up)-PVO(oc,mkt))/(S*SHOCK_SPOT)
check("la diferencia adelantada se aparta mas de la formula cerrada",
      abs(delta_adelante()-an_delta) > 5*abs(g["delta"]-an_delta),
      f"adelante {abs(delta_adelante()-an_delta):.4f} vs central {abs(g['delta']-an_delta):.4f}")

print("\n8-bis. Forward FX: lineal, sin gamma ni vega")
fw = Trade("f","USD/MXN",True,1_000_000,K,EXP,DEL)
gf = FXForward.greeks(fw,mkt)
check("PV = Q*Df*(F-K)", abs(gf["pv"] - 1_000_000*mkt.df_quote*(mkt.forward-K))<1e-9)
check("delta = Q*Df_base", abs(gf["delta"] - 1_000_000*mkt.df_base)/(1_000_000*mkt.df_base)<1e-10)
check("gamma exactamente cero", gf["gamma"]==0.0)
check("vega exactamente cero", gf["vega"]==0.0)
check("el forward no declara gamma ni vega",
      "gamma" not in FXForward.griegas and "vega" not in FXForward.griegas)
check("el PV del forward no depende de la volatilidad",
      abs(FXForward.pv(fw,mkt) - FXForward.pv(fw,mkt.bump(dvol=0.20)))<1e-12)
check("paridad: call - put = forward",
      abs((PVO(oc,mkt)-PVO(op,mkt)) - FXForward.pv(fw,mkt))<1e-6)

print("\n8-ter. Registro de productos")
check("una vanilla se reconoce",
      identificar({"Product Description":"FXOption/VANILLA/(C)USD/PEN(P)/BUY/x"}) is FXOptionVanilla)
check("un forward se reconoce",
      identificar({"Product Description":"FXForward/NDF/USD/PEN"}) is FXForward)
check("un producto no soportado devuelve None",
      identificar({"Product Description":"IRSwap/FIXFLOAT/USD"}) is None)
check("las claves del registro no se repiten",
      len({p.clave for p in REGISTRO})==len(REGISTRO))
check("cada producto declara sus columnas obligatorias",
      all(p.OBLIGATORIAS and all(k in p.COLUMNAS for k in p.OBLIGATORIAS) for p in REGISTRO))
check("una opcion se llama CALL/PUT y un forward Outright",
      FXOptionVanilla.tipo_texto(oc)=="CALL" and FXForward.tipo_texto(fw)=="Outright")

print("\n8-quater. Cada producto lee su propio export")
_opc = {"Product Description":"FXOption/VANILLA/(C)USD/PEN(P)/BUY/x","Ccy Pair":"USD/PEN",
        "Put/Call":"CALL USD","Quantity":"1000000","Strike":"3.40",
        "Expiry Date":"01/12/2026","Delivery Date":"03/12/2026"}
_fwd = {"Product Description":"FXForward/OUTRIGHT/USD/PEN","Ccy Pair":"USD/PEN",
        "Buy/Sell":"Sell","Quantity":"2000000","Fwd Rate":"3.38",
        "Delivery Date":"03/12/2026"}
t,fa = FXOptionVanilla.leer(_opc)
check("la opcion se lee completa", t is not None and not fa and t.strike==3.40 and t.call)
t,fa = FXForward.leer(_fwd)
check("el forward se lee con su propio alias de tasa", t is not None and t.strike==3.38)
check("el forward toma el sentido de Buy/Sell", t is not None and t.quantity==-2_000_000)
t,fa = FXForward.leer({"Ccy Pair":"USD/PEN","Quantity":"2000000"})
check("un forward sin tasa ni entrega dice cuales faltan",
      t is None and set(fa)>={"Strike","Delivery Date"}, str(fa))
# Una fila de opcion SI se puede leer como forward: tiene par, cantidad, strike
# y entrega. Lo que impide mezclarlas no es `leer` sino `reconoce`, y por eso el
# lector del portafolio siempre pregunta primero.
check("una fila de opcion no la RECONOCE el forward",
      not FXForward.reconoce(_opc))
t,fa = FXOptionVanilla.leer({"Ccy Pair":"USD/PEN","Quantity":"1000"})
check("una opcion sin strike ni fechas dice cuales faltan",
      t is None and set(fa)>={"Strike","Expiry Date","Delivery Date"}, str(fa))
check("el reconocimiento no confunde los dos productos",
      identificar(_opc) is FXOptionVanilla and identificar(_fwd) is FXForward)

print("\n9. Lectura del portafolio de Calypso")
from impactlib.portfolio import load
CSV=os.environ.get("PORTAFOLIO","")
if CSV and os.path.exists(CSV):
    rows,descartes=load(CSV)
    check("se leen operaciones", len(rows)>0, str(len(rows)))
    check("cada fila trae su producto", all(r.producto is not None for r in rows))
    check("todas tienen vencimiento y entrega",
          all(r.opt.expiry and r.opt.delivery for r in rows))
    check("la entrega nunca precede al vencimiento",
          all(r.opt.delivery>=r.opt.expiry for r in rows))
    check("hay compras y ventas",
          any(r.opt.quantity>0 for r in rows) and any(r.opt.quantity<0 for r in rows))
else:
    print("  (omitido: exporta PORTAFOLIO=<ruta al csv> para correr este grupo)")

print("\n"+"="*66)
print("RESULTADO: TODO OK" if not FALLOS else f"RESULTADO: {len(FALLOS)} FALLAS -> {FALLOS}")
sys.exit(1 if FALLOS else 0)
