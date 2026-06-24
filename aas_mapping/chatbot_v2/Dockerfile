FROM python:3.12-slim

# LangGraph agent stack (native tool-calling) over the KIConnect OpenAI-compatible API.
RUN pip install --no-cache-dir \
    flask requests \
    "langgraph>=0.2" "langchain-openai>=0.2" "langchain-core>=0.3" \
    "langchain-community>=0.3" "faiss-cpu>=1.8" "langfuse>=2.0"

WORKDIR /app

# Install aas4graph (neo4aas) from the local checkout (build context = repo root) so
# chatbot_v2 reuses the shared agent_tools. Brings the neo4j driver and basyx.
COPY . /src/neo4aas
RUN pip install --no-cache-dir /src/neo4aas

# chatbot_v2 app modules (flat imports) + static assets.
COPY aas_mapping/chatbot_v2/app.py aas_mapping/chatbot_v2/config.py \
     aas_mapping/chatbot_v2/llm.py aas_mapping/chatbot_v2/tools.py \
     aas_mapping/chatbot_v2/graph.py aas_mapping/chatbot_v2/system_prompt.py ./
COPY aas_mapping/chatbot_v2/static/ static/

ENV KICONNECT_API_KEY=""
ENV REPOSITORY_URL="http://repository/api/v3.1"

EXPOSE 8091

CMD ["python", "app.py"]
