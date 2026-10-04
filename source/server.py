"""vm106 hosting entrypoint for bryn1/rocket (MC#2317).

The vm106 renderer runs `python server.py` with NO PORT env and nginx proxies
sibbamala.com/rocket/ -> 127.0.0.1:8118. app.py's __main__ block binds 8050, so this
shim imports the Dash `app` object and binds the manifest PORT (default 8118) on 0.0.0.0.
"""
import os

from app import app  # Dash instance defined in app.py (root)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8118"))
    app.run(host="0.0.0.0", port=port, debug=False)
