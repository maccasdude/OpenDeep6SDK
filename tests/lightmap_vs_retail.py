"""Relight retail levels with the SDK light model and compare every luxel with
the retail .ls. Usage: python3 tests/lightmap_vs_retail.py crypta,minesb [GAMEDIR]"""
import sys, struct, numpy as np
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'formats'))
import d6level, d6bspc
g=sys.argv[2] if len(sys.argv) > 2 else '.'
for lvl in sys.argv[1].split(','):
    b=d6level.BSPFile.load(d6level.find_file(g,lvl+'.bsp'))
    ll=d6level.LightList.load(d6level.find_file(g,lvl+'.lgt')).lights
    lights=[d6bspc.Light((l.x,l.y,l.z), l.intensity) for l in ll]
    lf=d6level.FaceLightInfo.load(d6level.find_file(g,lvl+'.lf')).faces
    ls=d6level.LightData.load(d6level.find_file(g,lvl+'.ls')).data
    res={}
    for name,amb,pk in (('old',10.0,30.0),('new',d6bspc.DEFAULT_AMBIENT,d6bspc.DEFAULT_PEAK)):
        m=d6bspc.LightModel(lights, amb, pk, tracer=d6bspc.Tracer(b))
        lf2,ls2,_,_=d6bspc.compute_lightmaps(b,m,log=lambda *a:None)
        A=[];B=[]; same=0
        for fi,(r1,r2) in enumerate(zip(lf,lf2.faces)):
            if (r1.smin,r1.tmin,r1.width,r1.height)!=(r2.smin,r2.tmin,r2.width,r2.height): continue
            same+=1
            n=r1.width*r1.height
            a=np.frombuffer(ls,'<u2',n,r1.offset); c=np.frombuffer(ls2.data,'<u2',n,r2.offset)
            A.append(((a>>5)&63)/63*30); B.append(((c>>5)&63)/63*30)
        A=np.concatenate(A); B=np.concatenate(B)
        res[name]=(same,len(lf),np.sqrt(np.mean((A-B)**2)),A.mean(),B.mean())
    print(lvl, 'faces with equal extents %d/%d' % res['new'][:2], ' '.join('%s rmse %.2f mean retail %.2f ours %.2f' % (k,v[2],v[3],v[4]) for k,v in res.items()))
