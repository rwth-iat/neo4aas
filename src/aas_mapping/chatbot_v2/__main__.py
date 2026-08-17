"""Entry point: ``python -m aas_mapping.chatbot_v2``.

Kept separate from ``app.py`` so importing the Flask app (tests, a WSGI server)
never starts a listener.
"""

import os

from .app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8091")))
