import json
import boto3
import http.client
from datetime import date

# lambda_router.py -- V3R NEXUS Routing Utility
# Shared module -- imported by all V3R Lambda handlers
# Exposes single function: route_llm(route_id, system_prompt, user_prompt, max_tokens, temperature)
# Resolves route_id to provider via v3r-routing table
# Selects best available key from v3r-keys table using failover strategy
# path field in v3r-keys record specifies provider endpoint path
# Groq requires /openai/v1/chat/completions -- all others use /v1/chat/completions
# FP-002: http.client.HTTPSConnection used exclusively -- urllib.request prohibited

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
routing_table = dynamodb.Table('v3r-routing')
keys_table = dynamodb.Table('v3r-keys')

def get_route(route_id):
    response = routing_table.get_item(Key={'route_id': route_id})
    return response.get('Item')

def get_key(provider):
    response = keys_table.get_item(Key={'key_id': provider})
    return response.get('Item')

def reset_if_new_day(provider, key_record, today):
    if key_record.get('last_reset') != today:
        keys_table.update_item(
            Key={'key_id': provider},
            UpdateExpression='SET requests_today = :zero, last_reset = :today',
            ExpressionAttributeValues={':zero': 0, ':today': today}
        )
        key_record['requests_today'] = 0
        key_record['last_reset'] = today
    return key_record

def increment_usage(provider):
    keys_table.update_item(
        Key={'key_id': provider},
        UpdateExpression='SET requests_today = requests_today + :inc',
        ExpressionAttributeValues={':inc': 1}
    )

def call_openai_compat(key_record, system_prompt, user_prompt, max_tokens, temperature):
    # path field in v3r-keys specifies provider-specific endpoint
    # Groq: /openai/v1/chat/completions
    # Cerebras/Mistral/Fireworks: /v1/chat/completions or provider-specific path
    path = key_record.get('path', '/v1/chat/completions')
    conn = http.client.HTTPSConnection(key_record['base_url'])
    payload = json.dumps({'model': key_record['model'], 'messages': [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}], 'max_tokens': max_tokens, 'temperature': temperature})
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key_record['api_key'], 'Content-Length': str(len(payload.encode('utf-8')))}
    conn.request('POST', path, payload, headers)
    res = conn.getresponse()
    data = json.loads(res.read().decode('utf-8'))
    return data['choices'][0]['message']['content']

def route_llm(route_id, system_prompt, user_prompt, max_tokens=1024, temperature=0.3):
    route = get_route(route_id)
    if not route:
        route = get_route('default')
    if not route:
        return 'V3R routing error: default route not found.'
    provider_order = [route.get('primary'), route.get('fallback_1'), route.get('fallback_2'), route.get('fallback_3')]
    today = str(date.today())
    for provider in provider_order:
        if not provider:
            continue
        key_record = get_key(provider)
        if not key_record:
            continue
        if key_record.get('status') != 'active':
            continue
        key_record = reset_if_new_day(provider, key_record, today)
        daily_limit = int(key_record.get('daily_limit', 9999))
        requests_today = int(key_record.get('requests_today', 0))
        if requests_today >= daily_limit:
            keys_table.update_item(
                Key={'key_id': provider},
                UpdateExpression='SET #s = :exhausted',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':exhausted': 'exhausted'}
            )
            continue
        try:
            result = call_openai_compat(key_record, system_prompt, user_prompt, max_tokens, temperature)
            increment_usage(provider)
            return result
        except Exception:
            continue
    return 'V3R routing error: all providers in chain exhausted or unavailable.'
