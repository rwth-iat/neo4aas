FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git gcc && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir gunicorn

# Install SDK first — other packages declare it as a dependency
RUN pip install --no-cache-dir \
    "basyx-python-sdk @ git+https://github.com/rwth-iat/basyx-python-sdk.git@aas-demonstrator#subdirectory=sdk"

# Install server package — provides `app` package with WSGIApp + AASQL routes
RUN pip install --no-cache-dir \
    "basyx-python-server @ git+https://github.com/rwth-iat/basyx-python-sdk.git@aas-demonstrator#subdirectory=server"

# Install neo4aas from the local checkout (build context = repo root) so local changes
# apply without pushing. The [server] extra pulls the basyx integration layer; the SDK
# itself is already installed from git above, so it is satisfied and not re-pulled
# from PyPI.
COPY . /src/neo4aas
RUN pip install --no-cache-dir "/src/neo4aas[server]"

ENV INPUT=/input
ENV NEO4J_URI=bolt://neo4j:7687
ENV NEO4J_USER=neo4j
ENV NEO4J_PASSWORD=12345678
ENV API_BASE_PATH=/api/v3.1/

VOLUME ["/input"]
EXPOSE 80

CMD ["gunicorn", "--bind", "0.0.0.0:80", "--workers", "1", "--timeout", "120", \
     "neo4aas.basyx_ext.server.run_repository:application"]
