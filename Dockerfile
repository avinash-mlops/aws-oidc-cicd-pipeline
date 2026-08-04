FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ENV MODEL_ID=HuggingFaceTB/SmolLM2-135M-Instruct
ENV HF_HOME=/model-cache
ENV TRANSFORMERS_CACHE=/model-cache

RUN python -c "\
from transformers import AutoModelForCausalLM, AutoTokenizer; \
AutoTokenizer.from_pretrained('HuggingFaceTB/SmolLM2-135M-Instruct'); \
AutoModelForCausalLM.from_pretrained('HuggingFaceTB/SmolLM2-135M-Instruct')"

COPY app.py .

EXPOSE 3000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
