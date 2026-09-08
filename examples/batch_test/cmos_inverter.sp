* CMOS inverter with a current-source bias
VDD vdd 0 DC 3.3
VIN in 0 PULSE(0 3.3 0 100p 100p 1n 2n)
MP out in vdd vdd PMOSMOD L=180n W=4u
MN out in 0 0 NMOSMOD L=180n W=2u
IBIAS vdd bias DC 50u
RBIAS bias 0 10k
CL out 0 10f
.model NMOSMOD NMOS (VTO=0.5 KP=250u)
.model PMOSMOD PMOS (VTO=-0.5 KP=100u)
.tran 10p 10n
.end
