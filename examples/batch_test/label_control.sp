* Metadata control over which labels are drawn
* Each resistor asks for something different; R5 asks for nothing and so
* keeps the default, which is the reference and the value.
V1 in 0 DC 5 ; labels=ref
R1 in a 10k ; labels=ref
R2 a b 22k ; labels=value
R3 b c 33k ; labels=ref,value
R4 c d 47k ; labels=none
R5 d 0 56k
M1 d g 0 0 nfet ; labels=value
V2 g 0 DC 1 ; labels=none
.model nfet nmos
.end
