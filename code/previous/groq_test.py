import http.client, json
conn = http.client.HTTPSConnection('api.groq.com')
payload = json.dumps({'model': 'llama-3.1-8b-instant', 'messages': [{'role': 'user', 'content': 'What is V3R?'}], 'max_tokens': 1024, 'temperature': 0.7})
conn.request('POST', '/openai/v1/chat/completions', body=payload.encode('utf-8'), headers={'Authorization': 'Bearer (See sensitive_data.txt)', 'Content-Type': 'application/json', 'Content-Length': str(len(payload.encode('utf-8')))})
resp = conn.getresponse()
print('STATUS:', resp.status)
print('BODY:', resp.read().decode())