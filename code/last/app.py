"""
HuggingFace Space - RAG Chat Interface
Deploy this as app.py in a new HF Space (Gradio SDK)
Set these secrets in Space Settings:
  CHAT_LAMBDA_URL   = your Lambda Function URL for rag-chat
  INGEST_LAMBDA_URL = your Lambda Function URL for rag-ingest
  IDEAGEN_LAMBDA_URL = your Lambda Function URL for rag-ideagen
"""

import gradio as gr
import requests
import os
import json

CHAT_URL    = os.environ.get("CHAT_LAMBDA_URL", "")
INGEST_URL  = os.environ.get("INGEST_LAMBDA_URL", "")
IDEAGEN_URL = os.environ.get("IDEAGEN_LAMBDA_URL", "")

def chat(message, history, session_id):
    if not CHAT_URL:
        return history + [{"role": "user", "content": message}, {"role": "assistant", "content": "ERROR: CHAT_LAMBDA_URL not set."}]
    try:
        resp = requests.post(CHAT_URL, json={
            "query":      message,
            "session_id": session_id or "default",
            "use_rag":    True,
        }, timeout=30)
        data  = resp.json()
        reply = data.get("answer", data.get("response", data.get("error", "No response")))
        mem   = data.get("memory_turns", 0)
        rag   = "🟢" if data.get("rag_used") else "🔵"
        reply = f"{rag} {reply}\n\n_[Memory: {mem} turns]_"
    except Exception as e:
        reply = f"Error: {str(e)}"
    return history + [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]

def ingest_url(url, status_box):
    if not INGEST_URL:
        return "ERROR: INGEST_LAMBDA_URL not set."
    try:
        resp = requests.post(INGEST_URL, json={
            "text":   url,
            "source": url,
        }, timeout=45)
        data = resp.json()
        if resp.status_code == 200:
            return f"✅ Ingested {data.get('chunks_stored', '?')} chunks from {url}"
        else:
            return f"❌ Error: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"❌ Exception: {str(e)}"

def ingest_text(title, text, status_box):
    if not INGEST_URL:
        return "ERROR: INGEST_LAMBDA_URL not set."
    try:
        resp = requests.post(INGEST_URL, json={
            "text":   text,
            "source": title or "manual-input",
        }, timeout=60)
        data = resp.json()
        if resp.status_code == 200:
            return f"✅ Ingested {data.get('chunks_stored', '?')} chunks from '{title}'"
        else:
            return f"❌ Error: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"❌ Exception: {str(e)}"

def ingest_file(file, status_box):
    if not INGEST_URL:
        return "ERROR: INGEST_LAMBDA_URL not set."
    if file is None:
        return "No file uploaded."
    try:
        import base64, io
        filepath = file.name if hasattr(file, "name") else file
        with open(filepath, "rb") as f:
            raw = f.read()
        fname = os.path.basename(filepath)
        if fname.lower().endswith(".pdf"):
            pdf_b64 = base64.b64encode(raw).decode("utf-8")
            resp = requests.post(INGEST_URL, json={"pdf_base64": pdf_b64, "source": fname}, timeout=120)
        else:
            text = raw.decode("utf-8", errors="ignore")
            resp = requests.post(INGEST_URL, json={"text": text, "source": fname}, timeout=60)
        data = resp.json()
        if resp.status_code == 200:
            return f"✅ Ingested {data.get('chunks_stored', '?')} chunks from '{fname}'"
        else:
            return f"❌ Error: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"❌ Exception: {str(e)}"

def generate_spec(idea, status_box):
    if not IDEAGEN_URL:
        return "ERROR: IDEAGEN_LAMBDA_URL not set.", ""
    if not idea:
        return "ERROR: No idea provided.", ""
    try:
        resp = requests.post(IDEAGEN_URL, json={"idea": idea}, timeout=120)
        data = resp.json()
        if resp.status_code == 200:
            spec = data.get("spec", {})
            spec_id = data.get("spec_id", "unknown")
            output = f"SPEC ID: {spec_id}\n\n"
            output += f"=== ANALYSIS ===\n{spec.get('pass1_analysis', '')}\n\n"
            output += f"=== ARCHITECTURE ===\n{spec.get('pass2_architecture', '')}\n\n"
            output += f"=== STACK ===\n{spec.get('pass3_stack', '')}\n\n"
            output += f"=== DEPLOYMENT ===\n{spec.get('pass4_deployment', '')}\n\n"
            output += f"=== RISKS ===\n{spec.get('pass5_risks', '')}\n\n"
            output += f"=== VALIDATION ===\n{spec.get('validation_status', '')}"
            return f"✅ Spec generated. ID: {spec_id}", output
        else:
            return f"❌ Error: {data.get('error', 'Unknown error')}", ""
    except Exception as e:
        return f"❌ Exception: {str(e)}", ""

# --- UI ---

with gr.Blocks(title="RAG Memory System") as demo:
    gr.Markdown("# 🧠 RAG Memory System\nPersonalized AI with persistent memory and knowledge base.")

    with gr.Tab("💬 Chat"):
        session_id = gr.Textbox(label="Session ID (name this conversation)", value="default", scale=1)
        chatbot    = gr.Chatbot(height=500, label="Conversation")
        msg_input  = gr.Textbox(label="Your message", placeholder="Ask anything...", lines=2)
        with gr.Row():
            send_btn  = gr.Button("Send", variant="primary")
            clear_btn = gr.Button("Clear Chat")

        send_btn.click(chat, inputs=[msg_input, chatbot, session_id], outputs=chatbot)
        msg_input.submit(chat, inputs=[msg_input, chatbot, session_id], outputs=chatbot)
        clear_btn.click(lambda: [], outputs=chatbot)

    with gr.Tab("📥 Add to Knowledge Base"):
        status = gr.Textbox(label="Status", interactive=False)

        with gr.Accordion("Add URL", open=True):
            url_input = gr.Textbox(label="URL", placeholder="https://example.com/article")
            url_btn   = gr.Button("Ingest URL", variant="primary")
            url_btn.click(ingest_url, inputs=[url_input, status], outputs=status)

        with gr.Accordion("Add Text / Notes", open=False):
            text_title = gr.Textbox(label="Title / Source name")
            text_input = gr.Textbox(label="Text content", lines=8)
            text_btn   = gr.Button("Ingest Text", variant="primary")
            text_btn.click(ingest_text, inputs=[text_title, text_input, status], outputs=status)

        with gr.Accordion("Upload PDF or Document", open=False):
            file_input = gr.File(label="Upload file", file_types=[".pdf", ".txt", ".md"])
            file_btn   = gr.Button("Ingest File", variant="primary")
            file_btn.click(ingest_file, inputs=[file_input, status], outputs=status)

    with gr.Tab("⚙️ Product Generator"):
        gen_status = gr.Textbox(label="Status", interactive=False)
        idea_input = gr.Textbox(label="Describe your product idea", placeholder="e.g. Build a system that monitors social media for brand mentions and generates daily sentiment reports", lines=4)
        gen_btn    = gr.Button("Generate Specification", variant="primary")
        spec_output = gr.Textbox(label="Generated Specification", lines=30, interactive=False)
        gen_btn.click(generate_spec, inputs=[idea_input, gen_status], outputs=[gen_status, spec_output])

    gr.Markdown("_Powered by AWS Lambda + DynamoDB + Groq + HuggingFace_")

demo.launch(theme=gr.themes.Soft())