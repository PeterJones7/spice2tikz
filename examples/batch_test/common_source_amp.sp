* NMOS common-source amplifier
VDD vdd 0 DC 5
V1 in 0 AC 1 SIN(0 10m 1k)
RD vdd out 4.7k
M1 out in 0 0 NMOSMOD L=1u W=10u
.model NMOSMOD NMOS (VTO=0.7 KP=120u LAMBDA=0.02)
.ac dec 20 1 1MEG
.end
