"""Install monitoring, a scoped budget, and daily historical-data snapshots.

Requires a deployed public URL. Preview only unless --apply is supplied.
"""
import argparse
import json
import subprocess
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
                          'monthly_budget_usd':50,'daily_snapshots_retained_days':30,
                          'not_applied':True},indent=2)); return
    gc('services','enable','monitoring.googleapis.com','billingbudgets.googleapis.com',
       'bigquerydatatransfer.googleapis.com',f'--project={PROJECT}')
    session=AuthorizedSession(Credentials(gc('auth','print-access-token')))
    def api(method,url,**kwargs):
        response=session.request(method,url,timeout=30,**kwargs)
        response.raise_for_status()
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
    if not any(b.get('displayName')=='Smart Nature demo' for b in budgets):
        api('POST',budget_url,json={
            'displayName':'Smart Nature demo','budgetFilter':{'projects':[f'projects/{number}']},
            'amount':{'specifiedAmount':{'currencyCode':'USD','units':'50'}},
            'thresholdRules':[{'thresholdPercent':0.5},{'thresholdPercent':0.9},{'thresholdPercent':1.0}],
            'allUpdatesRule':{'monitoringNotificationChannels':[channel['name']],
                              'disableDefaultIamRecipients':False}})
    # Snapshots exclude guest conversations and photos to respect demo retention.
    bq=bigquery.Client(project=PROJECT,credentials=session.credentials)
    ds=bigquery.Dataset(f'{PROJECT}.smartnature_backups'); ds.location='us-central1'
    ds.default_table_expiration_ms=30*86400*1000
    bq.create_dataset(ds,exists_ok=True,timeout=30)
    backup_account=f'smartnature-backup@{PROJECT}.iam.gserviceaccount.com'
    accounts=gc('iam','service-accounts','list',f'--project={PROJECT}','--format=value(email)').splitlines()
    if backup_account not in accounts:
        gc('iam','service-accounts','create','smartnature-backup',f'--project={PROJECT}')
    gc('projects','add-iam-policy-binding',PROJECT,f'--member=serviceAccount:{backup_account}',
       '--role=roles/bigquery.jobUser','--condition=None','--format=none')
    for dataset_id,role in [('smart_nature_DB','READER'),('smartnature_backups','WRITER')]:
        dataset=bq.get_dataset(f'{PROJECT}.{dataset_id}',timeout=30)
        entries=list(dataset.access_entries)
        if not any(e.entity_id==backup_account for e in entries):
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
    print('Monitoring, owner notifications, $50 budget and daily snapshots configured.')
    print('A budget alert does not enforce a spending cap.')


if __name__=='__main__': main()
