# lambda_ingest_v4.py
# V3R RAG Ingest Handler — Phase 6: Multi-modal (Text + PDF)
# Uses AutoTokenizer + AutoModel — NEVER SentenceTransformer()
# Model loaded offline from /var/task/models/all-MiniLM-L6-v2
# PDF support via pypdf — pure Python, no Tesseract binary required

import json
import os
import uuid
import base64
import io
import boto3
import torch
import numpy as np
from transformers import AutoTokenizer, AutoModel
from datetime import datetime, timezone
from pypdf import PdfReader

MODEL_PATH = "/var/task/models/all-MiniLM-L6-v2"
TABLE_NAME = "rag-chunks"
REGION = "us-east-1"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model = AutoModel.from_pretrained(MODEL_PATH, local_files_only=True)
model.eval()
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
    embedding = mean_pooling(output, encoded["attention_mask"])
    embedding = torch.nn.functional.normalize(embedding, p=2, dim=1)
    return embedding[0].tolist()

def chunk_text(text, size=500, overlap=50):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + size])
        if chunk.strip():
            chunks.append(chunk)
        i += size - overlap
    return chunks

def extract_pdf_text(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text and text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)

def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body", "{}")) if isinstance(event.get("body"), str) else event
        source = body.get("source", "manual")

        if "pdf_base64" in body:
            pdf_bytes = base64.b64decode(body["pdf_base64"])
            text = extract_pdf_text(pdf_bytes)
            if not text.strip():
                return {"statusCode": 400, "body": json.dumps({"error": "PDF contained no extractable text"})}

        elif "text" in body:
            text = body["text"]

        else:
            return {"statusCode": 400, "body": json.dumps({"error": "No text or pdf_base64 field provided"})}

        if not text.strip():
            return {"statusCode": 400, "body": json.dumps({"error": "Empty content after extraction"})}

        chunks = chunk_text(text)
        stored = 0
        for chunk in chunks:
            embedding = embed(chunk)
            table.put_item(Item={
                "chunk_id": str(uuid.uuid4()),
                "source": source,
                "text": chunk,
                "embedding": json.dumps(embedding),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            stored += 1

        return {"statusCode": 200, "body": json.dumps({"message": "Ingested", "chunks_stored": stored, "source": source})}

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}