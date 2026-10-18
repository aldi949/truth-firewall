"""Frozen development falsification set; independent current-source oracle, no coding worker."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

ORACLE = """import json,sys
from pathlib import Path
request=json.load(sys.stdin)
namespace={}
exec(compile(Path(request['path']).read_bytes(), request['path'], 'exec'),namespace)
print(json.dumps(namespace[request['symbol']](*request['args']),sort_keys=True,allow_nan=False,separators=(',',':')))
"""

def run(cases_path: Path, work: Path, source: Path | None) -> dict:
    if source:
        sys.path.insert(0, str(source.resolve()))
    from truth_firewall.completion import AcceptanceCondition, TaskCompletionContract, TaskCompletionVerifier
    work.mkdir(parents=True, exist_ok=False)
    counts = {key: 0 for key in ('TRUE_ACCEPT','FALSE_ACCEPT','TRUE_REJECT','FALSE_REJECT','UNKNOWN')}
    records = []
    for case in json.loads(cases_path.read_text(encoding='utf-8'))['cases']:
        root = work / case['id']; root.mkdir()
        def write(files):
            for name, content in files.items():
                path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content,encoding='utf-8')
        write(case.get('precompile_files', {}))
        timestamps={name:(root/name).stat().st_mtime_ns for name in case.get('precompile_files', {})}
        for name in timestamps: py_compile.compile(str(root/name),doraise=True)
        write(case['files'])
        for name,stamp in timestamps.items():os.utime(root/name,ns=(stamp,stamp))
        conditions=[]
        for raw in case['checks']:
            values=dict(raw); values['condition_id']=values.pop('id')
            if 'cases' in values:values['cases']=tuple((tuple(a),b) for a,b in values['cases'])
            conditions.append(AcceptanceCondition(**values))
        contract=TaskCompletionContract(case['id'],case['original_task'],tuple(conditions))
        attempt=TaskCompletionVerifier(root,contract).attempt_done(case.get('worker_claim','DONE'))
        if 'after_verification_files' in case:write(case['after_verification_files'])
        decision=(attempt.current_decision(root) if 'after_verification_files' in case else attempt.decision).value
        oracle=case['oracle']; complete=None; outputs=[]
        if oracle:
            if 'file_exists' in oracle:complete=(root/oracle['file_exists']).is_file()
            else:
                complete=True
                for args,expected in oracle['cases']:
                    result=subprocess.run([sys.executable,'-I','-c',ORACLE],input=json.dumps({'path':str((root/oracle['path']).resolve()),'symbol':oracle['symbol'],'args':args}),capture_output=True,text=True,encoding='utf-8',timeout=10)
                    passed=result.returncode==0 and result.stdout.strip()==json.dumps(expected,sort_keys=True,allow_nan=False,separators=(',',':'))
                    complete &= passed; outputs.append({'args':args,'expected':expected,'exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'passed':passed})
        assert complete is case['oracle_expected_complete'],f"Independent oracle setup mismatch: {case['id']}"
        classification=('UNKNOWN' if complete is None or decision=='HUMAN_REQUIRED' else
                        ('TRUE_ACCEPT' if complete else 'FALSE_ACCEPT') if decision=='VERIFIED_DONE' else
                        ('FALSE_REJECT' if complete else 'TRUE_REJECT'))
        counts[classification]+=1
        records.append({'id':case['id'],'original_task':case['original_task'],'acceptance_checks':case['checks'],'candidate_files':{name:(root/name).read_text(encoding='utf-8') for name in case['files']},'worker_used':False,'worker_claim':case.get('worker_claim','DONE'),'decision':decision,'oracle_complete':complete,'oracle_outputs':outputs,'classification':classification,'evidence':[{'condition':e.condition_id,'verdict':e.verdict.value,'observation':e.observation} for e in attempt.evidence],'known_contract_limitation':case.get('limitation')})
    result={'benchmark_sha256':hashlib.sha256(cases_path.read_bytes()).hexdigest(),'python':sys.executable,'source_override':str(source) if source else None,'worker':'NOT_USED; verifier-only comparison','classification':'HUMAN_REQUIRED always UNKNOWN, never silently counted as reject','counts':counts,'cases':records}
    (work/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--cases',type=Path,required=True); parser.add_argument('--work',type=Path,required=True); parser.add_argument('--source',type=Path)
    a=parser.parse_args(); result=run(a.cases,a.work,a.source);print(json.dumps({'counts':result['counts'],'evidence':str((a.work/'results.json').resolve())},indent=2))
