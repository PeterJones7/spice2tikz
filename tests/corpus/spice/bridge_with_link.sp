* Bridge between two supplies, with the midpoints linked
* The same shape as bridge_two_supplies with a fifth resistor across the two
* midpoints, which gives the router a long horizontal part for other nets to
* cross. Crossing a lead is fine; crossing the drawn body is not.
V1 N1 0 DC 10
V2 N3 0 DC 5
R1 N1 N2 1k
R2 N2 N3 1k
R3 N1 N4 1k
R4 N4 N3 1k
R5 N2 N4 1k
.op
.end
