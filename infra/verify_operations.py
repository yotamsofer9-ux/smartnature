"""Verify live deployment operations, and run the first scheduled backup now."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import time
import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google.cloud import bigquery

PROJECT='yotamsofer-513010'
URL='https://smartnature-7fr5l6n5la-uc.a.run.app'

def gc(*args): return subprocess.check_output(['gcloud',*args],text=True).strip()


def main():
    session=AuthorizedSession(Credentials(gc('auth','print-access-token'),quota_project_id=PROJECT))
    def api(method,url,**kwargs):
        r=session.request(method,url,timeout=30,**kwargs)
        r.raise_for_status()
        return r.json()
    base=f'https://monitoring.googleapis.com/v3/projects/{PROJECT}'
    checks=api('GET',base+'/uptimeCheckConfigs').get('uptimeCheckConfigs',[])
    check=next(c for c in checks if c.get('displayName')=='Smart Nature public health')
    assert check['httpCheck']['path']=='/_stcore/health'
    assert check['monitoredResource']['labels']['host']==URL.split('https://')[1]
    policies=api('GET',base+'/alertPolicies').get('alertPolicies',[])
    alert=next(p for p in policies if p.get('displayName')=='Smart Nature unavailable')
    assert alert.get('enabled') and alert.get('notificationChannels')
    billing=json.loads(gc('billing','projects','describe',PROJECT,'--format=json'))['billingAccountName']
    budgets=api('GET',f'https://billingbudgets.googleapis.com/v1/{billing}/budgets').get('budgets',[])
    budget=next(b for b in budgets if b.get('displayName')=='Smart Nature demo')
    amount=budget['amount']['specifiedAmount']
    assert amount=={'currencyCode':'ILS','units':'160'},amount
    assert budget['notificationsRule'].get('monitoringNotificationChannels')
    assert {r['thresholdPercent'] for r in budget['thresholdRules']}=={0.5,0.9,1.0}
    print('Uptime monitoring, notifications and ILS 160 budget verified.',flush=True)
    root=f'https://bigquerydatatransfer.googleapis.com/v1/projects/{PROJECT}/locations/us-central1'
    configs=api('GET',root+'/transferConfigs').get('transferConfigs',[])
    config=next(c for c in configs if c.get('displayName')=='Smart Nature daily snapshots')
    assert not config.get('disabled') and config['schedule']=='every 24 hours'
    runs=api('GET','https://bigquerydatatransfer.googleapis.com/v1/'+config['name']+'/runs').get('transferRuns',[])
    today=dt.datetime.now(dt.timezone.utc).date().isoformat()
    successful=next((r for r in runs if r.get('state')=='SUCCEEDED' and r.get('runTime','').startswith(today)),None)
    if successful:
        run=successful
    else:
        active=next((r for r in runs if r.get('state') in {'PENDING','RUNNING'} and r.get('runTime','').startswith(today)),None)
        started={'runs':[active]} if active else api('POST','https://bigquerydatatransfer.googleapis.com/v1/'+config['name']+':startManualRuns',
                    json={'requestedRunTime':dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z')})
        run=started['runs'][0]
        print('First scheduled-identity backup requested.',flush=True)
        for attempt in range(75):
            run=api('GET','https://bigquerydatatransfer.googleapis.com/v1/'+run['name'])
            if run.get('state') in {'SUCCEEDED','FAILED','CANCELLED'}: break
            time.sleep(4)
        assert run.get('state')=='SUCCEEDED',run
    bq=bigquery.Client(project=PROJECT,credentials=session.credentials)
    suffix=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d')
    counts={}
    for name in ['users','reserves','reports','chat_history']:
        backup=bq.get_table(f'{PROJECT}.smartnature_backups.{name}_{suffix}',timeout=30)
        source=bq.get_table(f'{PROJECT}.smart_nature_DB.{name}',timeout=30)
        assert backup.table_type=='SNAPSHOT' and backup.num_rows==source.num_rows
        assert backup.expires is not None
        counts[name]=backup.num_rows
    response=requests.get(URL+'/_stcore/health',timeout=20)
    assert response.status_code==200 and response.text.strip()=='ok'
    service=json.loads(gc('run','services','describe','smartnature',f'--project={PROJECT}',
                          '--region=us-central1','--format=json'))
    assert service['metadata']['annotations']['run.googleapis.com/minScale']=='1'
    assert service['metadata']['annotations']['run.googleapis.com/maxScale']=='1'
    assert service['spec']['template']['spec']['serviceAccountName']==f'smartnature-runtime@{PROJECT}.iam.gserviceaccount.com'
    now=dt.datetime.now(dt.timezone.utc)
    params={'filter':f'metric.type="monitoring.googleapis.com/uptime_check/check_passed" AND metric.label.check_id="{check["name"].split("/")[-1]}"',
            'interval.endTime':now.isoformat().replace('+00:00','Z'),
            'interval.startTime':(now-dt.timedelta(minutes=20)).isoformat().replace('+00:00','Z'),
            'view':'FULL'}
    metrics=api('GET',base+'/timeSeries',params=params).get('timeSeries',[])
    probe_values=[s['points'][0]['value'].get('boolValue') for s in metrics if s.get('points')]
    result={'url':URL,'verified_at':now.isoformat(),
            'revision':service['status']['latestReadyRevisionName'],
            'public_health_ok':True,'warm_instances':1,'max_instances':1,
            'budget':amount,'uptime_monitoring':True,'latest_probe_values':probe_values,
            'daily_backup_state':run['state'],'snapshot_rows':counts,
            'snapshot_retention_days':30}
    Path('infra/operations_result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__=='__main__': main()
