"""
RAG Memory System - HuggingFace Space
Compatible with Gradio 6.x
"""

import gradio as gr
import requests
import os

CHAT_URL   = os.environ.get("CHAT_LAMBDA_URL", "")
INGEST_URL = os.environ.get("INGEST_LAMBDA_URL", "")


def ingest_url(url):
    if not INGEST_URL:
        return "ERROR: INGEST_LAMBDA_URL not set in Space secrets."
    if not url or not url.strip():
        return "Please enter a URL."
    try:
        resp = requests.post(INGEST_URL, json={
            "type":    "url",
            "content": url.strip(),
            "source":  url.strip(),
        }, timeout=60)
        data = resp.json()
        if resp.status_code == 200:
            return f"✅ Ingested {data.get('chunks_stored', '?')} chunks from {url}"
        else:
            return f"❌ Error: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"❌ Exception: {str(e)}"


def ingest_text(title, text):
    if not INGEST_URL:
        return "ERROR: INGEST_LAMBDA_URL not set in Space secrets."
    if not text or not text.strip():
        return "Please enter some text."
    try:
        resp = requests.post(INGEST_URL, json={
            "type":    "text",
            "content": text.strip(),
            "source":  title.strip() if title else "manual-input",
        }, timeout=60)
        data = resp.json()
        if resp.status_code == 200:
            return f"✅ Ingested {data.get('chunks_stored', '?')} chunks from '{title}'"
        else:
            return f"❌ Error: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"❌ Exception: {str(e)}"


def ingest_file(file):
    if not INGEST_URL:
        return "ERROR: INGEST_LAMBDA_URL not set in Space secrets."
    if file is None:
        return "No file uploaded."
    try:
        import base64
        with open(file, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        fname = os.path.basename(file)
        resp = requests.post(INGEST_URL, json={
            "type":    "pdf_b64",
            "content": b64,
            "source":  fname,
        }, timeout=60)
        data = resp.json()
        if resp.status_code == 200:
            return f"✅ Ingested {data.get('chunks_stored', '?')} chunks from '{fname}'"
        else:
            return f"❌ Error: {data.get('error', 'Unknown error')}"
    except Exception as e:
        return f"❌ Exception: {str(e)}"


def chat(message, history, session_id):
    if not CHAT_URL:
        history.append({"role": "assistant", "content": "ERROR: CHAT_LAMBDA_URL not set in Space secrets."})
        return history, ""
    if not message or not message.strip():
        return history, ""
    try:
        resp = requests.post(CHAT_URL, json={
            "message":    message.strip(),
            "session_id": session_id.strip() if session_id else "default",
            "use_rag":    True,
        }, timeout=30)
        data = resp.json()
        reply = data.get("response", data.get("error", "No response"))
        mem   = data.get("memory_turns", 0)
        rag   = "📚" if data.get("rag_used") else "💬"
        reply = f"{rag} {reply}\n\n_Memory: {mem} turns stored_"
    except Exception as e:
        reply = f"❌ Error: {str(e)}"

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})
    return history, ""


# ── UI ────────────────────────────────────────────────────────────────────────

with gr.Blocks(title="RAG Memory System") as demo:
    gr.Markdown("# 🧠 RAG Memory System\nPersonalized AI with persistent memory and knowledge base.")

    with gr.Tab("💬 Chat"):
        session_input = gr.Textbox(
            label="Session ID",
            value="default",
            placeholder="Name this conversation"
        )
        chatbot = gr.Chatbot(
            label="Conversation",
            height=400,
            type="messages"
        )
        msg_input = gr.Textbox(
            label="Your message",
            placeholder="Ask anything...",
            lines=2
        )
        with gr.Row():
            send_btn  = gr.Button("Send", variant="primary")
            clear_btn = gr.Button("Clear")

        send_btn.click(
            fn=chat,
            inputs=[msg_input, chatbot, session_input],
            outputs=[chatbot, msg_input]
        )
        msg_input.submit(
            fn=chat,
            inputs=[msg_input, chatbot, session_input],
            outputs=[chatbot, msg_input]
        )
        clear_btn.click(fn=lambda: ([], ""), outputs=[chatbot, msg_input])

    with gr.Tab("📥 Add to Knowledge Base"):
        status_box = gr.Textbox(label="Status", interactive=False)

        gr.Markdown("### Add URL")
        url_input = gr.Textbox(
            label="URL",
            placeholder="https://example.com/article"
        )
        url_btn = gr.Button("Ingest URL", variant="primary")
        url_btn.click(
            fn=ingest_url,
            inputs=[url_input],
            outputs=[status_box]
        )

        gr.Markdown("### Add Text / Notes")
        text_title = gr.Textbox(label="Title")
        text_input = gr.Textbox(label="Text content", lines=6)
        text_btn   = gr.Button("Ingest Text", variant="primary")
        text_btn.click(
            fn=ingest_text,
            inputs=[text_title, text_input],
            outputs=[status_box]
        )

        gr.Markdown("### Upload File")
        file_input = gr.File(
            label="Upload PDF or text file",
            file_types=[".pdf", ".txt", ".md"]
        )
        file_btn = gr.Button("Ingest File", variant="primary")
        file_btn.click(
            fn=ingest_file,
            inputs=[file_input],
            outputs=[status_box]
        )

    gr.Markdown("_Powered by AWS Lambda + DynamoDB + Groq + HuggingFace_")

demo.launch()
