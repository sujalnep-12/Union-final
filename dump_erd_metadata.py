import json, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','community_hub.settings')
import django; django.setup()
from django.apps import apps
from django.db.models import OneToOneField

AI_MODELS={'AIAgent'}
AI_FIELDS={'ai_summary','ai_category','ai_priority'}
out={'models':[], 'relations':[]}
auth=next(m for m in apps.get_models() if m.__name__=='User' and m._meta.app_label=='auth')
def pack(m):
    fields=[]; rels=[]
    for f in m._meta.fields:
        if f.name in AI_FIELDS: continue
        target=getattr(getattr(f,'remote_field',None),'model',None)
        fields.append({'name':f.name,'type':f.__class__.__name__,'target':target.__name__ if target else None})
        if target and target.__name__ not in AI_MODELS:
            rels.append({'source':m.__name__,'field':f.name,'target':target.__name__,'cardinality':'1:1' if isinstance(f,OneToOneField) else 'N:1'})
    for f in m._meta.many_to_many:
        target=f.remote_field.model
        if target.__name__ in AI_MODELS: continue
        rels.append({'source':m.__name__,'field':f.name,'target':target.__name__,'cardinality':'N:M'})
    return {'name':m.__name__,'fields':fields,'relations':rels}
out['models'].append(pack(auth))
for m in apps.get_app_config('hub').get_models():
    if m.__name__ not in AI_MODELS: out['models'].append(pack(m)); out['relations'].extend(pack(m)['relations'])
with open('erd_metadata.json','w',encoding='utf-8') as f: json.dump(out,f,indent=2)
print(len(out['models']),sum(len(m['fields']) for m in out['models']),len(out['relations']))
