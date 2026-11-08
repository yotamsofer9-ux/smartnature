"""Validate a backup, or restore to a NEW dataset with WRITE_EMPTY protection.

Example: python infra/restore_backup.py backups/STAMP --dataset restore_check --apply
Credentials use the signed-in gcloud user, never a key copied into the archive.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from google.api_core.exceptions import NotFound


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup',type=Path)
    parser.add_argument('--dataset',default='smartnature_restore_check')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    manifest=json.loads((args.backup/'manifest.json').read_text())
    restored={}
    for name,meta in manifest['tables'].items():
        path=args.backup/f'{name}.json'
        assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['sha256']
        restored[name]=json.loads(path.read_text())
        assert len(restored[name]['rows'])==meta['rows']
    print('Backup checksums, schemas and row counts verified.',flush=True)
    if not args.apply: return
    project=manifest['project']
    token=subprocess.check_output(['gcloud','auth','print-access-token'],text=True).strip()
    client=bigquery.Client(project=project,credentials=Credentials(token))
    ds_id=f'{project}.{args.dataset}'
    try: client.get_dataset(ds_id,timeout=30)
    except NotFound: pass
    else: raise RuntimeError('Restore requires a new dataset; existing dataset was not changed')
    dataset=bigquery.Dataset(ds_id);dataset.location='us-central1'
    dataset.default_table_expiration_ms=86400*1000
    client.create_dataset(dataset,timeout=30)
    for name,data in restored.items():
        schema=[bigquery.SchemaField.from_api_repr(f) for f in data['schema']]
        body='\n'.join(json.dumps(row,ensure_ascii=False) for row in data['rows']).encode()
        config=bigquery.LoadJobConfig(schema=schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition='WRITE_EMPTY')
        client.load_table_from_file(io.BytesIO(body),f'{ds_id}.{name}',
            job_config=config,location=dataset.location,timeout=30).result(timeout=90)
        table=client.get_table(f'{ds_id}.{name}',timeout=30)
        assert table.num_rows==len(data['rows'])
        assert [f.to_api_repr() for f in table.schema]==data['schema']
        print(f'Restored and verified {name}: {table.num_rows} rows',flush=True)


if __name__=='__main__': main()
