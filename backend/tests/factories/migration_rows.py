"""Builders for rows used while exercising historical schema migrations."""

from __future__ import annotations

import base64
import zlib
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, MetaData, Numeric, Table

RELEASED_V0121_REVISION = "e7b4c1d9a6f2"

# PostgreSQL fresh installs at v0.12.1 were built with ``create_all`` from the
# released models and then stamped, not bootstrapped through the SQLite-authored
# baseline migration. This compressed DDL is an exact ``create_all`` render from
# tag v0.12.1 (afbe54dd430d3983698315fc3229b906de3a01b2). Keeping the snapshot here
# makes the upgrade proof independent of Git tags and checkout depth in CI.
_V0121_POSTGRES_DDL_B85 = (
    "c-rk<S##S+l71fl3dX$Hj?jbKeZ+1|>_pHKWOFT%8j@<a=LG>05QR1fuyN?<U%y#*WnBOyxm(_zCsC-Xudd9jtgOtc<7G5mN0asE"
    "i)eC{7g>9M$R^X(B${8IPhP!Tt<PQ_O<t})ETbqsKPA84A1_WK@?-Jtf61TKI=XoI`X4V|92-z|c9S=GRW@zXc8v!5alZI8kKZpA"
    "C#3v5icVJXI$EvCf2Y&gSwz22m&YGwKf`Ymds`Jnwrv5qt_r=eKhL61q-1n5qgd0E^V!@go>tp#pOx*GyiB#i=hNlK6KEGHcyU5|"
    "x>$dRmR9*gotJG^AL{BRPqSKwSS;qt=|?C7h2Krj-(ALMQwF%aTuskr^N+N7XA$&0K91(=>E&`pYdT&pE|#<T+E<%3hL>|XinQ?Q"
    "DL~UHfr2NWS%m;Lo4=>Or<W@>q|tJ@usgI*pn^OpnyRa}85)xL<@8K-W_rFN|Hy8}tBb|r408D)i>f6}*HO<F(-YpD^NTfYt>}!W"
    "^PXvD0|q~i7xVRUaps@$><&=Mq$u)Doz(ZYNxQw?SE&v<y<9J7n|_9AS@B`*15%V|pq<a?NE7aWNjQu8fSPS$v&rJ4nlm!Dm-D%t"
    "(dBvuGh?FGRoCX_u4!u+^4-02Do>)*>E#(6x3g%vTF37$SD!7UU#kraepTn~J(@Z?@bks;oPhyZe{k^U{*bi!CNH4$p(^sNj<K4a"
    "Qc|6s0a|=MTb)nW#~<8N53Mk%=U<o6C8M6p3qnbJL{@7U687Cq`p1j&i?fJlIiAjsqcbjNxWq9VP#rQD#j4&x{bnQPi*>w={&hK9"
    "@+zabXvi`mqsl&ih|X->o1`n+q)YSGpc#NWrZawed3F{kR(GY@9=(1=C8N8n>S(f?%$WPx{FE}tr|D8wu$--D$J0<_uRzFBio^|G"
    "K#SREtrllLvqsI{16?^<m6x09j>#rNWM3|sa9S=tt(eedG(p+smvyw9JK9F!@;2L>Z91M_tS|Y5vUbTS6%uE5ez91tUBj!EtrMW="
    "*=ptJNLjV{RlZG#yl9hpm$h2$pQ3jk7L4No;drs6U~95gET`v`>(-~AgP1U9WoxY8lo?{wRQ5?Vm}k-HgMI?}E>@OMt!Y-|KrHu`"
    "_|k}Sg?_FL5|q%>cV|>{NXYUW!vagRh-UKY#ozLD0y7LEZvw10nFE6yO<w%1gp8A)iJmZBzWVO%f4xSjVhxJvkcezQ{qPzkg7^iR"
    "(m_b_wrZdOSsj1bO-_NN*0j0`B!S3EJ~W4_D)vbk%Qizh$;iErXnLBJ4KPu>i?{pl6;7H%W~e^i@72d`ok5e*IB6&A**Pdf)ANhT"
    "r`h_$;&M&DO#Z!?N2r6{Ast%;CKmLZ&y$bQ=gF%)ef>h4+1dOg`el;e#nueO&W<kTuDNn$)hfB1&p?8Q>Y|hC#wJO_8jR=OjfV1!"
    "gk@CIgG)2n1kRP3N7J{!A<IZ%QE&sxE+irdPWxr``|m{-JIT<Z@jss~O_DLkPg0CMus(vZAoS64B54^00piJVLZU2AIT)f1T1t~Q"
    "m^701T2aKCw?AOY0kv+IgYrozJVO-n<9BayR+57;k=z{W$sqX24$s+$CjUK)H}`~|oXOY8+aG_@X%i)j&0}078wYvIWWMa!`%RR~"
    "BI~?~>kJfC;N3J!C+}b%6HVu+1&~E(>CSYK7;_!o5dKVKiT7;|<16_KbRp0tNt3>0_a<Yu*WG?oCV3H~34HtJ2iEECP*h1ej<bMD"
    "vVLaNb8hbSZX7yLRN}V!l9gt22QzK=Tg>GrC0ao=Ong$2$%*-B!qYtBm@t;s9tEI%^%vz7r%JI8{sOF7VqCPP1h-iY@zF%`OYc|^"
    "tx2cBc(*0{0lR|NlTRO_Wu%}d|09erlj;0qVvzD|HCZpAI+CPKK71x5;q9x7npImIso_N(5HYDMEcD$!_&Twx!rIrqc|#qLgXt}#"
    "hF6tx&8FE)O-g8eRA2o?wb@Z6)kl9pQ~0UUMoO^RI?<q(YYFzUL<e%H&hqxhAF)(#if-pmy-u2I?}AXmfU-$47aeZVn7sV%B`Tq;"
    "GnQW7Z0r0$Qjj{&Bz9L9w&?YUX0l%5WZFWry00dJg?5Gu2n7s(eMUPBMnqEG5;W!0u6TAs^ovqGvY!{m%qGe@xy=-39D&lVR+ju9"
    "s8Aa_t&sW}D2fSJD~%HRTTJ;$D^C6&Fhrt_xNd!wQ@uEv<PIUxHNc{N89|c6dz)+zyo3aWkPlGnA!(Z1s!sia1cX@A!S<b0<kRgE"
    "7{{B;nVNL%bsSP{t32I;2yHrmH$?$WYr4&^Fi}<yXd>*6lbxn?O8NEX<&&yze-6WwZ^w)IYQ3C7TZq0xsUc=#mcm|{*cCa&zS2hW"
    "Xrd1t#W`IpquKj8!G5Ls_<FL8PC+A@A4e-eYCcJ$)h&h{Xqp$&Klc>olWmk`UjvND5G1{;;^I?JwSta}dc_!d3u9@=i(+AiIkvfP"
    "+H4P!r@YE{oO4~259E==X<p;qrCb8tNljbTFvjsF*@BQx%|-0j+cs`8pjIHd=$EzR6WE$=3dJV|<DqpLG2gbxxrxfjemvP#W3#Oe"
    "YM+Qv#f(AuB;Q*bQkrL1SKXlr<w(+fUh;hQK+!B|-W-bLKDH#J%u#1B%rO5j1i1$!DT?YARB-5JS7-5c+aC0BY`%{-9ZZHUp?-SJ"
    "Q(%e?RbIBv5r*_S+i9AUvN(MYGIg6Z4b}I>xWZR9(&#Ipgzf-HEs+PXoTbTqqsy+g23a3S=K%hGs0_jGsBE5IsSJ+JsYFK~VHK%B"
    "y3}9dFJ*NLlU`Nn5JN&vc4L_Iu_CM)-@`d!>g%lBDm%mXua)UYsjRno3M#E`x)3(p5V*{=Nt(<}Dk4X6$T3ZbgxflAvthNctXCB|"
    "p>}25CttD}w1Oh#8ltGoOo)fAacl!Yq#^Lnpv=!ly+*=3#=U^$z>xJQ-Ch}hbRk8E@QN-x0o~4&@!uN;MIzOjL$b{R_DS6|Mt5LX"
    "`JzpT=~<v}vuJIW##kn^HrZKA2UiF6RYgkN6n5Q&ENAZlj#>GLDM$UkB}TxwZ1hj#$)#V@!gEzrTXKLcbuB(MM-w>*M-yU#ang`;"
    "+T{ukROF!xo?|$qKAnD8E!S{pMQeUwna80M@w`lLiix$p{153rp7iUCTUyM1pv2rt;?^$NhM#cPk`|=ep>=nRT90^+0m8gUnl>hm"
    "N3}H?e~g?bJ~TsjfBka7Ny)^{m*t1HPd*$?m~F+q)Hr!0Hiw(6Iv#E^vW}=kCzU`2TDB43hFJ1}_sAs=xF$w@zyUex0o4dIfU9yn"
    "WTg9s17#dhfih(Kkd;9j3u#>&j<?rISq?W(TV^NjmE&;KK|(U5ax<Ol*QR0`C@y$IJ1Z0QQ~W$N5~v8WMV?1GNr6n$xKGYdC6{ax"
    "i7_m`g6#k(f^HG|j1*kwq{n=!fQURq!7>a76cg=7<T)J2B(RbaTZx|%je`Q?n8fS6!IOk_Qqwj1eL_NxA4PHxU#_!!cip0dtGot^"
    "@+Rj%??pj;rM5ua>ica1UyG_b;B9>cyXEVQBoRQorLWqy+Iy3m?r^B;R{G%R<_z93vW5_+O=9ubs-|qn;jKG~97OIZiBdHMRVZD1"
    "ZdtE)`k3&fA}e?8wJ}~ijrfqM&yVp&+ia@4_zu-VKi%7(en+2fs-lB#?RD?77Ah)tFk0KHg9&?_z1XFC=tf&k<j7(x4jRK=K7xVd"
    "ff_lAkW3|TExt=?;L~KQ$OP7|-@3fc_F>&U#EorL6|fMc*_|^vIJ4WTs0iQOnKJyj80Uin7L%?^{g96l(FetVJYvG-OoyjTpZ~te"
    "^-_g8H+!&ZfKIM$U%aHY-9N39V#C2HNuffq+<&`=#C<YAjtwFTG$oEwy~jOf0oBWa1=IkHIZ)EtZ5~LYF7=#B>oEAR2B8c(pGHk0"
    "G-tI1KK3<2oNl9nz9Vj2W33#5jcRM<|BD&2O?E>$(XwFZjA$l4)sg9+#468>=uJW+vnHK3*c2LPn9dS{v{(rrP0_5F6<C4Lu_Zgo"
    "fL&6byMb?Q54zEQ6&tPAWO}B^b#<}G^2Q!bq{o^yg)E0>{>ZqUL?qZ_ay(rfPfsGVyz(p50ECx^6T$DpqxVB|7ZEcen%Yxb_t!(^"
    "b+0|cHk;hQ#N6cB?E@!1yxM~%L4qzl`}pUgyJ>^Krhs<Jq9n?rkEJ5F_0Mbox5Vj_qtSpf4&o8<4?F<Z49La&U6nOC0klpivF+G6"
    "p%hs0O+vOyt*KINlKrNOugMP2v=6hShV4=9(`1s!Pm-y>ud1<Yk|Hm^IFC|QD)mjGk%QLOUjn$?t8f<SLS<KP4h2ZxT~)<ocb{~1"
    "?(Vdi$<;$M&psiccuAe2g9YK|ux%?kuWmOFnQFF{NepN2Q&>f|ga>`b5hFz4@6=1<?^ec~EK^q`!m;0jtA;+Z*&3D=1WTh?bwqzz"
    ";_!oONxpKU)ZCYrLygyQR)oGnbBc_d?(=O{zsnl8SK^>`Jp;39W?A{LLl`+A&s)!XB5D6E?1#iu$5Q;NU?&>n#J&c?AtVBgh!CuW"
    "P!!9FU<i!~M+ZE}3N;X4b)n)|SyjcuF8r#v5)Qby{2nZ>j!aZhzt_JkMui$K#RXlkm|r9yC~r1<`LDC9I%}@k7Wsin9CPRih7DwQ"
    "2dKXp!-{ow1Gt{^o$<ne!#(gIceu{xWKTTGx{_v&zpbmAn>hnZ8+-?xN*&NFG2#v!o-_bNn`sA*N_7~GnG*3;b`pRaCeQ=4G{5tD"
    "H9Uae=(-6l9zt?|2638nC5AtiV?}Jea!$lor|no2)d*Xa3~WDTL2Up~S@k_woX;LA%D#CvJn@EHK9fE0C?aUC_^nHrbG<8(Ukww>"
    "yJ5_`Vx&r^-3nvIo3s01^t)kXDSju6<2ao!IrbF`+AT3Lzb3|Yb&RZn-W+3y*5A6U`Bv3CZ8N|95+mw}#azO`G&v1aIsOnGf1JFM"
    "ll;%gn~|q4V+}!Oetoo`p%N;`PCGF&SWY`23PT{mvT@Kz^nxKau)*}?elXouW&ruEWyG0?;FoDku2kZ($G0L8PsutqmOOf#oHsda"
    "Af1X)&?k{ywc}gjnAYmCwH=as67h?DW9p1XNHocnyD>BX!+jXW)Y4&E;Zu7R{xPEFDlc;yC-?X@9Z|v<hBOGy@J+MGG3=CPf?@h}"
    ";o)`<fW*O?7G5}N)9S27ea_yh^dWGZ>*$6r6P;%ba>DccM-y7$Xo3bqkKG(nxp4TiYVkSvnE;IST?jN~2!zh<E+8#^2%IGWT$pC2"
    "A;3E0xiGc#A#e^KS_^W3^Q?!QLZo9aAoN1FnBM3Xll$lV+N(<?vs75~5<GjYa9nqX;&;od3Og{N<lCe!iL&d@u?6il-zG&M%b~7z"
    "F!>F0opJ<LwebP=CAP~?Qf{+CsSxg%FJjhY_HiqY4iti6w#^Ze{wo|-4^Ri4YlBHk(gbD`na>DjL74iQ4b`WzS&(?ivH`<umIWu}"
    "pp7I2p>f1{ozz)O&SV~X4b)dPj}6hCXb@@&3lLG^-0^)`!474(YHm0|%X1_@A7_7zYsT2kQt!?uWD(J(-Y~Nvq6pjAbP&ASA&~GI"
    "r-NWq2?7aQOLP!@|AHX=yBEeQMHO-xO18x6jJGSvwl!R&4Latr3Z~ztJC|{%OMZ35NR=1F%hSQ<gZG3KDY`sH_Nt*Y<eNUmVacXJ"
    "7y&psMo-X<3q;(#<b?5E!lVK5={6A9*Z!ha?dXLZ;($jfaed$CO%rx54H=M~_Y@h%J5bn+YtNsfLyCJ;*xo~>*&ZtRUYl{2WlQiP"
    "tdPrTkAElX0v&SX;E3m)q>q5pHm^R!!0y!pYF!)TgSM^_ayiur7tOGSk?ed)h+5QTd91{vmsbv>=VlES^NPD9?)-o7#btCKj9v~H"
    "bko?TN|kSlb=#)!p0R^+-4|s3<u4O^IIn*z?i7+xrR7B<U7yWG6*4^1wX!|_=n4Xx!Zd`i;Ya~kVo1aAU0~*b5q`#6Y2SrOst|U`"
    "(29nfh(*vG32O-m2o<U6j}kI2bv2G<&5LY}YhC=odHiJ#<Dm|DFvcHuFs=#!Lc1Ad8~A2U%aqd)o3mI19cSudF*RF96PC>5mdK44"
    "R)~tsjCfV^2>P@At9VB>8r1HT=7g*D!b{`IA&|Vx@?c^or5{7=E@n~2Kl}9<N$TRI7~Goi?D4qA0X#8%^s*xJ8ff~>SoZbop0@-3"
    "BK(W!aVQ@P>iZV?7&D+U3;Gz;drIO16f_vZ<atKGx&@C3j3v;h78@^1gq!YuTcWQbouvX*XxRdfH#~OJ-y>k@ep7$`>N#%r9fQGk"
    "wmiY!=xYS>xLy7bP7m^-XE*Mg-@sPf1?T1!ggA^*Tm}dyoUlG%TD9*Ld}ydmU4g*uc~l~Ji{CaW=~XWE4K!z;m!0=j2$HkeUT0}n"
    "1R6wNaAOBY-nZS5Peg++n-W3zYCGb>GM;yHxVCvW{w&U6OYR9thlV<^zB9LO@B45@?ry(_WOcJ5`55qi(b9*zz|ca!RvX8aT+hP{"
    ";F^r_mOsZeoO;iJ_goC6Wf=&n-74b()UK2n+CpnWLv1xPkFQ9Y7kcWD#s-%gtmcO{X#1gmb%u96*lm{j&lxpstHV9M@`IfP?JIH#"
    "Y458!Osm6B7!GtT@LcOO+<n)z+xe}_x{SM$x*kq=^Xxr+rIJ?NzzF4OX6$S1^nSS}gH^~Ak6aVCUwzd2tv&kaj+aZbZJt_tE-QIN"
    "0G}atWsqr6ZOM775icl`$raL+swpE5vlfdYmcyfuO6shw?-Si0)SuJsrp-6%KEF^YUYs{#i%>wBt4*3OuCbCCnr_pZ2L_&A=+~1+"
    "2pex6`j`)u6gDp_pdz7H+!<m<rVBSCdKs28M_aXt>#{7F!LTYSlcKy!CnJPbZZq#Wxy-pu4)p#kkBE|+G8%f~yb_D1X!mFVYDea`"
    "tJ%eB4VJ<B{aF%Y7iU*j8GF1z8AzYd^72Q<mOd9>udlZ~WKM@{jU}t9cH1ahcFF0lSZtrfp!0QuAVjn6_9#buS2d3HYv5MbvAPT_"
    "`GA0pXB}qDXvq<F%eBb?fwv689g)F6hJ|Br$7ENOh;T|C5+b58haS?wAS9`Ui(3{y;a}Zn<zvMS-OvERsXb6u;EnKm?uojgh3y5&"
    "fUz?406Nu91JjkK2iB!E8mNveJ)ow(X8;<>y<l8rw@}#kKB_ji8YX2M>3!_#&_zwYwX^hqT1uaRXr}kEkZTr)%$psJ#ma`B8^kkZ"
    "S_;)%^%)3t?sr+wkr)_aF&9EjsP&da;|ZbDMS5Q|ae$9+VMVWQA92J;RHIRj$yu3>bYym4w(^&|TjU{H)-%74#6^m+AP{u;<Dgic"
    "Z!El`*uDy6&aqWEuN-mLG3XW&XC=O~K1U_PTn<#huoHKWM1WQGs;c)RA5tJ|R8{*u70A6$GVzBu|83sCKtd1bMPNmzZ8*i}uFPBW"
    "Ca(VokpFK2ggS}$vjwB>p@-lGd_QY5`0;$rTCX!txcyqrQB_D*3&M+BuJ?A59Z@W4>}6k&y8S}>uo!k6^QhVIp78DZhdhdtlB?*;"
    "#CjTDzCg@hYr*kR4+!6SqDU0!pF{2zen{N7ZCEiT29>iyRlRSYAtKV=d^f4f|M<HBVZ=@uFg@W@7|ML21w%RGZmiHSjA)ay1K1wg"
    "{-#<%_9mzmWABd`z5;cDoK*#)hP%=q9g_wwgU)U;y46+j!t&FjV!g<-lUI3G`y&GC?2zPjYy_DhKStFkF#^^}V4Af_UOeGNv!oBj"
    "&A_{#cvh@Z^v3;m*7>>})9SQF%IY2(E-Z%89Zjpq%)51Wg9(c9<MAAWA^pL_3Uqh?v4V9(N^5`vqNOF26?W&w><z$kgLlIB--Ft@"
    "Bag_1uy=9ZX8VAwa4zi*e_-bELm}A*D{P17({GrS%dI$bZ1c8oMC`DV<StxyL=UA;S~u;D#rON9%&%a{_b|Q?wz?*uURv()JyND;"
    "dZ)U(2Cp8(B?C+wX@KcD(Q|i!eJ1R9p>IGq_w#;S@3E@RXn&>0>q-t`Y|OCl0L&?rHAb~H1;oX@SMlgjSZpGku(Z@ckO326APZ&)"
    "0>?lhjBpf`4&qeJ5Y`~QOow!GzKQCjuHG@T7~UQ{i(+~}<syx6ylXQmik}<A0QiMLx6z3o_&(6mWcZSe^ZgnT%(<s0##90Gd0)U_"
    "6Ge2m6KVh5+8CcW5tGxLoE*75ThD^8>l6?)1kr3U9w*Jh!BJ5905~=C4lJ4252QA@28e%uivi3-TZPbfZYv?&mNAYp%pAsm5Cz*%"
    "dUDT8p!@~QpwByh#?dDP=xd!!J=US=XZm%Z!>W+}$h8SF-x;<X?)k0P#d~q@xJW;EFJb6`P<mw;T?xnV<fs=#pGR!CoL$89o`*c2"
    "NX+OH?(h%_o31<rY_OM?%)^Z41J|fL1g^4;0oOPz1g=fh2yk@)z8BbkAHKK6NCWk^IH-;Kz%lEE@KMguw4&*oVQSIT&gt12gb0(Q"
    "Ox#XSP9|eK<qQy>VxsgO`wVJ}5qWTLHp>;6Ft~~Bb+_M?NnXTZtzj5i?<v$UnBkknK?68;8howlJCpmzi~j=wyKEH"
)


def seed_schema_row(connection, table: str, **values: object) -> None:
    table_obj = Table(table, MetaData(), autoload_with=connection)
    row: dict[str, object] = {}
    for column in table_obj.columns:
        if column.name in values:
            row[column.name] = values[column.name]
        elif column.nullable or column.default is not None or column.server_default:
            continue
        elif column.primary_key and isinstance(column.type, Integer):
            continue
        elif isinstance(column.type, Integer):
            row[column.name] = 0
        elif isinstance(column.type, Boolean):
            row[column.name] = False
        elif isinstance(column.type, (DateTime, Date)):
            row[column.name] = datetime(2026, 1, 1)
        elif isinstance(column.type, (Float, Numeric)):
            row[column.name] = 0.0
        else:
            row[column.name] = column.name
    connection.execute(table_obj.insert().values(**row))


def create_released_v0121_postgres_schema(connection) -> None:
    """Create the exact PostgreSQL schema emitted by the v0.12.1 models."""
    if connection.dialect.name != "postgresql":
        raise ValueError("the v0.12.1 PostgreSQL schema requires PostgreSQL")
    ddl = zlib.decompress(base64.b85decode("".join(_V0121_POSTGRES_DDL_B85))).decode(
        "utf-8"
    )
    for statement in ddl.split(";\n\n"):
        if statement.strip():
            connection.exec_driver_sql(statement)


def seed_released_v0121_rows(connection) -> None:
    """Seed representative user data that existed in the released schema."""
    seed_schema_row(connection, "users", id=1, username="released-owner")
    seed_schema_row(
        connection,
        "collections",
        id=1,
        name="Released collection",
        slug="released-collection",
        path="released-collection",
    )
    seed_schema_row(connection, "tags", id=1, name="legacy", slug="legacy")
    seed_schema_row(
        connection,
        "models",
        id=1,
        name="Released model",
        slug="released-model",
        hash="a" * 64,
        collection_id=1,
        thumbnail_path="thumbnails/released-model.webp",
    )
    seed_schema_row(
        connection,
        "files",
        id=1,
        model_id=1,
        path="models/released-model.stl",
        original_filename="released-model.stl",
        file_type="STL",
        version=1,
        size_bytes=100,
        sha256="b" * 64,
        thumbnail_path="thumbnails/released-file.webp",
    )
    seed_schema_row(
        connection,
        "files",
        id=2,
        model_id=1,
        path="models/released-model.gcode",
        original_filename="released-model.gcode",
        file_type="GCODE",
        version=2,
        size_bytes=200,
        sha256="c" * 64,
        revision_status="KNOWN_GOOD",
        revision_label="Release slice",
        is_recommended=True,
    )
    seed_schema_row(
        connection,
        "metadata",
        id=1,
        file_id=2,
        slicer_name="PrusaSlicer",
        slicer_version="2.8.1",
        layer_height_mm=0.2,
        estimated_time_s=3600,
        filament_weight_g=12.5,
    )
    seed_schema_row(
        connection,
        "owned_storage_objects",
        id=1,
        backend="local",
        namespace="released-vault",
        key="models/released-model.gcode",
        object_kind="artifact",
        token="released-token",
        size_bytes=200,
        etag="released-etag",
    )
    seed_schema_row(
        connection,
        "storage_delete_intents",
        id=1,
        backend="local",
        namespace="released-vault",
        key="trash/old-model.stl",
        object_kind="artifact",
        token="released-delete-token",
        size_bytes=50,
        status="pending",
        attempts=0,
    )
    models = Table("models", MetaData(), autoload_with=connection)
    connection.execute(
        models.update().where(models.c.id == 1).values(thumbnail_file_id=1)
    )
