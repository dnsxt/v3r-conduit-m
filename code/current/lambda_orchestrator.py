import json
import boto3
from lambda_router import route_llm

# lambda_orchestrator.py -- V3R multi-agent orchestration pipeline
# MODERATOR routes to 'orchestration' -- quality tier, task decomposition
# KNOWLEDGE RETRIEVAL routes to 'retrieval' -- sourced constraint-aware information
# CODER routes to 'code' -- production-grade platform-compatible generation, max_tokens=2048
# SYSTEM DEBUG routes to 'reasoning' -- chain-of-thought validation gate, max_tokens=2048
# No direct LLM calls -- all execution via route_llm()
# temperature=0.3 preserved from original for all agents

RETRIEVE_FUNCTION = 'rag-retrieve'
GRAPH_FUNCTION = 'rag-graph'

lambda_client = boto3.client('lambda', region_name='us-east-1')

def moderator(task, rag_context, graph_context):
    system = f'You are the MODERATOR agent of V3R. V3R self-knowledge -- RAG: {rag_context} GRAPH: {graph_context} Decompose this task into RETRIEVAL, OUTPUT, and VALIDATION sections. Be precise and platform-aware.'
    return route_llm('orchestration', system, task, max_tokens=1024, temperature=0.3)

def knowledge_retrieval(retrieval_task, rag_context, graph_context):
    system = 'You are a knowledge retrieval specialist. Your only job is to surface accurate, version-specific, constraint-aware information. No speculation. No generalization. Cite only what you know with confidence. Flag anything uncertain. Platform constraints: AWS Lambda python:3.12, Linux x86_64, free tier only, Groq llama-3.1-8b-instant, DynamoDB PAY_PER_REQUEST.'
    content = f'Retrieval task: {retrieval_task}\n\nRAG context: {rag_context}\n\nGraph context: {graph_context}'
    return route_llm('retrieval', system, content, max_tokens=1024, temperature=0.3)

def coder(output_task, kr_output):
    system = 'You are a production code specialist. Write only platform-compatible, constraint-validated code. Platform: AWS Lambda python:3.12 linux/amd64. Forbidden: SentenceTransformer() direct instantiation, urllib.request for external APIs, Windows paths, win_amd64 wheels, inline JSON in PowerShell, local_files_only=True. Every output must be immediately deployable. No pseudocode. No placeholders.'
    content = f'Task: {output_task}\n\nValidated knowledge: {kr_output}'
    return route_llm('code', system, content, max_tokens=2048, temperature=0.3)

def debug_gate(coder_output, validation_task):
    system = 'You are a mandatory validation gate. Review the provided output against the validation criteria. Check: syntax validity, platform compatibility, forbidden pattern violations, side effects, import availability. Output either CLEARED FOR DELIVERY followed by the output, or CORRECTIONS REQUIRED followed by an itemized list of issues. No output passes without explicit clearance.'
    content = f'Validation criteria: {validation_task}\n\nOutput to validate: {coder_output}'
    return route_llm('reasoning', system, content, max_tokens=2048, temperature=0.3)

def get_rag_context(query):
    try:
        resp = lambda_client.invoke(FunctionName=RETRIEVE_FUNCTION, InvocationType='RequestResponse', Payload=json.dumps({'query': query, 'top_k': 3}))
        body = json.loads(json.loads(resp['Payload'].read())['body'])
        chunks = body.get('results', [])
        return '\n\n'.join([c.get('text', '') for c in chunks])
    except Exception:
        return ''

def get_graph_context(node_id='v3r_platform', depth=2):
    try:
        resp = lambda_client.invoke(FunctionName=GRAPH_FUNCTION, InvocationType='RequestResponse', Payload=json.dumps({'action': 'query', 'node_id': node_id, 'depth': depth}))
        body = json.loads(json.loads(resp['Payload'].read())['body'])
        related = body.get('related', [])
        return 'Related components: ' + ', '.join([n.get('label', '') for n in related]) if related else ''
    except Exception:
        return ''

def lambda_handler(event, context):
    body = json.loads(event.get('body', '{}')) if isinstance(event.get('body'), str) else event
    task = body.get('task', '')
    if not task:
        return {'statusCode': 400, 'body': json.dumps({'error': 'No task provided'})}

    rag_context = get_rag_context(task)
    graph_context = get_graph_context()

    plan = moderator(task, rag_context, graph_context)

    sections = {'RETRIEVAL': '', 'OUTPUT': '', 'VALIDATION': ''}
    current = None
    for line in plan.splitlines():
        for key in sections:
            if line.startswith(key + ':') or line.startswith(key):
                current = key
        if current:
            sections[current] += line + '\n'

    kr_output = knowledge_retrieval(sections['RETRIEVAL'], rag_context, graph_context)
    coder_output = coder(sections['OUTPUT'], kr_output)
    final_output = debug_gate(coder_output, sections['VALIDATION'])

    return {'statusCode': 200, 'body': json.dumps({'plan': plan, 'knowledge': kr_output, 'output': final_output})}
