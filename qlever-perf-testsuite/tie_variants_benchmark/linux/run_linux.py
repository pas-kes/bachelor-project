import subprocess,re,statistics,collections,sys,json
N=int(sys.argv[1])
def run(b):
    r=subprocess.run([f'/w/out/{b}'],capture_output=True,text=True); txt=r.stdout+r.stderr
    name=None; out={}
    for l in txt.splitlines():
        m=re.search(r'Running measurement "(.*)" \.\.\.',l)
        if m: name=m.group(1)
        m=re.search(r'Done in ([0-9.e+-]+) seconds',l)
        if m and name: out[name]=float(m.group(1))*1000; name=None
    return out
result={}
for comp in ['gcc','clang']:
    res={'plain':collections.defaultdict(list),'split':collections.defaultdict(list)}; order=[]
    for i in range(N):
        seq=['plain','split'] if i%2==0 else ['split','plain']
        for k in seq:
            for n,v in run(f'bench_{comp}_{k}').items():
                if n not in order: order.append(n)
                res[k][n].append(v)
        print(comp,'run',i,flush=True)
    result[comp]={'order':order,'data':{k:{n:v for n,v in d.items()} for k,d in res.items()}}
json.dump(result,open('/w/out/linux_results.json','w'))
print('done',flush=True)
