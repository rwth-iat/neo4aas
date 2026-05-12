from app.interfaces.repository import WSGIApp


class Neo4jWSGIApp(WSGIApp):
    """WSGIApp backed by Neo4jObjectStore.

    All query logic (/query/shells, /query/submodels) and capability advertisement
    are handled by the base WSGIApp via the QueryableObjectStore protocol.
    This class is kept as a named entry point for the Neo4j deployment.
    """
