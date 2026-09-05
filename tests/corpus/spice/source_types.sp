* Every source specification, and the symbol each one gets
* A reader should see that a source is a stimulus without reading its label.
* Sinusoidal and pulsed sources get their own symbols; the ones circuitikz
* has no shape for keep the plain one, and their waveform belongs in the
* caption. Note V4: a DC level with a small signal on it is a biased supply,
* not a stimulus, so it stays plain too.
V1 a 0 DC 5
R1 a 0 1k
V2 b 0 SIN(0 1 1k)
R2 b 0 1k
V3 c 0 PULSE(0 5 0 1n 1n 1u 2u)
R3 c 0 1k
V4 d 0 DC 5 AC 1
R4 d 0 1k
V5 e 0 PWL(0 0 1u 5)
R5 e 0 1k
I1 f 0 DC 1m
R6 f 0 1k
I2 g 0 SIN(0 1m 1k)
R7 g 0 1k
I3 h 0 PULSE(0 1m 0 1n 1n 1u 2u)
R8 h 0 1k
.end
