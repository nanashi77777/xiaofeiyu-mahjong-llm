"""Read-only audit of production experience provenance, freezes and recalls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from majsoul_llm.store import Store
from majsoul_llm.yaku import YakuReference


def run(args):
    store=Store(args.database)
    try:
        revisions=[json.loads(r[0]) for r in store.db.execute('SELECT payload FROM memories ORDER BY created_at')]
        indexed={(m['id'],m['revision']):m for m in revisions}
        errors=[]
        for memory in revisions:
            for source in memory['supports']+memory['counterexamples']:
                if not store.db.execute('SELECT 1 FROM events WHERE id=?',(source,)).fetchone():
                    errors.append({'memory_id':memory['id'],'missing_event':source})
            if memory['catalog_version'] is not None:
                if memory['catalog_version']!=YakuReference().catalog['catalog_version']:
                    errors.append({'memory_id':memory['id'],'unknown_catalog':memory['catalog_version']})
        decisions={r['payload']['action']['window_id']:r['id']
                   for r in store.records('decision',limit=100000)}
        freezes={r['match_id']:r for r in store.records('memory_freeze',limit=100000)}
        recalls=[]
        for row in store.records('memory_recall',limit=100000):
            refs=row['payload']['revisions']
            if not refs:
                continue
            freeze=freezes.get(row['match_id'])
            frozen={(m['id'],m['revision']) for m in freeze['payload']['memories']} if freeze else set()
            for ref in refs:
                key=(ref['id'],ref['revision'])
                if key not in indexed or key not in frozen:
                    errors.append({'recall_id':row['id'],'invalid_or_unfrozen_reference':ref})
            window=row['payload']['window_id']
            recalls.append({'recall_id':row['id'],'match_id':row['match_id'],'window_id':window,
                            'decision_record_id':decisions.get(window),'revisions':refs})
        report={'kind':'read_only_production_memory_provenance','revisions':len(revisions),
                'latest_memories':len({m['id'] for m in revisions}),
                'status_counts':{status:sum(m['status']==status for m in revisions)
                                 for status in ('candidate','stable','inactive')},
                'freeze_count':len(freezes),'nonempty_recalls':len(recalls),
                'recalls_with_model_decision':sum(r['decision_record_id'] is not None for r in recalls),
                'errors':errors,'recalls':recalls,'production_data_modified':False,
                'proves_improved_results':False}
        report['passed']=bool(revisions and recalls and not errors)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({k:v for k,v in report.items() if k!='recalls'},ensure_ascii=False))
        return 0 if report['passed'] else 1
    finally:
        store.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('runtime/archive.sqlite'))
    parser.add_argument('--output',type=Path,default=Path('runtime/memory-provenance-v1.json'))
    raise SystemExit(run(parser.parse_args()))
