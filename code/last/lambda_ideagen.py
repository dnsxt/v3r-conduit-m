import json
import os
import uuid
import boto3
from datetime import datetime, timezone
from lambda_router import route_llm

# lambda_ideagen.py -- V3R product specification generator
# 5-pass pipeline: Analysis, Architecture, Stack, Deployment, Risks
# All passes route to 'default' tier -- high-volume sequential speed calls
# max_tokens=1024, temperature=0.3 preserved from original
# Results stored in rag-specs DynamoDB table

RETRIEVE_FUNCTION = 'rag-retrieve'
GRAPH_FUNCTION = 'rag-graph'
SPECS_TABLE = 'rag-specs'
REGION = 'us-east-1'

dynamodb = boto3.resource('dynamodb', region_name=REGION)
specs_table = dynamodb.Table(SPECS_TABLE)
lambda_client = boto3.client('lambda', region_name=REGION)

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

def run_pipeline(raw_idea):
    v3r_context = get_rag_context('V3R architecture agents constraints forbidden patterns')
    graph_context = get_graph_context()

    pass1 = route_llm('default', 'You are V3R, a Cloud-Native GNN-integrated Multi-Agent Multi-LLM Industrial Science and Intelligence Engineering Platform. Analyze this product idea. Output plain text with labeled sections: INTENT, DOMAIN, CONSTRAINTS, TITLE, SUMMARY.', f'IDEA: {raw_idea}\n\nV3R CONTEXT: {v3r_context}\n\nGRAPH: {graph_context}', max_tokens=1024, temperature=0.3)
    pass2 = route_llm('default', 'You are V3R. Design the full system architecture for this product. Output plain text with labeled sections: LAYERS, DATA_FLOW, COMPONENTS.', f'IDEA: {raw_idea}\n\nANALYSIS: {pass1}', max_tokens=1024, temperature=0.3)
    pass3 = route_llm('default', 'You are V3R. Select all technologies and define API contracts for each component. Output plain text with labeled sections: COMPUTE, STORAGE, APIS, FRAMEWORKS, API_CONTRACTS.', f'IDEA: {raw_idea}\n\nARCHITECTURE: {pass2}', max_tokens=1024, temperature=0.3)
    pass4 = route_llm('default', 'You are V3R. Write complete deployment steps, estimate time, assess free-tier compatibility, and provide cost breakdown. Output plain text with labeled sections: DEPLOYMENT_STEPS, ESTIMATED_TIME, FREE_TIER_COMPATIBLE, COST_BREAKDOWN.', f'IDEA: {raw_idea}\n\nSTACK: {pass3}', max_tokens=1024, temperature=0.3)
    pass5 = route_llm('default', 'You are V3R. Identify all risks, severity levels, and mitigations for every component and dependency. Output plain text with labeled sections: RISKS, SEVERITY, MITIGATIONS.', f'IDEA: {raw_idea}\n\nFULL_SPEC: {pass1}\n\n{pass2}\n\n{pass3}\n\n{pass4}', max_tokens=1024, temperature=0.3)

    return pass1, pass2, pass3, pass4, pass5

def lambda_handler(event, context):
    try:
        body = json.loads(event.get('body', '{}')) if isinstance(event.get('body'), str) else event
        raw_idea = body.get('idea', '')
        if not raw_idea:
            return {'statusCode': 400, 'body': json.dumps({'error': 'No idea provided'})}

        passes = run_pipeline(raw_idea)

        spec = {'spec_id': str(uuid.uuid4()), 'generated_at': datetime.now(timezone.utc).isoformat(), 'raw_idea': raw_idea, 'pass1_analysis': passes[0], 'pass2_architecture': passes[1], 'pass3_stack': passes[2], 'pass4_deployment': passes[3], 'pass5_risks': passes[4], 'validation_status': 'CLEARED'}
        specs_table.put_item(Item=spec)

        return {'statusCode': 200, 'body': json.dumps({'spec_id': spec['spec_id'], 'spec': spec})}
    except Exception as e:
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}
