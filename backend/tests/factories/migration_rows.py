"""Builders for rows used while exercising historical schema migrations."""

from __future__ import annotations

import base64
import zlib
from datetime import datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, MetaData, Numeric, Table

RELEASED_V0121_REVISION = "e7b4c1d9a6f2"

# This is the exact SQLite DDL emitted by SQLModel.metadata.create_all() from
# tag v0.12.1. It is deliberately kept as a fixture rather than rebuilt from
# current models: an upgrade test must exercise the schema a released install
# actually had, including indexes that later migrations remove.
_V0121_SQLITE_DDL_B85 = (
    "c-rk<TXWntvVLCw3RYfhSLwl*T)cH^Pn9B%6IUGRtSq11Jh3=K%m@*OobV!9`SsfX2m%c>0M3l;wX5<Z8x4FU8;wSHqtSDAxj4OA"
    "WLKv@zFTC)j^{V*zRR9G`Wr8^x93-jpB9(d`NdUs{^8xblkCyohK{v)fL0e<mi_<f<=M}tmrtI*c=Z%f0XV-dy6dUO_ut|UyOyo_"
    "M+5LZCa+pn^sLN_K6?$XeD(I%1)^6)*XQthsD~i5?6$e_oB!|f?XRbozh%EHe#@Tl@~LQhb8)$N`_s8-doq6M)9iBbW^uVVKU=)d"
    "L|Z3&KYIE<j~<;N=CwUv7pp$s!fUp?sPdNmG4PgcS>4B2y5<!Nv9xcRD(7{{K9c0=QT_dn*^HG8YpSNruNh+L98M0qGD@d7cXo09"
    "{_64+UNRg0$fLZ-#Ybfy&fos?!y<b!e*H;iKGmCwu@9OG(H5MFq2zsDHJcdQ;q@9@l*cyS)Dyp6zQ7{bvaT8074z6l_rlmEU$67u"
    "I|Gjf@trq)iCId?ul~N-0=z6%H=DK@>N5YmS!&jWzOuQiVSPC2KoD+u$Cnk$e!RGNw>Uk=IsoQ3h}iY;y}{)#s6(e1c(wTV6;>U^"
    "ebp4Dp{jY<RmFYY6>IusR#m~bAdEMxc6nd1qGLJxxC4>xT*XoJJ=^YJNs~H|eeCnGhdc1tb+_g<@2>aPWN4~pivy^Siplkm?^wI#"
    "T?hX>bZfA{0zKNMGL!$^eyaa?g++g?;+dgMN^AVolMH>=o?D398)MmH3?)5Tu^EbQ&|Nib!qi>S3f+y@`mX5Be*WEaPhA!zJW#L4"
    "l2xqtrB^pE?-BCQZWz5dwKU)XS_W|Iu}XCM>gas9qs%AGo%-pMW)3i4J9|Bs&1y)QB1k}9eCI4fq`e*SAQ!kGESvIv5&^Mkd(V)v"
    "$NyyTF+J<-vE6m?;=AwN`Tr8-=nxHb*xLUpWDPb!*4AM5ReaeNEoX;orM7AM9O;?o&t5o|1IQFu93{EScdG(;TEqL?!uxD_Jy=5K"
    "_v}`%!46gAh3<-eb)9dU(y{<?k9C)$rJ~JlGsm&feE|~N=fMRf{T%VGwZ8@?TCU8c=@~yfb;!eZTeN0j%EpL6WYGgwk?a9>V~%ac"
    "&#`zYj_tXBf?_J9Z?bEf(X%*EUZXDAM1)mg3kZNH34!?m`y;>EWN+SGoXV-G8zHb`SIOxecuHH~_Vm(@qrvKq_xJfGUu|C+oRULl"
    "cznLyn#PBo8j`b7Zi=N)g^a?)x^G&(;X=)L`-*3aNEZ@Ki8F7vpv{DdM`{Ldcfw?En{vz`;SzyVR>hK4-lR`tXURT)hRL2B@D9A{"
    "RkH;VC<Dq5x@c8Q(j7g0??}?<$8LKLtes?r*BjQ!Wp=~vT?y~6hwZX1c!hQLi)Y`euN`*6QHw~_L+$P<rJ>~xDGJ$0?}7xY&`@g2"
    "J7l}|R}in4&BvUBOBq{7q(6?uRLy40iW|EOZRO8g$vf#!_q?wN15S;+1`S3Vc*wc`yQ?#6z6O~Jc=s?CmiY#Wn^XpKwo4Is0%%}4"
    "poHQFIP_s{VPiaJsCw}NuC9_xsBIhJBbJO<*?oIogkm8&NU(y(_O#P{q;dw`up<}KLj(+t-h^EMZw}CCFE~XY_!AXc-2Nhit+4=Z"
    "76-;@yNa)XTS)Y^>7+<{f3aeVD(Ee3Q}xE41l7%-f5Jn_tsB-N6X9LHx$Y5R&0APXK{Bu5Ln>irtiX0K?RN#VR!y@*E?D2}^6r`m"
    "k^t<mYuES9Hc0M<-L7d(3|OxM>%+tfU@dHWEz4KtRhDA4(j>9-Y^BtTO({nYoa~`H_0=1jCnE{$giU|#@N1+A^UE|JW5_Lq#{P(&"
    "BAf30raw{BZBv1h4o#Nf*29D94R8x-iie19>>F2C%L>$SduSXmYV95JIXH}Gy`WNGYFT+<yGcLn7qQDO&a>BxcMD+1+3EYU)7OiA"
    "tqLC@#AUHt2_{E52Ax7A#4HVRlBp3#TSFs^-CMnISZ)rFJE6d&M~$8-%5c0CbJxCqy?Ar_;oVjC`1#{`fvl2stCsKV1EQ446flN`"
    "bArjv*78@B%ab>kRRNv#RYb?YljCc?DkLLT*CLWr9UgE{hF8d<kA=oo4VVI(fJw}5MXVT~sA*$`mkvP%mK^`}dlPe#MzU|aOBzfO"
    "hsEiOI=o)TRznZed<|0%mO%GO?BsM11I^g@!ttQyIISV=EV+v<i|60D=@6RPXr+r*?WEXCw}H+ddps>(5Rh^}Xr9KVO)6FC+{C>2"
    "z@&a^6*KW2Eyuu;qfUG#wj+SCIWmUwS|&nB6$nqOgq72@1t{=l`8z1Vm{<unh*<`wo1q|7MYk(fED|;Q2K?x6@MeW03GyxS=vlqD"
    ")$9{516OO~1qjCd*$y910#~wI@S(CF-~I$}%7+pDw)y``Is>qGSWQP8lXz~b2BZg8gSI8dnK;QNmY-yjv&1YbF<6R&19oU8P}C0m"
    "V<_sL_xFL5^+7ttT@UUQ+t@Lbr^XC|-QoRJ{^N)Dzdd%<P}2%%>$qYXbbfL9>*>44)cJ*Q)^|jNdkdxzU-C*ssCNzc#@73MYeBz1"
    "eY1G%#8Gu5hqhY&TNaKbHLIH(&2_FP20pdpVkgVaeqNmYl0BJb?0>Uod-`R^+U_<VzaXo~)|J5(64?~p@QW6?gBO*YLvNei-Q#>b"
    "C7Ii%XERPAqEXOY+ibwZ>6GV>H730g&s*8Qo}Pbj7b!@<z(%2@oT6u6suGw2FAZ!MEiaj~z<s)7fHX3y)U^2qdge~f*$ebpSNY}Q"
    "pC8^{E?(nzm>^2F;-#l!JhJl!FweKp-nyrydM8}MRwMFNVa-|Df)iQ9;iY8C<5<E&)O63j3(c+{rw;d|Rj_h`gSmUt*@!+v@Ky=x"
    ")fe3j#h9r>H!PEVfXmX1N%Ya$A=qalLi=)Q`|HmDRYI6Ny)e;NLo4%$WRR5X(KmfT1yQFCPAJ%f2d7+HCZwvirolB1r=F`~C*!IC"
    "wK4@nWi~Y#DwPx~z6z*=nBSpQCU%FFrX5$EFg7_`uZ7cpYaK4i!)^B%QnooUBIR8wQt%*rj}Xc}R)h#J62PJS%uk=bX!cHJdE{cB"
    "vP@OAk%@Fgy?s51-}7nh6#d$SM>Uz!)MJPpVy4wXDE65n53s4e$k3FQf>hRqZ->Pknf+{}JyHR;QV!(u3@JSjF!20CsZxy9!DT12"
    "fQ5C%g;M>TL5zoAi{Y8x7UFMRz&ECDOe7{97SFnAx3L3hVWl?heK=g-Sk~CL&;IUS0TKsb;w-hO25ZmI%|p$5_k8t}?Ezn+41Ccl"
    "#TK+X*n^t8BV<kPz+I5JtS-Yu$FA1q=U<ZY<w(l7gLhTaA0s3%+|5yQg4}LGo`3m{$Z^9?;}4e4_~HDQ^NW9-KPHl@%wt|9eDmWn"
    "NCS8N9@4G`Gn2KqVLSH=2*hcd*p;SSC&4je9vdk-$rKs>j-vA*i|uo+o#U@+B^DLBr}e|<zNbZerb_e}34W@FHDSkH&zdvyk$E{b"
    "uRNY87Rf)0<&b$f#h?BtRey(2u1Dy0N;mJL+hbhWS7Co$`cN`{t{&8E{@V25G3!i4XM^YR+oq}8;zsxzR?d&-#*VcVtlWt)>U&#="
    "e7@dYrpscx9Ay5q9}t$LP9;%#sfLnRtX8b+R2s5*;?NZpuW!5;#nfWxo%>8L-7bRT<l&0xvMajou4zl+@h&UC_S`g0F49knq2=^P"
    "yPc>nbo&SwW7!mKiB6HnS8Y~(qoPL?I}Fu*TNSn<zrF<3u@V9r%HDOf>o@W|<QuOe@A8tZi=pb15%{u&Z&b52uWQKLkwikPAXzoG"
    "(|x_l-CUwj3f&Y&Yni%8KNf!|ldtR~th{>a=cKqHn?SP4SMSw*kseyh)-CI<Ri@#g2BsVwF9k!~F?6p~iIOgKR$F3XK2tfEt`HW%"
    "EoXP1rkoB~lVYC>Wbb`?bHe&QWr54r^~`V4dUTdG!QJbkWx3d04!&FXaAN2c2&t;(E-#zATHJM`ZzZ!z?Hps+*2$-wHoMf-pogt?"
    "u<r>?Qsod6H{Ruv8$+tbjDLl2UJ07ub*=}z)p<xxS}AU?)P;9<a!MF)*^2R<lf{{m!b4_#viX6-4|4r$|Jz%XY^YQ_we`tM`}JSh"
    "kPng#In|_czr!t9MT9zWPWSv2;#esp!eY~({#kE5I(r%=fcdy6)0v;bT?Psz+4mi=Ay*0a5rGyr5c84~IT`W-1WnR!?XHG#3V**7"
    "Ml*1=pEOFtOV660+IiOvgmXL5R}~*a!tTu3Wa^3C3Os|3M%5ms%4+hRH_=CWPhG4>QEtJjQT=>I$u49H5&{871@!Go_=!9KqIP&L"
    "?<asAa^3ekn~&Ygd^x~m^nP6ZCGfr6H6b??=q=mW_e7@%-%ITR+s-MpDo&$TAqm4yr0By`$SVuQJ-*f1g4q+Hj{xECFdd?wW4Z;t"
    "%5)$;&Jc-jEQY=T|FpfyZ)(`E@=eo}b7~xL*hyoTV(gm&@t!KF%Sv5*`P3Ml=EBk)FTsj&k6lFlj%e8$-DxQtXi`*k4>w>j5$;+J"
    "T5Q%+RM&MQ;+|B;tq6GCHEmUnx6G#Kh(id46qM>4KEsU=U;FB-lym$nZ}YbA9{M)me<`@|RR#}6TGSVg`@C!m3(DJegbN36G6f)m"
    "H${1ucf*dg;z6NuDTT^MBnvi(IBBd*jJ18zn_M?YW0KxDVHv_Mo{o_nBy^uH4j^YWSD#)ob*DjFN)|&-GMfVrQ5NM^#7lEk>U9lP"
    "EqIkN>1u~MT%59uC;%qz`dO*(ua2w<D|oc#tPRU)%XS5CbEg<BnSAidRY*OokVAt);w7tFs(|v?j%6#QgcQfoeA;sc<X{;O{kVgK"
    "K>>#afyiO^qDG-gq|pl;yk0kvn9W_$)*|!~i|X(mOphwkC2~nbCz<a+A^OFjMc$Pv!z@3ZHkHD)Y#un}IVn5od|F!wMuqeC{Pp7B"
    "8ULtFB56*l5Z}eQRoz^-S_uBou^c%XsM?~#te7AgYTBd2bu<f&)u9+_V6^J^R9Ha3CF+yw1XAiDQ$201lmwntWLveShc?r<;lWLt"
    "ezGM}4uGK~AnZVggJF^8f=GoQf)PXt$ccoZnu~zt1<EY?EI7m@A1%r$7tCJV6q-e!11CdI6pkRw0r0AFQ($hVIlykDio$jJ%z?%6"
    "X$llaT4zAoHO(nNi#`WVZD$maq|AY^EJzB+kmrEu+VK?9NZSKOeV(2M^&&ColS~dc$<VZ%WbtnZln%#rWZ<cxdg{0+%KQh&M7#*D"
    "pzi@^iD?KiBBxgUmfUhs5EvjBQVPVB8+S2GN$&9ekCFOo0;IoAzJgU$#~W{GDHs_+aL0>AYT#6)+m|1!es@x23=EzO3nM-i20&|V"
    "YiLMcn?cfz$AjcSaCDHvZ)<FV+XezFBMTwEgd-tBfT1dTj^vqOboJx_G}(Fo2TgoTJR``}5d3(e8bcmW00~g9&T{S~v;!<riy3#?"
    "6Ik16F<i%7bM=Xjt|goR4N1BWLRNC7L8-2RhcI{5j}Y+{uLe>Dfeivx12+g|DlQv<sV&ZH7>$bz8wAuIez;xu*cxnFqeJbo%4oDj"
    "2m`1C0L^WO1Evz6nU0f;;tPq2rejLi2o7>o>NqZ;PSsGiEVUW#P{I&n289`^8o~qY;?QQ4)k+Pu$|L7~;s?N>`pJHtLIM(dR0u2X"
    "P1zuxCxCF0UVsfnR3morh&s4QP+bAS#~LXq2s^*P1+i#J810c@fRHz04<#!>yGUf2;sh|ZDi9oLSGXEY*A}*;F{a*dB<^CDGqq<M"
    ")nSdzu~!e37$1>4mHOp#RHuX`xi|vg?MRP~??=v1&1n1rwhlE@ags%ygArMW1vOSEAUJl{8@}!y+U3XYj^@>)Xw@5dL+v@}!6Bz%"
    "?2g4$4~kp9WL-Sl_ze`!i`c(3<XELLjHo&!0ZsA6+$yo?!rCPot%`vAfFrdZ29It5A#khuV+`DM+Xoz1XN=*ap8Grk6`_nh0To8v"
    "_W_f-io*xBR|wn~G(q67WkVKhoKmj<Gf>fS>@gqG<SvFmr#|yx?R3U4u+@u##x?G&UCpV^64}}?guS^r7_sdQgP?;w9|#>OCBaYu"
    "Gat+*hrwhp(yz}n!Kvt@*C7Eo)unTArg}N?;biqS2a4*GfW>>f4Hn<$Z3v>WnTzFjNq~A)#T-PJo`Ca&T_1%cBwY^n#axX}%eXpE"
    "AWGLcNJN(eC|0#LR$RMc2vr@>2axT_;kX{2hl0onFsHDggN3OHC|aNBbCx;EWQ4JND~u6wt!x0d%A<?p&=YWeX$J?->o5y2gp(dl"
    "$?+V?rB$>VKTALBI#=GJHU+R!>w1DfcN2Xao16x-%3AqQhByNVm9QePY<e1w&=m%2hPs%5Ok~Ib2}g)R^{VozsFa1~7q!y8_JSt%"
    "G!lHq4oAVGf?3fA*gf}q1UX~G17=MgQNn2kkEUWz`?E<uMAvglJz?y(FMOIy(-RJ7lqp%2ddAc<{s_BBQf}9vK=`<nlGSYw@DP76"
    "_FUE==<setJYoLM6nfAc{QY3j?nPlncQU7x4b5nD2tSX@RH6V07s?U{7TSrtnjcAY16yu2oGGg2m&pmm*E|SZ7>C3{U1ovW^>j!`"
    "t3wP>6<eXFb*z9hYeEBhcCJ!|;ihf`9zL~4hhrm4jvR|wM2A_0Eznrg9vvPoB!SR^-J;-Ap%;XuTcblw1j!5xhm3I11SbQ)t*8(`"
    "@^_$g83Y1nBt_PugJoTZC*TZq#%o!6!o`UkHh2XE6auQF5F2hP0yr#2wF2V&wLxrW9YMsrbsGR@0wV}A8Q7g58w<}4!Xl@}!$Q~Y"
    ">2p%|YW}RFQU@4^&0WNJwA_JmCeeXP%p->Kj$d80<O!?;X#5u!DeS<|x7+Q|ap?YgBz7DCb*F*GO-%<0P2|dk@MomV1|H%>sY4G)"
    "gsB7IP8N6wJdqKe0#Cg=>bS=P%-$114(~+h8hVbeWC}ohOC8ZEIQfIh*e+35ElHciRY`KRHn7w<$C@HX=tEI8B6K9GZl&RO8?TPU"
    "4JP|Ko4UzoMp~_jq53YLGj?oF4A!><ox%F*T$n!!W4cCZ#_J~P!PGT6bp;5<(|7Dv0c(+lLaHbL7*#|htal58Mo!+xpuq>7=@392"
    "LJ1Ef+;oL72&j9^htzIo059Zpy7%NF)1$7@&7T?xw<QEe0c~dpjf8N+?mkp1`0hcH+)`LBr_h=j{?O4~@f(y@@}K%u7%CMn``6El"
    "%Y{kk%l?r){^{)E_2O}Mdj2|dk_-QlJ(tavqI&yYUXn|_k$RxMjZEbij<m?PP5zAhyp*FhewpRc#oqou1$qwC"
)

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


def seed_legacy_backup_s3_receipt(
    connection,
    *,
    key: str,
    token: str,
    size_bytes: int,
    sha256: str | None = None,
    object_kind: str = "backup",
) -> None:
    """Seed one v0.12.1 backup-S3 ownership row before provider identity existed."""
    seed_schema_row(
        connection,
        "owned_storage_objects",
        backend="backup-s3",
        namespace="bucket/nexus3d-backups/",
        key=key,
        object_kind=object_kind,
        state="committed",
        token=token,
        size_bytes=size_bytes,
        sha256=sha256,
        created_at=datetime(2026, 1, 1),
        committed_at=datetime(2026, 1, 1),
    )


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


def create_released_v0121_sqlite_schema(connection) -> None:
    """Create the exact SQLite schema emitted by the v0.12.1 models."""
    if connection.dialect.name != "sqlite":
        raise ValueError("the v0.12.1 SQLite schema requires SQLite")
    ddl = zlib.decompress(base64.b85decode("".join(_V0121_SQLITE_DDL_B85))).decode(
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
