"""Deploy the verified image only after hosting/cost approval.

Run without --apply to inspect the exact deployment command.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

PROJECT='yotamsofer-513010'
REGION='us-central1'
SERVICE='smartnature'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    release=json.loads(Path('infra/release_manifest.json').read_text())
    assert release['status']=='SUCCESS'
    for name,expected in release['file_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected, f'Rebuild required: {name}'
    # A single warm instance makes the baseline predictable for a small demo.
    command=['gcloud','run','deploy',SERVICE,f'--project={PROJECT}',f'--region={REGION}',
        f"--image={release['image']}",
        f'--service-account=smartnature-runtime@{PROJECT}.iam.gserviceaccount.com',
        '--env-vars-file=infra/runtime.env.yaml','--port=8080','--cpu=1','--memory=1Gi',
        '--min=1','--max=1','--concurrency=15','--timeout=3600',
        '--session-affinity','--cpu-throttling','--allow-unauthenticated',
        '--ingress=all','--execution-environment=gen2','--format=json']
    if not args.apply:
        print(json.dumps({'deployment_command':command,'public':True,'min_instances':1,
                          'max_instances':1,'not_deployed':True},indent=2))
        return
    subprocess.run(['gcloud','services','enable','run.googleapis.com',f'--project={PROJECT}'],check=True)
    result=json.loads(subprocess.check_output(command,text=True))
    url=result['status']['url']
    with urllib.request.urlopen(url+'/_stcore/health',timeout=20) as response:
        assert response.status==200
    Path('infra/deployment_result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({'url':url,'public_health_verified':True}))


if __name__=='__main__': main()
