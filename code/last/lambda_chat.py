import json
import os
import boto3
import decimal
from datetime import datetime, timezone
from lambda_router import route_llm

# lambda_chat.py -- V3R chat handler
# Routes all LLM calls through route_llm('default') -- speed tier
# max_tokens=1024, temperature=0.7 preserved from original
# Maintains session memory in rag-memory DynamoDB table
# Merges RAG context and graph context into system prompt before LLM call
# v3r_ tagged RAG sources trigger V3R systems architect persona

RETRIEVE_FUNCTION = 'rag-retrieve'
GRAPH_FUNCTION = 'rag-graph'
MEMORY_TABLE = 'rag-memory'

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
memory_table = dynamodb.Table(MEMORY_TABLE)
lambda_client = boto3.client('lambda', region_name='us-east-1')

def get_history(session_id):
    try:
        resp = memory_table.query(KeyConditionExpression='session_id = :sid', ExpressionAttributeValues={':sid': session_id}, ScanIndexForward=True)
        items = resp.get('Items', [])
        if items:
            return items[-1].get('turns', [])
        return []
    except Exception:
        return []

def save_history(session_id, turns):
    try:
        memory_table.put_item(Item={'session_id': session_id, 'timestamp': int(datetime.now(timezone.utc).timestamp()), 'turns': turns, 'updated': datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass

def lambda_handler(event, context):
    body = json.loads(event.get('body', '{}')) if isinstance(event.get('body'), str) else event
    query = body.get('query', '')
    session_id = body.get('session_id', 'default')
    use_rag = body.get('use_rag', True)

    history = get_history(session_id)

    context_text = ''
    rag_used = False
    v3r_sources = []
    if use_rag and query:
        try:
            retrieve_resp = lambda_client.invoke(FunctionName=RETRIEVE_FUNCTION, InvocationType='RequestResponse', Payload=json.dumps({'query': query, 'top_k': 3}))
            retrieve_body = json.loads(retrieve_resp['Payload'].read())
            if isinstance(retrieve_body.get('body'), str):
                retrieve_body = json.loads(retrieve_body['body'])
            chunks = retrieve_body.get('results', [])
            v3r_sources = [c.get('source', '') for c in chunks if str(c.get('source', '')).startswith('v3r_')]
            if chunks:
                context_text = '\n\n'.join([c.get('text', '') for c in chunks])
                rag_used = True
        except Exception:
            pass
        graph_context = ''
        try:
            graph_resp = lambda_client.invoke(FunctionName=GRAPH_FUNCTION, InvocationType='RequestResponse', Payload=json.dumps({'action': 'query', 'node_id': 'v3r_platform', 'depth': 2}))
            graph_body = json.loads(json.loads(graph_resp['Payload'].read())['body'])
            related = graph_body.get('related', [])
            if related:
                graph_context = 'Related components: ' + ', '.join([n.get('label', '') for n in related])
        except Exception:
            pass
        if graph_context:
            context_text = context_text + '\n\n' + graph_context if context_text else graph_context

    history_text = ''
    for turn in history[-6:]:
        history_text += 'User: ' + turn.get('user', '') + '\nAssistant: ' + turn.get('assistant', '') + '\n\n'

    system_prompt = 'You are V3R, a Cloud-Native GNN-integrated Multi-Agent Multi-LLM Industrial Science and Intelligence Engineering Platform. You have deep self-knowledge of your own architecture, agents, phases, forbidden patterns, and engineering decisions. Reason as a V3R systems architect. Be precise, technical, and reference specific V3R components when relevant.' if v3r_sources else 'You are a helpful assistant with access to a knowledge base. Answer questions accurately based on the provided context.'
    if context_text:
        system_prompt += '\n\nRelevant context from knowledge base:\n' + context_text

    full_user = ('Previous conversation:\n' + history_text + '\n\n' if history_text else '') + query
    answer = route_llm('default', system_prompt, full_user, max_tokens=1024, temperature=0.7)

    turns = list(history)
    turns.append({'user': query, 'assistant': answer, 'timestamp': datetime.now(timezone.utc).isoformat()})
    save_history(session_id, turns)

    return {'statusCode': 200, 'body': json.dumps({'answer': answer, 'rag_used': rag_used, 'memory_turns': len(turns)})}
