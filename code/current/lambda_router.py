import json
import boto3
import http.client
from datetime import date

# lambda_router.py -- V3R NEXUS Routing Utility
# Shared module -- imported by all V3R Lambda handlers
# Exposes single function: route_llm(route_id, system_prompt, user_prompt, max_tokens, temperature)
# Resolves route_id to provider via v3r-routing table
# Selects best available key from v3r-keys table using failover strategy
# Handler receives normalized text response -- never knows which provider executed
# All providers currently openai_compat -- gemini adapter reserved for future extension
# FP-002: http.client.HTTPSConnection used exclusively -- urllib.request prohibited

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
routing_table = dynamodb.Table('v3r-routing')
keys_table = dynamodb.Table('v3r-keys')

def get_route(route_id):
    # Fetch routing record by route_id -- fallback to default if not found
    response = routing_table.get_item(Key={'route_id': route_id})
    return response.get('Item')

def get_key(provider):
    # Fetch provider key record from v3r-keys by provider name
    response = keys_table.get_item(Key={'key_id': provider})
    return response.get('Item')

def reset_if_new_day(provider, key_record, today):
    # Reset daily counter if last_reset is not today
    # Lightweight daily reset -- EventBridge midnight reset is primary mechanism
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
    # Increment request counter after successful call
    keys_table.update_item(
        Key={'key_id': provider},
        UpdateExpression='SET requests_today = requests_today + :inc',
        ExpressionAttributeValues={':inc': 1}
    )

def call_openai_compat(key_record, system_prompt, user_prompt, max_tokens, temperature):
    # OpenAI-compatible adapter -- handles Groq, Cerebras, Mistral, Fireworks
    # base_url stored without https:// prefix -- HTTPSConnection requires host only
    conn = http.client.HTTPSConnection(key_record['base_url'])
    payload = json.dumps({
        'model': key_record['model'],
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt}
        ],
        'max_tokens': max_tokens,
        'temperature': temperature
    })
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + key_record['api_key'],
        'Content-Length': str(len(payload.encode('utf-8')))
    }
    conn.request('POST', '/v1/chat/completions', payload, headers)
    res = conn.getresponse()
    data = json.loads(res.read().decode('utf-8'))
    return data['choices'][0]['message']['content']

def route_llm(route_id, system_prompt, user_prompt, max_tokens=1024, temperature=0.3):
    # Main entry point for all V3R handlers
    # 1. Resolve route_id to provider order via v3r-routing
    # 2. Iterate provider order -- skip exhausted, paused, or unavailable keys
    # 3. Execute call on first available provider
    # 4. Return normalized text -- caller never knows which provider executed
    route = get_route(route_id)
    if not route:
        route = get_route('default')
    if not route:
        return 'V3R routing error: default route not found.'

    provider_order = [
        route.get('primary'),
        route.get('fallback_1'),
        route.get('fallback_2'),
        route.get('fallback_3')
    ]

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
