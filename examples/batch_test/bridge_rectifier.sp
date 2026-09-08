* Full-wave bridge rectifier
V1 acp acn SIN(0 12 50)
D1 acp outp DBRIDGE
D2 acn outp DBRIDGE
D3 0 acp DBRIDGE
D4 0 acn DBRIDGE
RL outp 0 1k
.model DBRIDGE D (IS=1e-14 N=1.8 RS=0.1)
.tran 100u 100m
.end
