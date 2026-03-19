import json
import boto3
from lambda_router import route_llm

# lambda_orchestrator.py -- V3R multi-agent orchestration pipeline
# V3.2 -- SQS coordination layer added (MT-02)
# MODERATOR routes to 'orchestration' tier -- quality reasoning
# KNOWLEDGE RETRIEVAL routes to 'retrieval' tier
# CODER routes to 'code' tier -- max_tokens=2048
# SYSTEM DEBUG routes to 'reasoning' tier -- max_tokens=2048
# SQS FIFO queues used for agent coordination and parallel execution
# Falls back to direct execution if SQS poll times out
# temperature=0.3 preserved on all agents

REGION = 'us-east-1'
RETRIEVE_FUNCTION = 'rag-retrieve'
GRAPH_FUNCTION = 'rag-graph'

# SQS queue URLs -- derived from confirmed ARNs
QUEUE_BASE = 'https://sqs.us-east-1.amazonaws.com/236510207245'
Q_MODERATOR = QUEUE_BASE + '/v3r-moderator.fifo'
Q_KR = QUEUE_BASE + '/v3r-knowledge-retrieval.fifo'
Q_CODER = QUEUE_BASE + '/v3r-coder.fifo'
Q_DEBUG = QUEUE_BASE + '/v3r-debug.fifo'
Q_DOCUMENTATION = QUEUE_BASE + '/v3r-documentation.fifo'
Q_INFRASTRUCTURE = QUEUE_BASE + '/v3r-infrastructure.fifo'

lambda_client = boto3.client('lambda', region_name=REGION)
sqs_client = boto3.client('sqs', region_name=REGION)

def sqs_send(queue_url, payload, group_id='v3r-pipeline'):
    # Send message to SQS FIFO queue
    # ContentBasedDeduplication enabled on queues -- no dedup ID needed
    sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(payload),
        MessageGroupId=group_id
    )

def sqs_poll(queue_url, wait_seconds=20):
    # Long-poll SQS queue for response -- returns message body or None
    # wait_seconds=20 is max long-poll -- reduces empty receives
    resp = sqs_client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=wait_seconds,
        VisibilityTimeout=300
    )
    messages = resp.get('Messages', [])
    if not messages:
        return None
    msg = messages[0]
    # Delete message after receipt -- processed exactly once
    sqs_client.delete_message(
        QueueUrl=queue_url,
        ReceiptHandle=msg['ReceiptHandle']
    )
    return json.loads(msg['Body'])

def moderator(task, rag_context, graph_context):
    system = 'You are the MODERATOR agent of V3R. V3R self-knowledge -- RAG: ' + rag_context + ' GRAPH: ' + graph_context + ' Decompose this task into RETRIEVAL, OUTPUT, and VALIDATION sections. Be precise and platform-aware.'
    return route_llm('orchestration', system, task, max_tokens=1024, temperature=0.3)

def knowledge_retrieval(retrieval_task, rag_context, graph_context):
    system = 'You are a knowledge retrieval specialist. Your only job is to surface accurate, version-specific, constraint-aware information. No speculation. No generalization. Cite only what you know with confidence. Flag anything uncertain. Platform constraints: AWS Lambda python:3.12, Linux x86_64, free tier only, DynamoDB PAY_PER_REQUEST.'
    content = 'Retrieval task: ' + retrieval_task + '\n\nRAG context: ' + rag_context + '\n\nGraph context: ' + graph_context
    return route_llm('retrieval', system, content, max_tokens=1024, temperature=0.3)

def coder(output_task, kr_output):
    system = 'You are a production code specialist. Write only platform-compatible, constraint-validated code. Platform: AWS Lambda python:3.12 linux/amd64. Forbidden: SentenceTransformer() direct instantiation, urllib.request for external APIs, Windows paths, win_amd64 wheels, inline JSON in PowerShell, local_files_only=True. Every output must be immediately deployable. No pseudocode. No placeholders.'
    content = 'Task: ' + output_task + '\n\nValidated knowledge: ' + kr_output
    return route_llm('code', system, content, max_tokens=2048, temperature=0.3)

def debug_gate(coder_output, validation_task):
    system = 'You are a mandatory validation gate. Review the provided output against the validation criteria. Check: syntax validity, platform compatibility, forbidden pattern violations, side effects, import availability. Output either CLEARED FOR DELIVERY followed by the output, or CORRECTIONS REQUIRED followed by an itemized list of issues. No output passes without explicit clearance.'
    content = 'Validation criteria: ' + validation_task + '\n\nOutput to validate: ' + coder_output
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

def run_pipeline_sqs(task, rag_context, graph_context, plan, sections):
    # SQS-coordinated pipeline -- publishes tasks to agent queues
    # Polls for results with 20-second long-poll
    # Falls back to direct execution on poll timeout

    # Publish KR task to queue
    sqs_send(Q_KR, {'task': sections['RETRIEVAL'], 'rag_context': rag_context, 'graph_context': graph_context})

    # Poll KR response queue (moderator queue receives agent results)
    kr_result = sqs_poll(Q_MODERATOR, wait_seconds=20)
    if kr_result:
        kr_output = kr_result.get('result', '')
    else:
        # Fallback to direct execution if SQS times out
        kr_output = knowledge_retrieval(sections['RETRIEVAL'], rag_context, graph_context)

    # Publish CODER task
    sqs_send(Q_CODER, {'task': sections['OUTPUT'], 'kr_output': kr_output})

    # Poll CODER response
    coder_result = sqs_poll(Q_MODERATOR, wait_seconds=20)
    if coder_result:
        coder_output = coder_result.get('result', '')
    else:
        coder_output = coder(sections['OUTPUT'], kr_output)

    # Publish DEBUG task
    sqs_send(Q_DEBUG, {'task': sections['VALIDATION'], 'coder_output': coder_output})

    # Poll DEBUG response
    debug_result = sqs_poll(Q_MODERATOR, wait_seconds=20)
    if debug_result:
        final_output = debug_result.get('result', '')
    else:
        final_output = debug_gate(coder_output, sections['VALIDATION'])

    return kr_output, coder_output, final_output

def lambda_handler(event, context):
    # Check if invoked by SQS trigger -- agent execution mode
    if 'Records' in event:
        # SQS-triggered agent execution
        # Each record is a task dispatched by MODERATOR
        results = []
        for record in event['Records']:
            body = json.loads(record['body'])
            agent = body.get('agent', 'kr')
            task = body.get('task', '')
            rag_context = body.get('rag_context', '')
            graph_context = body.get('graph_context', '')
            kr_output = body.get('kr_output', '')
            coder_output = body.get('coder_output', '')
            validation_task = body.get('task', '')
            if agent == 'kr':
                result = knowledge_retrieval(task, rag_context, graph_context)
            elif agent == 'coder':
                result = coder(task, kr_output)
            elif agent == 'debug':
                result = debug_gate(coder_output, validation_task)
            else:
                result = knowledge_retrieval(task, rag_context, graph_context)
            # Publish result back to moderator queue
            sqs_send(Q_MODERATOR, {'agent': agent, 'result': result})
            results.append({'agent': agent, 'status': 'complete'})
        return {'statusCode': 200, 'body': json.dumps({'processed': results})}

    # Direct HTTP invocation mode -- standard pipeline
    body = json.loads(event.get('body', '{}')) if isinstance(event.get('body'), str) else event
    task = body.get('task', '')
    use_sqs = body.get('use_sqs', True)
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

    if use_sqs:
        kr_output, coder_output, final_output = run_pipeline_sqs(task, rag_context, graph_context, plan, sections)
    else:
        kr_output = knowledge_retrieval(sections['RETRIEVAL'], rag_context, graph_context)
        coder_output = coder(sections['OUTPUT'], kr_output)
        final_output = debug_gate(coder_output, sections['VALIDATION'])

    return {'statusCode': 200, 'body': json.dumps({'plan': plan, 'knowledge': kr_output, 'output': final_output})}
