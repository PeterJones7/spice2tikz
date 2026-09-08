* Two-stage BJT amplifier (npn and pnp)
VCC vcc 0 DC 12
V1 in 0 AC 1 SIN(0 10m 1k)
CIN in b1 1u
RB1 vcc b1 470k
RB2 b1 0 100k
RC1 vcc c1 4.7k
RE1 e1 0 1k
CE e1 0 10u
Q1 c1 b1 e1 QNPN
Q2 c2 c1 vcc QPNP
RC2 c2 0 2.2k
.model QNPN NPN (BF=200 IS=1e-15 VAF=100)
.model QPNP PNP (BF=150 IS=1e-15 VAF=80)
.tran 10u 5m
.end
