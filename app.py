import os
import time
from contextlib import asynccontextmanager
from threading import Thread

import torch
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

MODEL_ID = os.getenv("MODEL_ID", "HuggingFaceTB/SmolLM2-135M-Instruct")
MODEL_VERSION = os.getenv("MODEL_VERSION", "local")
MAX_NEW_TOKENS_CAP = 128

model: AutoModelForCausalLM | None = None
tokenizer: AutoTokenizer | None = None


def load_model() -> None:
    global model, tokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, torch_dtype=torch.float32)
    model.eval()


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_model()
    yield


app = FastAPI(title="Tiny LLM inference", version=MODEL_VERSION, lifespan=lifespan)


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    max_new_tokens: int = Field(default=64, ge=1, le=MAX_NEW_TOKENS_CAP)


class BatchItem(BaseModel):
    id: str
    prompt: str = Field(min_length=1, max_length=2000)


class BatchRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=16)
    max_new_tokens: int = Field(default=64, ge=1, le=MAX_NEW_TOKENS_CAP)


def format_prompt(user_text: str) -> str:
    messages = [{"role": "user", "content": user_text}]
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )


def run_generation(input_ids: torch.Tensor, max_new_tokens: int) -> str:
    with torch.inference_mode():
        output = model.generate(
            input_ids,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
        )
    new_tokens = output[0][input_ids.shape[1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


def generate_text(prompt: str, max_new_tokens: int) -> tuple[str, float]:
    input_text = format_prompt(prompt)
    inputs = tokenizer(input_text, return_tensors="pt")
    start = time.perf_counter()
    text = run_generation(inputs["input_ids"], max_new_tokens)
    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    return text, latency_ms


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "version": MODEL_VERSION}


@app.get("/")
def root():
    return {
        "service": "tiny-llm-inference",
        "model": MODEL_ID,
        "version": MODEL_VERSION,
        "endpoints": {
            "generate": "POST /v1/generate",
            "stream": "POST /v1/generate/stream",
            "batch": "POST /v1/batch/infer",
            "demo": "GET /demo",
            "docs": "GET /docs",
        },
    }


@app.post("/v1/generate")
def generate(request: GenerateRequest):
    text, latency_ms = generate_text(request.prompt, request.max_new_tokens)
    return {
        "model": MODEL_ID,
        "version": MODEL_VERSION,
        "prompt": request.prompt,
        "completion": text,
        "latency_ms": latency_ms,
    }


@app.post("/v1/generate/stream")
def generate_stream(request: GenerateRequest):
    input_text = format_prompt(request.prompt)
    inputs = tokenizer(input_text, return_tensors="pt")
    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)
    generation_kwargs = {
        **inputs,
        "streamer": streamer,
        "max_new_tokens": request.max_new_tokens,
        "do_sample": True,
        "temperature": 0.7,
        "top_p": 0.9,
    }

    def token_stream():
        thread = Thread(target=model.generate, kwargs=generation_kwargs)
        thread.start()
        for chunk in streamer:
            if chunk:
                yield chunk
        thread.join()

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")


@app.post("/v1/batch/infer")
def batch_infer(request: BatchRequest):
    start = time.perf_counter()
    results = []
    for item in request.items:
        text, item_latency_ms = generate_text(item.prompt, request.max_new_tokens)
        results.append(
            {
                "id": item.id,
                "prompt": item.prompt,
                "completion": text,
                "latency_ms": item_latency_ms,
            }
        )

    total_ms = round((time.perf_counter() - start) * 1000, 2)
    return {
        "model": MODEL_ID,
        "version": MODEL_VERSION,
        "processed": len(results),
        "total_latency_ms": total_ms,
        "results": results,
    }


@app.get("/demo", response_class=HTMLResponse)
def demo_page():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Tiny LLM stream demo</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }
    textarea { width: 100%; height: 100px; font-size: 1rem; }
    button { margin-top: 0.5rem; padding: 0.5rem 1rem; font-size: 1rem; }
    pre { background: #f4f4f4; padding: 1rem; border-radius: 8px; white-space: pre-wrap; min-height: 120px; }
  </style>
</head>
<body>
  <h1>Tiny LLM (streaming)</h1>
  <p>Token-by-token output from <code>SmolLM2-135M-Instruct</code> on this EC2 container.</p>
  <textarea id="prompt">Explain OIDC in one sentence.</textarea>
  <button id="go">Generate (stream)</button>
  <pre id="out"></pre>
  <script>
    const out = document.getElementById("out");
    document.getElementById("go").onclick = async () => {
      out.textContent = "";
      const prompt = document.getElementById("prompt").value;
      const res = await fetch("/v1/generate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, max_new_tokens: 80 }),
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        out.textContent += decoder.decode(value);
      }
    };
  </script>
</body>
</html>"""
