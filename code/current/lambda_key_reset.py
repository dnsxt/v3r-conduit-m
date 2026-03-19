import boto3
from datetime import date

# lambda_key_reset.py -- V3R NEXUS daily key reset handler
# Invoked by EventBridge Scheduler at midnight UTC daily
# Scans all records in v3r-keys
# Resets requests_today to 0 and status to active for all keys
# Sets last_reset to today's ISO date string
# This is the heartbeat of NEXUS homeostasis
# Runs in under 1 second -- 1024 MB memory, 60 sec timeout sufficient
# No LLM calls -- no lambda_router dependency
# IAM: AmazonDynamoDBFullAccess covers all required operations

dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
keys_table = dynamodb.Table('v3r-keys')

def lambda_handler(event, context):
    today = str(date.today())
    # Scan all provider key records
    response = keys_table.scan()
    items = response.get('Items', [])
    reset_count = 0
    errors = []
    for item in items:
        key_id = item.get('key_id')
        if not key_id:
            continue
        try:
            keys_table.update_item(
                Key={'key_id': key_id},
                UpdateExpression='SET requests_today = :zero, #s = :active, last_reset = :today',
                ExpressionAttributeNames={'#s': 'status'},
                ExpressionAttributeValues={':zero': 0, ':active': 'active', ':today': today}
            )
            reset_count += 1
        except Exception as e:
            errors.append({'key_id': key_id, 'error': str(e)})
    result = {'reset_count': reset_count, 'errors': errors, 'reset_date': today}
    print('V3R key reset complete:', result)
    return {'statusCode': 200, 'body': result}
