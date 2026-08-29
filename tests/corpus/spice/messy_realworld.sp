Messy hand-written deck
* full-line comment, column 1
   * indented comment line

.PARAM rload=1k
.include devices.lib
V1  IN   0   DC 0  AC 1
* a comment may sit between a card and its continuation
+  SIN(0 1
+       1k)          ; inline semicolon comment
R1   IN    Out   10K        $ dollar-style comment
CLoad    Out  0   1u
Zq1  Out 0 weird
.WRDATA out.csv v(out)
.TRAN  1u 1m
.ac dec 10 1 100k
.print tran V(out)
.END
this trailing junk after .end is ignored
R99 nowhere 0 1
