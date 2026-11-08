"""Install monitoring, a scoped budget, and daily historical-data snapshots.

Requires a deployed public URL. Preview only unless --apply is supplied.
"""
import argparse
import json
import subprocess
import time
from urllib.parse import urlparse
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google.cloud import bigquery

PROJECT='yotamsofer-513010'


def gc(*args): return subprocess.check_output(['gcloud',*args],text=True).strip()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    host=urlparse(args.url).hostname
    assert host and args.url.startswith('https://')
    if not args.apply:
        print(json.dumps({'public_host':host,'uptime_interval_seconds':300,
                          'monthly_budget_ils':160,'daily_snapshots_retained_days':30,
                          'not_applied':True},indent=2)); return
    gc('services','enable','monitoring.googleapis.com','billingbudgets.googleapis.com',
       'bigquerydatatransfer.googleapis.com',f'--project={PROJECT}')
    session=AuthorizedSession(Credentials(gc('auth','print-access-token'), quota_project_id=PROJECT))
    def api(method,url,**kwargs):
        for attempt in range(3):
            response=session.request(method,url,timeout=30,**kwargs)
            if response.status_code not in {429,500,502,503,504}:
                break
            time.sleep(2*(attempt+1))
        if not response.ok:
            message=json.dumps(response.json().get('error',{}))
            raise RuntimeError(f"{response.status_code}: {message}")
        return response.json() if response.content else {}
    base=f'https://monitoring.googleapis.com/v3/projects/{PROJECT}'
    channels=api('GET',base+'/notificationChannels').get('notificationChannels',[])
    email=gc('config','get-value','account')
    channel=next((c for c in channels if c.get('type')=='email' and c.get('labels',{}).get('email_address')==email),None)
    if not channel:
        channel=api('POST',base+'/notificationChannels',json={
            'type':'email','displayName':'Smart Nature owner','labels':{'email_address':email}})
    checks=api('GET',base+'/uptimeCheckConfigs').get('uptimeCheckConfigs',[])
    check=next((c for c in checks if c.get('displayName')=='Smart Nature public health'),None)
    if not check:
        check=api('POST',base+'/uptimeCheckConfigs',json={
            'displayName':'Smart Nature public health',
            'monitoredResource':{'type':'uptime_url','labels':{'project_id':PROJECT,'host':host}},
            'httpCheck':{'path':'/_stcore/health','port':443,'useSsl':True,'validateSsl':True},
            'period':'300s','timeout':'10s'})
    check_id=check['name'].split('/')[-1]
    policies=api('GET',base+'/alertPolicies').get('alertPolicies',[])
    if not any(p.get('displayName')=='Smart Nature unavailable' for p in policies):
        api('POST',base+'/alertPolicies',json={
            'displayName':'Smart Nature unavailable','combiner':'OR','enabled':True,
            'notificationChannels':[channel['name']],
            'conditions':[{'displayName':'Health check failing','conditionThreshold':{
                'filter':f'metric.type="monitoring.googleapis.com/uptime_check/check_passed" AND resource.type="uptime_url" AND metric.label.check_id="{check_id}"',
                'comparison':'COMPARISON_LT','thresholdValue':1,'duration':'300s',
                'aggregations':[{'alignmentPeriod':'300s','perSeriesAligner':'ALIGN_FRACTION_TRUE'}],
                'trigger':{'count':1}}}]})
    billing=json.loads(gc('billing','projects','describe',PROJECT,'--format=json'))['billingAccountName']
    number=gc('projects','describe',PROJECT,'--format=value(projectNumber)')
    budget_url=f'https://billingbudgets.googleapis.com/v1/{billing}/budgets'
    budgets=api('GET',budget_url).get('budgets',[])
    existing_budget=next((b for b in budgets if b.get('displayName')=='Smart Nature demo'),None)
    desired_amount={'specifiedAmount':{'currencyCode':'ILS','units':'160'}}
    if existing_budget and existing_budget.get('amount') != desired_amount:
        api('PATCH','https://billingbudgets.googleapis.com/v1/'+existing_budget['name'],
            params={'updateMask':'amount'},json={'name':existing_budget['name'],'amount':desired_amount})
    if not existing_budget:
        created_budget=api('POST',budget_url,json={
            'displayName':'Smart Nature demo','budgetFilter':{'projects':[f'projects/{number}']},
            'amount':desired_amount,
            'thresholdRules':[{'thresholdPercent':0.5},{'thresholdPercent':0.9},{'thresholdPercent':1.0}],
            'notificationsRule':{'monitoringNotificationChannels':[channel['name']],
                              'disableDefaultIamRecipients':False}})
        print('Created budget amount: '+json.dumps(created_budget['amount']),flush=True)
    # Snapshots exclude guest conversations and photos to respect demo retention.
    bq=bigquery.Client(project=PROJECT,credentials=session.credentials)
    ds=bigquery.Dataset(f'{PROJECT}.smartnature_backups'); ds.location='us-central1'
    ds.default_table_expiration_ms=30*86400*1000
    bq.create_dataset(ds,exists_ok=True,timeout=30)
    api('POST',f'https://serviceusage.googleapis.com/v1beta1/projects/{number}/services/bigquerydatatransfer.googleapis.com:generateServiceIdentity',json={})
    backup_account=f'smartnature-backup@{PROJECT}.iam.gserviceaccount.com'
    accounts=gc('iam','service-accounts','list',f'--project={PROJECT}','--format=value(email)').splitlines()
    if backup_account not in accounts:
        gc('iam','service-accounts','create','smartnature-backup',f'--project={PROJECT}')
    gc('projects','add-iam-policy-binding',PROJECT,f'--member=serviceAccount:{backup_account}',
       '--role=roles/bigquery.jobUser','--condition=None','--format=none')
    gc('iam','service-accounts','add-iam-policy-binding',backup_account,f'--project={PROJECT}',
       f'--member=serviceAccount:service-{number}@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com',
       '--role=roles/iam.serviceAccountTokenCreator','--format=none')
    for dataset_id,role in [('smart_nature_DB','READER'),('smartnature_backups','OWNER')]:
        dataset=bq.get_dataset(f'{PROJECT}.{dataset_id}',timeout=30)
        entries=[e for e in dataset.access_entries if e.entity_id!=backup_account]
        entries.append(bigquery.AccessEntry(role,'userByEmail',backup_account))
        dataset.access_entries=entries
        bq.update_dataset(dataset,['access_entries'],timeout=30)
    query='\n'.join(
        f"EXECUTE IMMEDIATE FORMAT(\"CREATE SNAPSHOT TABLE IF NOT EXISTS `{PROJECT}.smartnature_backups.{name}_%s` CLONE `{PROJECT}.smart_nature_DB.{name}` OPTIONS(expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 DAY))\", FORMAT_DATE('%Y%m%d',CURRENT_DATE('UTC')));"
        for name in ['users','reserves','reports','chat_history'])
    transfer=f'https://bigquerydatatransfer.googleapis.com/v1/projects/{PROJECT}/locations/us-central1/transferConfigs'
    configs=api('GET',transfer).get('transferConfigs',[])
    if not any(c.get('displayName')=='Smart Nature daily snapshots' for c in configs):
        api('POST',transfer,params={'serviceAccountName':backup_account},json={
            'displayName':'Smart Nature daily snapshots','dataSourceId':'scheduled_query',
            'schedule':'every 24 hours','params':{'query':query},
            'emailPreferences':{'enableFailureEmail':True}})
    print('Monitoring, owner notifications, ILS 160 budget and daily snapshots configured.')
    print('A budget alert does not enforce a spending cap.')


if __name__=='__main__': main()
