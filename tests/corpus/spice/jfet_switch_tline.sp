* JFET buffer driving a switched transmission line
VDD vdd 0 DC 15
VEE 0 vee DC 5
V1 in 0 SIN(0 100m 1MEG)
J1 vdd in src JFETN
RS src 0 2.2k
J2 vee src pout PJFETP
RP pout vee 2.2k
SW1 src tap ctrl 0 SWMOD
VC ctrl 0 PULSE(0 5 0 1n 1n 1u 2u)
T1 tap 0 out 0 Z0=50 TD=10n
RT out 0 50
.model JFETN NJF (VTO=-2 BETA=1m LAMBDA=0.01)
.model PJFETP PJF (VTO=2 BETA=1m LAMBDA=0.01)
.model SWMOD SW (RON=1 ROFF=1MEG VT=2.5 VH=0.2)
.tran 1n 5u
.end
