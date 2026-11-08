"""Build and smoke-test an image in Cloud Build; this does NOT deploy a website."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

PROJECT='yotamsofer-513010'
REGION='us-central1'

def run(*args):
    return subprocess.check_output(['gcloud',*args],text=True).strip()


def main():
    files=run('meta','list-files-for-upload').splitlines()
    for name in files:
        p=Path(name)
        assert not any(part in {'backups','.git','.venv','venv','.python-build'} for part in p.parts),name
        assert p.name!='secrets.toml' and p.suffix not in {'.json','.sqlite'},name
    run('services','enable','cloudbuild.googleapis.com','artifactregistry.googleapis.com',f'--project={PROJECT}')
    existing=run('artifacts','repositories','list',f'--project={PROJECT}',f'--location={REGION}',
                 '--format=value(name)')
    if not any(x.split('/')[-1]=='smartnature' for x in existing.splitlines()):
        run('artifacts','repositories','create','smartnature',f'--project={PROJECT}',
            f'--location={REGION}','--repository-format=docker')
    fingerprint=hashlib.sha256()
    inputs=['app.py','auth_manager.py','domain.py','ai_service.py','repository.py',
            'requirements.txt','Dockerfile','assets/reserves_demo.csv','assets/display_names.py',
            '.streamlit/config.toml']+[str(p) for p in sorted(Path('tests').glob('*.py'))]
    hashes={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in inputs}
    for name in inputs:
        fingerprint.update(name.encode()); fingerprint.update(Path(name).read_bytes())
    tag='demo-'+fingerprint.hexdigest()[:12]
    image=f'{REGION}-docker.pkg.dev/{PROJECT}/smartnature/app:{tag}'
    result=run('builds','submit','.',f'--project={PROJECT}',f'--region={REGION}',
               '--config=cloudbuild.yaml',f'--substitutions=_IMAGE={image}','--format=json')
    build=json.loads(result)
    assert build['status']=='SUCCESS'
    digest=next(i['digest'] for i in build['results']['images'] if i['name']==image)
    immutable=image.split(':')[0]+'@'+digest
    manifest={'image':immutable,'image_tag':image,'file_sha256':hashes,'build_id':build['id'],'status':build['status'],
              'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'fingerprint':fingerprint.hexdigest()}
    Path('infra/release_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest),flush=True)


if __name__=='__main__': main()
