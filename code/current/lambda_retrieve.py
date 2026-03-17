import json
import boto3
import torch
import numpy as np
from transformers import AutoTokenizer, AutoModel

MODEL_PATH = "/var/task/models/all-MiniLM-L6-v2"
TABLE_NAME = "rag-chunks"
REGION = "us-east-1"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model = AutoModel.from_pretrained(MODEL_PATH, local_files_only=True)
db = boto3.resource("dynamodb", region_name=REGION)
table = db.Table(TABLE_NAME)

def mean_pooling(model_output, attention_mask):
    token_embeddings = model_output[0]
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(input_mask_expanded.sum(1), min=1e-9)

def embed(text):
    encoded = tokenizer(text, padding=True, truncation=True, return_tensors="pt")
    with torch.no_grad():
        output = model(**encoded)
    return mean_pooling(output, encoded["attention_mask"])[0].tolist()

def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
        query = body.get("query", "")
        top_k = int(body.get("top_k", 3))
        if not query:
            return {"statusCode": 400, "body": json.dumps({"error": "No query provided"})}
        query_embedding = embed(query)
        response = table.scan()
        items = response.get("Items", [])
        scored = []
        for item in items:
                    if "embedding" not in item:
                        continue
                    stored_embedding = json.loads(item["embedding"])
                    score = cosine_similarity(query_embedding, stored_embedding)
                    scored.append({"text": item["text"], "source": item.get("source", ""), "score": score})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return {"statusCode": 200, "body": json.dumps({"results": scored[:top_k]})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}