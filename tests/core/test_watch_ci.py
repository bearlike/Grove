"""CI watch predicates over captured forge payloads."""

from __future__ import annotations

import base64
import gzip
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from grove.core.config import GiteaTicketConfig, GitHubTicketConfig, TicketsConfig
from grove.core.contracts.watches import CiPredicate
from grove.core.tickets.registry import TicketProviderRegistry
from grove.core.watches.ci import CiWatcher

NOW = datetime(2026, 9, 22, 16, 0, tzinfo=UTC)
SHA = "1e62a1d065cb8a332e5a20e58b2f83ecef4fd78c"


def _github_check_runs_failed() -> dict[str, Any]:
    """Captured 2026-09-22 from the GitHub check-runs endpoint."""
    return json.loads(
        gzip.decompress(
            base64.b85decode(
                "ABzY8>9(?J0{`vZ{cjt`nFsLS^H&fC4rp;6Yu;Z76ouPdlU|zm()jLrEec`Y)?!SF3}39sf&cfLrDU6=D0(UFxGMV%;8^1BK0^-oW#;2&W`6l%FnF?<Ed2O1PL>yoC&PiJPumycS&pwyXUmIudF^lImBHYb)&I-3Q7W%7$}H1@31wGDthQYE^IWz)`TZp2@Bj4V<Ll-3)74>5e*fpw*Oy<uc>cSW-@KUH=jFe@EdF$UZk~VK`z-r(IzRL6?ksEWS)y9SV7(ALE6-_GHt@!Z9CIU^WNiF+bpCmfXBXuYU6=Q9!*VumUpQMVrt{&+iJy+1y&5gfmf=~PoS)3{bTU7g&YSYnPs==C%uiNzK-xMuS^u<aZ**}!KHUnt7IGulo-ipd{AfJyA<}%|7t8h=ds2>?j`Jd?j~nCUB952y(d44tVZMwp&*vM*&dUC$zC8<`aLP5Ij!^TG>7i0XZvVO->L&VKdkeO<_hh-4F5CUypF!<OH(HEWr^<Je!9~6t_;H>s=7Z72AeM7w5YPO@tDFYmYOpvP%?H!vcs#hNfe(S(H@!SR_p_@u(BJ-Nw2CtxgqgpHXE!1L{;SQ}o9DQkjTToYZ8>Bf{l&%T;?-}<*4fo>rn7$&&y8m8-xrg~xcq#5BLDknUVffT^Tn^9t(U$i=UOTHqP)4aS4+flMJ-A_&p%vM^p5R)os%bPL7&_d_3q=>wYr$+%h4ilehaHh#Bu2y<Bl?Q>qtMH?l?jjW8K;(isMnfSlnE2e|*E9NiBu2A|<OEN>kqW%ErrA?S9vvllb(ed|Nq@_g~EVzH9f5Z@>IjfB%=?U4Hj6iWgtL{>}Pslgo=d+jyVl#qs3T=;C8RYx{rr;EuVCS2v3LkE<82-PK?DAC~?v)3fx|_)YlnwfgquM$v5*z47JBx%Ex1UY*~3o6C8gmCJ8YYH9VwE>GIBfBWV)ax>Pe*-f;qdsm11S6jsVWY=f?#9eCFA<bkwo?MoIr`-!Z`Eby#Smhc!JXSgAmy^ljESKsnTi<O})~H+%hXh?6@XPA2Qo&o%+S}i(+|Uk)cXLR2i|zUM?w8f|x*c{I=5aQfcDdX)gRdXB8FMmw<u68W{lPKIfm@+gcbG#$tPa?Wk>8Zl`k)}!NBnX!osHi3c=c}kJ;gkFQ@)DB18*I=8Fz6tT|IGLtX1FE6;@)MzA2B9)A##Mmtj1LTVH9pjh^~+zPh_Kemu_~w!T}xcipswz8WpQUWUQ*oBQ10haLC1ls|meTi5-^=FHt!aFfeZf0jQ!+t!c&#MNJJ@b7DH|E)OAN-66Pd`hSw%)I1m$kmX#zpn4SoF;#}dBEgLYKKe=rQO(jnrG*u`FwSzZ{5XxT->}|&I_M5zlqgzZME%k);`-dH(uXw?>clc9gQc8)AtY7JA#~-rdjzUes_?zRk9Ibwad=4;pT*F+tMFi{QIrP?RLPwec1Px?oWapt%f%4{G@Hz?05`HskM;OO1}9pT%L#3Q?)&8^MTTS%FXX-b=fxUn@YJfladdUMY+6lX#ky{k5)gD>mNx{lm(-t*nBVTg>}y+|JuFm*4z5fT@ku8bLi9UjfYm{lUotqU*{k8*x2FS+HdbRYBBhEzOxq3{pjM3>PbGE+-MPGYIIJyw6U}>4Ogs*G7C?2EZdCx=c}GCwtu?5KyIpSSND^-(iCO4x}R@HZ_g*Q?%mJEFE9TIx}Vpd_QBoH?J)Q6er`vq*8P-2We(|nQhFTS&+8NUF?T<&PXTm4(fvgC6WvdAKhga}_Y>VubU)Gk{8#9HDr$wg)%|>4?w40@bNBvdoPArX|0#JEjgO3oBq&juB@!BEi8DNFFO70S_1Z^WRmuJOs5dv;>mH_%Oe$fRHT2}iNw_2YfqmC|lu)rxD-Y_^3e&MqyVW23*hk&k(7pS}>UAGiwf*4kBeh05+p+t|l+box_mQe-Ty{ROMu*h+eci{n-9X;eY5ex(f4}|i$5-eyqSJ^@BRY-$Je|hdcekys;`8NVa$dSxgR9AMHn^P3UN_|$7&NoV`Cyc?A1o$=>1>jgaqFGm*|9v!<LP=`<%^RYH`znFmA8-H?o2XU9<xNym(&df9WwLD&ZH*Hw$Ei|?$?>z(brpjJLNK;`S+E3yT@0W6Qk0aTb!<U40pv^Eb{#Q1NDyn>Hf}Zd8quf^y9V{{^|bh&dr@VxTU)`o?M;p36w_jxcpF`JNGD8WWH--`6`zA?(X&MRr@*fzFqe9z|TuPjqm8<PknstU)>pax^LTd$9~5?tDD+rF_~SRo=qmNJM31j!yj|JGvIr5IsIS-JNH^OhwpsnZ<gcoSn*-ho;j!2{{+%mCf0kWtA9gj-|`}_uA0-?a=hydZc|iuy?otV+}yCb>bK9ow#|LlIeFLXr#GE~J5JQQUSDa%t*}kGX>K(VN<(6KzVkf3{o3xJV;}N(*UA2BHd#)$HjKvQJeyqXI6+Utax}iBPPZQ~>nH5pTgu<<gX)tz?EXm8<@{{>+-{pi?U{Xjscml^=hisxxE$`hOv?8)^^176>%8B5?eg|+@1KBoMES6}J!=7RfH**0Ul3>H?L3gx-D~hG@r;S5gVHlglgl|7`?hWL>jQM|C-!|MookOufh8in6+~s9iO*CJ&Rpi&l#eC**11woJ?fleeACW0!uO$b`%9>)bFRD2*&+YO=^U-8bEUTX&^b#fbPhTPorBIn=b&@YIp`d84mt;&gU&(cpmWf<W7j$1_UN2^kj~LsxpRz(l%kJ>ne2)3QIp^@C){T1g49&V*tgEDU2ipXZasV*om(%Vrq0=ebWR?r&It#dgU&(cpmWeU=p1woItQJD&Ozs(bI>{H9CQvk*G1>F(|dG|KS<|(sx`51*~+Xir9r7et8<|t-UUl+^4@r_g*8pjI#=qcrp|4IucC7sB|I9Plk7NkuGBVk4mt;&gU&(cpmWeU=p1woItQJD&Ozs(bI>{HTsNIlcj?^pYI;S)Gsd0{O1Ji6u_T;7qw*l2o7PI7bG}@1f=8mcOGL${DJ@oMNOCSD8g#T)^bT~YF3_p!K&KuZ(CMMn#|m@`KnI`$&;jTGbO1U49e@r%2cQGc0q6j906G92fUXnJnSJSVa`B-2xpA%hIWCpuRCwZq@}wN6h!wFREIMgYYFv!2Z=N%C^XE+U{5kXZ^5@);izDSZgZw%09C!{q2c84Zf#<+;;5qOdcn&-Vo&(Q;=fHCvc~0)(Irbo(d;O}`x~*F2Myo&=b?wR!B9Y*OB(X6*Gs~k6taqK0b#+cw*E#uFbWX8DMz@M%9o=dqbPhTPorBIn=b&@YIp`d84mt;&gU&(cpmWeU=v*hA(`Ns=t>-?z_~AY_H>x$TRZAC}5``${B2iHo61d<*TiqBQgwNUbZFAaGwYl~1b!=|Egqk*|y4#!?(j#59wbV9j4mJmygU!L_U~{lJ*c@yQHV2!7&B5kibFewsTsNEBH?UQal;1qW=W6B7@jzv06em#%L6i%Q1j!nbq~f7m8!9p1yU*1f*{Z9LY}KD<WUCT~_?$dipTo#j_#Au=J_nzJ&%x*5bMQI%9DEKw2cLt_!RO#}-F!~DPfR>_?^PjQ)k>Zdx`}}qN}S}LD4_ze*-K)ySCYo^9s1q}wklT@=hnm5!MXJkYEC<64p|jK@FT@J<$!a*Ip7>{4mby#1I_{GfOEh(;2dxcI0u{q&H?AT;hg*g&fPn6uBnwd$3m7iW=@1Kk|^02;=SjDa~XV4$vdWd$GN(*&Z+9N&MEl_Gw0Z`&N`<s>l|<nI0u{q&H?9ubHF*^9B>Xe2b=@W0q1~oz`2e%NB7~}18gqWvbosM$g*;TilB&ck`k|&A>O9QYzRuNwJG|tIa=4|Xmy*TpU39pL9@=eW3@R7n}f~4=3sNMIoKR*4mJmygU!L_U~{lJ*c@yQHrL7KB>nWPbN3$D8lPA4xg1R}QcA*8<U|D*hzmIp>r_xCE5Wtv+vj9mpOe*nPSVFXuvHwX&q??kd=5SbpM%fA=iqbjIrtoW4n7B;gU`X|;B)Y~Za&BMtqQ>(kURIk)od=bY>^U)O*^jD1WMZD2op4MRX$W>Oy4%g>aGaEs;>ya9^;A-N4sJx!-^2FIoKR*4mJmygU!L_U~{lJ*c@yQHV2!7&B5k6*_?7_kIk_M*xaALuViyHMXEEUL^nAQ6&g>1*OK@gsf@G<PWNqdrIu>i+(!5+Hn&kiO`GEf+1!yX2vKSqHV2!7&B5kibFews9Bd9Y2b+V<!RBCdusPTqY_6Nl$xn}KExofext!le=)SKu^PCYWaxI9|%n+5DKwP6d@j;X;PI}we)Vt8hx<V(b3*F~g6=J<@|0$t!Lr#xX=&&jTgbqRnp@Yyt=pb|uItU$v4nhZ^gU~_fAaoGAZbE0>{_MGX&pP*CwX){~Ps%AFh+<w5C9NPq8$qP7F8L-quT<YQXI)jBTMu8y=GIH7X><CJS?9PtQk%05HV2!7&B5kibFews9Bd9Y2b+V<!RBCdusPTqY_6Nlsr|Fg-P`6~R2$i9q_e4^gz^}OqTUcEtS3Q*q?^baAw%yrr|R0As%~@Y^Vpn&&8=!1HV2!7&B5kibFews9Bd9Y2b+V<!RBCdusPTqY_5~dnSCp^$_LooH?_vJW+Rx+LX)JGAWBk8EcccKM~$QnqtW@kZO&9(t#u=O9h)<cakW;ikJRQ2R%?aL!RBCdusPTqYz{UDn}f~4=3sNMIoKR*4mQ`#=JdW<=fneS?mug-*4kvxS!4|<7l9{=(MTLu<tG`OQq1$%f8Ly~JKvnHKHr>{k1=oVNLOnuwGEqt&B5kibFews9Bd9Y2b+V<!RBCdusPTqYz{Wp$>!9ZtF=zArdQ;FOSXPpYhbH#$u*{-q{-S46{I9iGDS=Z+?HUp5l!Dfr|K@*s;V#9sy@?_t>O@%+t~Y<fKFk_RsbD<4nPN>1JD8J0CWI403Co1KnI`$&;jTGbRB_C?@6Ac53#vg$#ae=)%awHH6am|4JXdZOk9p-OZnZ$-WO|Cx~|RX>Nckzoz0o!9oVWgYz{UDn}f~4=3sNMIoKR*4mJmygU!L_U~{lJ*jy)@`*_;!wz<+XOOwm_eRS^2TGP&P<AljskR(|mDsV%bl)N-W8Rb%$Xc_NY=OnMHbL-*j=-he<k7T)4I~3|jbxv~V9CQvk2c3h?LFb@z&^hQFbPhTPorBIn=b&@YxlTGKZtGmzB3?ZXu9t;4NaTK3ZL+zBGo_Ou+1f~yZ!GbRRKysjVsy^wruVU|qEC^tj%|6F=vn0UmrzsWY<H1ULn@C{<U}tbXC#BjLF6EE5IKk(L=GYck%P!V<REeoIfxuY4kCB#B1gske7XBB1Tj^YwQSCE5xgwT$-o^^fl?AoqJ%LUM4(wFr~0-zDynA7t%t9ZEw^66BgvN2L(Y%X=BPln9Bd9Y2b+V<!RBCdusPTqYz{UDn}f~4=3sNMxh^&*cPs?)-DL3lGe0X0raT*bnQtB?hq&Cb*7^^PXEFGuA-w&cDhClCI44X=BSQ8ny4<tN$-3)5$m;7q$j`L?16=N=w&8McIk+5L4lW0mgUi9?;Bs&|xEx##E(e!`%faQkxEy!4*MBJQdOQkZz@Ghl2*!O?YXB=#jj=RZq9RvBH9?YK93{r+qyl3y=GZgFm1?PpaU0>QVBAItH8IW{gmFhYrCh0PU>q<G7zd04#sTAialklW954<T2aE&80poyiz_?Brw<Aezb4odS$gtIkt(G1a8fThD5fj?<xbgvrHCz%dQV>n!y^FnzT(9YIw<dh!eW%CmFX54hoEp+2O^@UKq{m@OIfxuY4k8DUgUCVTAaW2nh#W)?A_tL!$U)?eU*wpUw?u9{TDysKP`Gieyf`6Q^u`zxwO~Yf!-<PBkU#~c*(jzn>sjGSee|ht#%>zfM)+P7Zf^+(C|pzC%5s)#DV+I-DIBY*aHY0;Q8)^PgTg`Kpm0z)C>#_H3I~OQ!a?Dna8NiX92D-j6;3*5Zs*0l9lbrD%noq43tlT5PV&+kW?2#=QYOl=(lF)L6J?T0<t3q0zmvtSYNS_()9XsM6}}&b+gHNl$%fO%m<_k8?S33iz~SI<a5y*|91ac#hl9hx;oxv^I5->}4h{#0J8p+#Mw>kgtcs_D(lbkw%Q+eQwrzCCSk<q8uC>Ifmx59$6bUqIqOw)QI_-$kMtC1&WM1^Eb8S8KrgPeHCN}JBGyKDK?o%by)H&5%=j@Qnqtv;!wtLXIb`m=19CQvk2c3h?LFb@z&^hQFbPhTPorBIn=b&@Pu5(=M(K-2aaPH%aAMRsw=e2CkF~L~UnzWYzQM56{Td9a`n%pk&prurO+Z-2FZEih$9h+M(p{C8b?lxzJlpd+gaRHly&B5kibFews9Bd9Y2b+V<!RBCdusPTqYz{Wp$>yl*b7hCGf2=l^l}Q<u79<B6iHa-`%OWL>OHvDJy<)m=k)y6pk<)a`%htp9B6536s3~%?yU59*az`q1)b$~9l3`9ch#W)?A_tL!$U)>Fau7L)97GNx2a$uwLFA5I<h0zEEvHDzZ=McHul8cGB%D8^@*to4p;pEmGs26;vT`|;cBRru67PH>Tv$i5c0Ss^Gv-Q7)y$aN2wx>*Zli=p;&Xb)?Q!~CscmG;!RO#}@HzM#d=5SbpM%fA=iqbjIrtoW4n7B;>*RBc?w@P!-ZuBmt4cPPwKg<aLvo~?DCRQ>IW{CEZe;nxMeN<?7_Dk^>*4Fz+<FO*WTv?zwmC*&bFews9Bd9Y2b+V<!RBCdusPTqYz{UDn}f~4<~rHjj?t{Qa_8<n+uS#`#<rI4PHLYOvBq<voZ}>T*AUxeMsrFzc-ptm^*grpmY(%Kwsmg_HGR$=GTWRGN9uF^jBSO_!RO#}@HzM#d=5SbpM%fA=iqbjIrtoW4nFs<;B$Ldf>=BN=l)o0V5@4Bpwd{PskB5z+7K66B3?3?ylR;Ax_6wbJFt~jAJ|Gi&%jp24q2v^x?{yT3~U9?0q1~oz&YR?a1J;JoCD4Q=YVs-Ip7>{4mj5l=lCa<f;enA>mO?kXAPb;G?zwX+0zqMnsX%hs7Q{{yDUTss{eF3UUMmkjqp{Lg4ihGkxVtGhLj(x$Z;$M0g;2qLF6EE5IKk(L=GYck%P!V<REeoIfxuYu8YViYwt>zn_f+?h<L`>1Lm9i!}D4xb36oD{t(NoA&PrWe3Lw3Nz*oTU@`Ozbfv0lrp#@GuaYviQNp8{Z*F7nBLcco+en!M&;jTGbO1U49e@r%2cQGc0q6j906G92fDS;{3F!2`@vQ9r!&$!~wT82D-}n$!BveF2R6{wj)Jvj0&%&^flJ0vrYpJEGHm7S3XWb~Frp>uS=9;7QNNrAII4f)pHV2!7&B5kibFews9Bd9Y2b+V<!RBCdu(@tFr|iDD=AI5p&n!(Y=l9V$S8Hgic3L>^C<)d^qC%p?C0$yhK2es0^-ceobE<CUoT{EVrygJCoE>s@q&laNIR~AC&Ozs(bI>{H9CQvk2c3h?LFb@z&^hQFbgrY$=?+6%@0~v9Y7K2|r<`kY^n}ach!P@@Ae1EDNG7r@p_u3$=yYA6)761aKf2W*+6=m51v(9&1JD8J0CWI403Co1KnI`$&;jTGbO1U49e@r%*9qt(-Jd>p-)ZN*QnhR@(8gqyJW)v)qLjBJ7~u#FGH^u&*G%<obCOoIx%KdMY;L`TM>FjlKUSNQ6gCH&gU!L_U~{lJ*c@yQHV2!7&B5kibFews9Bi(W&GEfMTNe)++sdn@&gmq%aGE6H6;ak&;+Sqo@J&d`NRf5ZH_!39V_SLkv90_wjcqmEdCm>lv5sx!7~2Y-1J8lyz;oa^@Emv!JO`cw&w=N_bKp7f9C)rH&zVo~+`V(>SgmCtWXjPsqHRo4MU+X2cqb^4lx3T9;>q`(JI763oHNyN&LDU0Gvb^9&H?9ubHF*^9B>Xe2b=@W0q1~oz&YR?a1J;Joa=^jY!A-S2h2G~zprF-$*QaxAqdx|Au8}he3K2)jccg!T5(Hzw>ehV=2&%`W1nZvIeN&FtwJBG%`w;<Yz{UDn}f~4=3sNMIoKR*4mJmygU!L_U~{m!PBzE%9-HG2u(`Lj2DL`cnU9Sik}5@1Lm3II{K_bGzVS?JXZp4|R(CxJR((APrXOM6oKi<>a}4W2z~*3cusPTqYz{UDn}f~4=3sNMIoKR*4mJmy>u7UwkIk_MO*wb<s@94SSqCnCB3U{{R4(nxAY&qpr_KxNtd{-joUE&JvbxU6N2hak$my}_oP^Fn=b&@YIp`d84mt;&gU&(cpmWeU=p1woItQKWqI2cE-Jdyk?-}Q2wKC_N)uzd&O_lSKs2COTE*K)MjjRa`<&O1ibETGQ+T2F?DmJ%KLQR{K2hBKlq?vQ2wqbLyIoKR*4mJmygU!L_U~{lJ*c@yQHV2!7&B5k6*_=^(Y)(92RO_VHY7nWBOf-^~OCblMsIkP$(z=Y&>+BjS<odz=V2e>zZEih$9h+M(;nB=EM~~I!jDpR<=3sNMIoKR*4mJmygU!L_U~{lJ*c@yQHV2#QW^;W1Y7h??)jFw_ImeBr%uz|AV1_8k5(%NSMwyA0F=k6+Q}(WNyspmi>N>|iPv)FHWK^r*$EtH2ItQJD&Ozs(bI>{H9CQvk2c3h?LFb@z&^hQFbgq-mX}y2WxqFXlJ+C$6oJvu!>>}ZfP(&GJiMOR`*#xU7WvP)`^lfvx>Ut0x;p^C(evIouaB-wIr?DOcYz{UDn}f~4=3sNMIoKR*4mJmygU!L_U~{m!ZZ@af9-C7n<u?z?o_k$uY^w??T9X}VL^MQY-4I6^C6cQq1>v3Zz0W(RTvef44_`;<)=PLaYeFb{q(Y}0gbqRnp@Yyt=pb|uItU$v4nhZ^gU~_fAaoEq2wgX!v$urqyUF19XMR>1OnEl=GT%H(4sy9#nRAJmvZr(;lw*j}!VxcmCW6ZnOS)`b?%m~VU6-@fUCutj^&pr#R+qDIIk+5L4lW0mgUi9?;Bs&|xEx##E(e!`%faQ~a@|~x?w@n+-lJMawQMeEnOJawMB_72GE?GmaD+vXQ)8);()Dh0w64w3>NZCo<65okSZ$8N=3sNMIoKR*4mJmygU!L_U~{lJ*c@yQHV2!7&2_Rlxo@pj{-6=9XWv)SIUlIvIZ5I&7eplyiIa+v^7UmVGUM8-zI9I4U9DACU#(TDN2hak$c|O#BvxyM&Ozs(bI>{H9CQvk2c3h?LFb@z&^hQFbPhV#N#~ehw{&hiTDysSTH*c>Yt1VsgASCcR^g%|Dhf}mj+Qiy%_>{2B;!3RT&a&f6;7y4Bijhyi^A<K;Q)ne%3E2^a+P`I)R6w;R(@bL6|U5FKMKd7a8NiX925=;2Ze*eLE)fqP&g<Y6b=dpg@eK!yTU1XOX1499*=?;uxCFXf^q*>YrtyRQ8*(qv8u^LMV5(EG?6B0p()Qo>E1C;$(ku~8{w;@#BG#-$>b`h#Fg4cN*pi_7zd04#sTAialklW954<T2aE&80poyiz_=TX`=b5n-53849Eug5F^&WP"
            )
        )
    )


def _github_status_failed() -> dict[str, Any]:
    """Captured 2026-09-22 from the GitHub legacy status endpoint."""
    return json.loads(
        gzip.decompress(
            base64.b85decode(
                "ABzY8>9(?J0{^v`+iv4F5Qgvb6sm5Um_Ed(0L3D0+AUfnF19JK-J%dEiIx~sqDoSB9KrA25p|*uZEP0Jg(H*me;koBL(;{S<G4~`Mcg}h$b*1L>0URjxg-)g{($d}Q|M3V$ptH|W2G{AhY-$aFQO{W{a(NmBuK_3%H$HB{>q6|5;Z6^Ad?{;g#F1l@Mm~58j>*{_Q`nW50|qM3CQwx8BS+`_CSw?*H~fU9S*5mWj!pQMZi-<Ql&lM7!8Jl>1g(0{mpHMq9Gv0qseSKnU4Fn)3zh!A@Ovt`|Ee!YV)wT|MvOkg`C~P-(z(D!m~|>IXPRpCnPcuG*gtPW&bCFLr2BLdEkL`{G77T(Hatx@T*P+ZxRu8_=?Ts-P}wkgL#jOs3!mjyby3bjYA|wWiRSrdLCX?Nh<A-sR71agEbUjqDbgrEgJTRlbinhrayfiOzy_xyTSZRXPal?d$!ti2cLgdQ556S^rD4#6<3&f0ngKNoPloBVMP@eTWG36v0Bl=XfnK=_bVUGwS0W|ezRadvEbwU4S)W$4${@@<J<4sr-xOuoNkH}y_N^l;x2Q>yr$EP<;EZrbX~>45;IBaR=!DzX#Il*#v_{6jKfImPe%Q5e{d=c!S(H8HQ#<2{*dF(SsZ?3Yyai9&C|={=IQzV^}ppuj+(Le6C@Hft#ZOx_J2+I60Z}!6T~WEhdvpKJ=-N`jBfzpojD9@6^9U-Mi)XrQ6LTp0#gWGJ7Y2^toGS7ZC&VpFnhbbY9Rz<%(<Z29-JyeX;<iSYVvZwEi&i3x2Ni0P!MoR-|%HHP#FxJmuG9Wbtfikh{^NSm3e`5@&((S=|w>38s_;z;(^X6%G*rm{=%3dBOw6bttr`fja`=ej2inElq%xkFwxnsOGH%|>q7B<f*e(xgWcucku=E|xpuZhIfN1LQ(ZehK0p8BFzO2|w!OBRaj`n6RG04IChEQEJ#hlivWBrDTMI;~D#}kI!C<>%4EtPQ*cdFBd-7uMYdeG@II%!$S;L4qx0y99YZz3@oSYZmCs0}~+YCzc#P7<TYfIhB8W&4Rqm&Q}P%ABvS%+PS<1~nEh>fzSpStzLk<D`Aw&=M&<Guv~dXbhE2u?+<KhW&i`BVZ+<uOl?r6sRa?(>weT2bws&?{=vnyY|ke~Bne9>Zyr<H!csN)6wj+P@KD=UuJsN>i!?^>f?G)Tvwls$JZ-bFdR1@bs2q4a|(45#BjTsIq!pEj*57YhoFHC#hwlUA1eJN9D6B&2_hRZT?4meRj8uqEd;9uQRM-a}F{rh}9I7LR9!zl3lOYD`L1{w%mjWt5mFX?LH!c7!GBZV~R!z$BhIleR^KD&V_KKW*mnW{<YG*6}R4ok5)K?>!t-w%Z^|PWzfr0tA%K_N7$4rx}?EbmxUALJ$dm#(lj8~n6Yb!Cq)Aa@d=kPqptgeSrzqC2hPLYy@d%l9%HLbr%@KjICO?2V|;5_L=18HTZz69)OFLkY+m-en)|;GQ}5O8)$ZzVszyXrVH5xW"
            )
        )
    )


def _gitea_status_success() -> dict[str, Any]:
    """Captured 2026-09-22 from the Gitea commit status endpoint."""
    return json.loads(
        gzip.decompress(
            base64.b85decode(
                "ABzY8>9(?J0{_)lOOM+&5dJFyPc2}_dRW%u)SiMKdg}JFMIcZVWiu;@3Q4WA4felxhNNVBA91%$Q3O~o%;7vgelw)M5N)X8q$GM`jB8C+L?0-u3l_yuk~h0JOKO_8yx#FF&GI}+7)z3ng{;|acM%vEX=qn5ISq!C+m&@WX|756#}^_RQl_))B95VI&rLO{4#b<iFr04iLzxkB&>J<uzr4tzUAXxuYrTPlc2T$k(}ruN#E5!e`<rvwPF)~|AnLa%x_5pL#*Dh$IQQ?E>^F22oBL>^_$c*R)H&(R8wzJ~F#IzVP4-gpj#JH5unEo<Ka7(;{D`WGQzI2AhpFrQk~bAa<2a0W>#$fy;Z3+Llc?Ngw^+c~T+w-4Ied5D65s3!zG)wC|6hFl*R|;Bp8q3=(=xsavD29$j-r2qah_(yf^n9m>2(fozgrGRQJL(@DES(|C!Gc0_*(*eZAfhUboV`z81XC0IKPIm(|P=`BDNF(t{+J0r*ayuh&&El&5~qAI=L4Jm#vxjbv4i)?+Cmc<fIs{P#dfP&F*ZI_f&N5E4`<1bk(c)+HJi_(uUI{50fIyvbav7B&4*hDa#n$795Z9VEXP=KTDc)*iQhY3odj;n;z?EX{R}EYg5gog<%-2?cYrpmiF(KEKpkvqkJ7j>$tcHlQIIH+F_x%HcBw7<NYEF@A<MEl{|=AETQJ;l2OmX_re6f4`8D-2bV`)!<q1xN_$3NG(8G)2x!2jF_3NGO1~ExVs^s7!IE*RJHbHO%C3_~2s7WyQ4-HmG3;k)Xq!$cppPZUnH+eo{Lwm;A6LY$UEuS>Us~i@+CK+SOiza<=)}MSBV`Bwpa%_C0G})AJ(>p)5P`8o8nK)OR&j41DSq8bb+;@G=yvD3y-*4p$S<|{6ZWz+j?<?!nOC}c;}}>B{}w$xEN_c}>51XaH=REC5U~H#Vp=ZR@8BC!xRV3Fo{ZO)N&rjgoc#qIiQ~n>hd7HI?k7HNVOkuiVTUu4p;oQvzz(w{hd3i2DxvknPsUN|<)d;k970KgO=id9Nf>9ysFqHhshn#_59Z8(Lq@R!^wOyxpNz|>ScmC4D(ooCAmvxbWo%~ws-?GQj-66eWZ??~W8|ubvhW(h0eU%caGswnIzZT{i9-(^D8kTiCva<r&MdLLfd^xtL+;#o<^!!eUK!Ezlh~<K?CDAl-9v?UjS}?)V_sKAI!$Y|<l-IKh*iVk3>rQ#F-P|a9>twFPY^4B3GoTCH2Pvg$&Z|&>@)+dpz3Xnm>qGd{%9}FAwSzlvpEX=-M+xFAK>Dwjt8Ko1_LC=(88EIi|R1wIXo6BoHydxp5iqGe^FTR&m*{lrnubIpC$^OXT4s6-Qs-$?Qo>3amah67t(QjyR`RlU)q9EIl<9C@dqc`a09r$ygxTc0}vbT`=p{R@aQuAS<$7Y%|mB@``JRtY1<{X>^m2y?Kx8*DtsUYZxB3R#@BCtoOA2>`iCdTIuB_=a~9J&<S~nzUD3wHuFl##&Esv3PvyBGp3L~l%I~S4Ub05w;j#48%oh{<n8in!H}Jjx`Xj*W+4q8e7?>LXuP7Ti3WmROm)8>O1o?oIoAJT@04Bfwazj2m;xiCg`PQ4pbNMar&Hp%pe*=@pzW@`naqc@1000"
            )
        )
    )


def _registry(
    routes: dict[str, Any], *, provider: str = "github"
) -> tuple[TicketProviderRegistry, list[str]]:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        payload = routes.get(request.url.path)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, httpx.Response):
            return payload
        return httpx.Response(200, json=payload)

    config = TicketsConfig(
        github=GitHubTicketConfig(
            enabled=provider == "github", owner="psf", repo="requests", token_env="TOKEN"
        ),
        gitea=GiteaTicketConfig(
            enabled=provider == "gitea", owner="gitea", repo="tea", token_env="TOKEN"
        ),
    )
    registry = TicketProviderRegistry(
        config,
        env={"TOKEN": "test"},
        transport=httpx.MockTransport(handler),
    )
    return registry, requests


def _predicate(*, provider: str = "github", sha: str = SHA) -> CiPredicate:
    if provider == "gitea":
        return CiPredicate(provider="gitea", owner="gitea", repo="tea", head_sha=sha)
    return CiPredicate(provider="github", owner="psf", repo="requests", head_sha=sha)


def test_no_checks_yet_is_not_terminal() -> None:
    """The post-push empty response must not be mistaken for passed CI."""
    registry, _requests = _registry(
        {
            f"/repos/psf/requests/commits/{SHA}/check-runs": {"total_count": 0, "check_runs": []},
            f"/repos/psf/requests/commits/{SHA}/status": {"state": "pending", "statuses": []},
        }
    )

    assert CiWatcher(registry).evaluate(_predicate(), NOW) is None


def test_running_check_is_not_terminal() -> None:
    """A visible but unfinished check must keep the watch pending."""
    registry, _requests = _registry(
        {
            f"/repos/psf/requests/commits/{SHA}/check-runs": {
                "total_count": 1,
                "check_runs": [
                    {
                        "name": "build",
                        "status": "in_progress",
                        "conclusion": None,
                        "details_url": "https://github.com/psf/requests/actions/runs/34145371879",
                    }
                ],
            },
            f"/repos/psf/requests/commits/{SHA}/status": {"state": "pending", "statuses": []},
        }
    )

    assert CiWatcher(registry).evaluate(_predicate(), NOW) is None


def test_mixed_pass_fail_is_terminal_and_names_failed_check() -> None:
    """A completed mixed result reaches the recipient with the failure named."""
    checks = _github_check_runs_failed()
    statuses = _github_status_failed()
    registry, _requests = _registry(
        {
            f"/repos/psf/requests/commits/{SHA}/check-runs": checks,
            f"/repos/psf/requests/commits/{SHA}/status": statuses,
        }
    )

    outcome = CiWatcher(registry).evaluate(_predicate(), NOW)

    assert outcome is not None
    assert outcome.ok is False
    assert "lint" in outcome.summary
    assert "passed" in outcome.summary
    assert "failed" in outcome.summary
    assert outcome.url == "https://github.com/psf/requests/runs/101816313580"


def test_two_watches_for_one_commit_share_one_probe() -> None:
    """Dedupe belongs to an observation, not a callback registration."""
    checks = _github_check_runs_failed()
    statuses = _github_status_failed()
    registry, requests = _registry(
        {
            f"/repos/psf/requests/commits/{SHA}/check-runs": checks,
            f"/repos/psf/requests/commits/{SHA}/status": statuses,
        }
    )
    watcher = CiWatcher(registry)

    assert watcher.evaluate(_predicate(), NOW) is not None
    assert watcher.evaluate(_predicate(), NOW) is not None
    assert requests == [
        f"/repos/psf/requests/commits/{SHA}/check-runs",
        f"/repos/psf/requests/commits/{SHA}/status",
    ]


def test_network_error_is_not_a_failed_outcome() -> None:
    """An unavailable forge is uncertainty, never a red build."""
    registry, _requests = _registry(
        {f"/repos/psf/requests/commits/{SHA}/check-runs": httpx.ConnectTimeout("offline")}
    )

    assert CiWatcher(registry).evaluate(_predicate(), NOW) is None


def test_network_error_backs_off_the_shared_probe() -> None:
    """A rate limit or timeout must not be multiplied by each registration."""
    registry, requests = _registry(
        {f"/repos/psf/requests/commits/{SHA}/check-runs": httpx.ConnectTimeout("offline")}
    )
    watcher = CiWatcher(registry)

    assert watcher.evaluate(_predicate(), NOW) is None
    assert watcher.evaluate(_predicate(), NOW) is None
    assert requests == [f"/repos/psf/requests/commits/{SHA}/check-runs"]


def test_gitea_captured_statuses_normalize_to_a_settled_success() -> None:
    """Gitea's captured combined status shape is read through its own endpoint."""
    payload = _gitea_status_success()
    sha = str(payload["sha"])
    registry, requests = _registry(
        {f"/api/v1/repos/gitea/tea/commits/{sha}/status": payload}, provider="gitea"
    )

    outcome = CiWatcher(registry).evaluate(_predicate(provider="gitea", sha=sha), NOW)

    assert outcome is not None
    assert outcome.ok is True
    assert "goreleaser" in outcome.summary
    assert requests == [f"/api/v1/repos/gitea/tea/commits/{sha}/status"]
