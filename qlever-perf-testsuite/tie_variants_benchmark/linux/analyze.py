import subprocess,re,sys,glob
for f in sorted(glob.glob('/w/real/LT_*_*.o')):
    d=subprocess.run(['objdump','-dr','-C','--no-show-raw-insn',f],capture_output=True,text=True).stdout.split('\n')
    cur=None; funcs={}
    for l in d:
        m=re.match(r'^[0-9a-f]+ <(.*)>:$',l)
        if m: cur=m.group(1); funcs[cur]=[]; continue
        if cur: funcs[cur].append(l)
    rows=[]
    for n,ls in funcs.items():
        if n.startswith('LocatedTriplesPerBlock::mergeTriplesImpl<') or n.startswith('IdTable LocatedTriplesPerBlock::mergeTriplesImpl<'):
            ins=sum(1 for l in ls if re.match(r'^\s*[0-9a-f]+:\s',l))
            cmpcalls=sum(1 for l in ls if re.search(r'CALL26.*ValueId::compareThreeWay',l))
            rows.append((re.sub(r'.*mergeTriplesImpl','mergeTriplesImpl',n)[:34],ins,cmpcalls))
    print(f.split('/')[-1], sorted(rows))
