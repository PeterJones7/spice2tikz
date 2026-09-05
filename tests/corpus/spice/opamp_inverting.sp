* Inverting amplifier around a five-terminal op amp
* The `; symbol=opamp` metadata is the only reason this is drawn as a
* triangle rather than a labelled box: nothing guesses from the name.
.subckt lm741 plus minus out vcc vee ; symbol=opamp
.ends lm741
V1 in 0 AC 1
V2 vcc 0 DC 15
V3 vee 0 DC -15
R1 in inv 10k
R2 inv sig 100k
X1 0 inv sig vcc vee lm741
RL sig 0 10k
.end
