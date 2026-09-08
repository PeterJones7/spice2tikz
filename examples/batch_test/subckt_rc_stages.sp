* Cascaded RC stages with a nested subcircuit
.subckt rcstage in out
Rs in mid 1k
.subckt rctail a b
Rt a b 2k
Ct b 0 10n
.ends rctail
Cs mid 0 10n
Xtail mid out rctail
.ends rcstage
V1 src 0 AC 1
X1 src outa rcstage
X2 outa outb rcstage
RL outb 0 100k
.end
