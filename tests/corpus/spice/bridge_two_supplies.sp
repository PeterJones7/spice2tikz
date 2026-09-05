* Bridge between two supplies
* Two arms across a 10 V and a 5 V rail. Reported as a bug: the net spines
* were emitted without obstacle checking, so a column ran through the body
* of the resistor standing on it and across a terminal of another net,
* shorting two supplies. The validator saw nothing wrong, because each wire
* was legal on its own.
V1 N1 0 DC 10
V2 N3 0 DC 5
R1 N1 N2 100
R2 N2 N3 220
R3 N1 N4 330
R4 N3 N4 470
.op
.end
