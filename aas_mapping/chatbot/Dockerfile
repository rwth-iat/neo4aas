FROM python:3.12-slim

RUN pip install --no-cache-dir flask openai requests

WORKDIR /app

# Install aas4graph (neo4aas) from the local checkout (build context = repo root) so the
# chatbot can reuse the shared agent_tools (cypher_read + neo4aas read tools). Brings the
# neo4j driver and basyx as dependencies.
COPY . /src/neo4aas
RUN pip install --no-cache-dir /src/neo4aas

# Chatbot app modules (flat imports) + static assets.
COPY aas_mapping/chatbot/app.py aas_mapping/chatbot/config.py aas_mapping/chatbot/llm.py \
     aas_mapping/chatbot/tools.py aas_mapping/chatbot/orchestrator.py \
     aas_mapping/chatbot/system_prompt.py ./
COPY aas_mapping/chatbot/static/ static/

ENV KICONNECT_API_KEY=""
ENV REPOSITORY_URL="http://repository/api/v3.1"

EXPOSE 8090

CMD ["python", "app.py"]
