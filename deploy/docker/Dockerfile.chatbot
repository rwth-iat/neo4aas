FROM python:3.12-slim

WORKDIR /app

# Install neo4aas from the local checkout (build context = repo root) so the chatbot
# reuses the shared agent_tools without a push. The [chatbot] extra brings the
# LangGraph/LangChain stack, so the dependency list lives in pyproject.toml only —
# and note it does NOT pull basyx: the chatbot talks to core and the REST API.
# The chatbot is a package inside the distribution, so its modules and static/
# assets come along; there is no per-file COPY list to keep in sync.
COPY . /src/neo4aas
RUN pip install --no-cache-dir "/src/neo4aas[chatbot]"

ENV KICONNECT_API_KEY=""
ENV REPOSITORY_URL="http://repository/api/v3.1"

EXPOSE 8091

CMD ["python", "-m", "neo4aas.chatbot"]
