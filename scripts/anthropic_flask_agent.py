import os
import secrets
import threading
import traceback

from flask import Flask, jsonify, request, session

from anthropic_stateful_agent import Agent

app = Flask(__name__)
# A random secret is fine for a local demo (sessions reset on restart); set
# FLASK_SECRET_KEY yourself for anything that needs sessions to survive one.
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

_MAX_MESSAGE_LENGTH = 4000

# One Agent (conversation history) per browser session, each guarded by its
# own lock so two requests from the same session can't race on self.messages.
_agents: dict[str, tuple[Agent, threading.Lock]] = {}
_agents_lock = threading.Lock()


def _get_agent() -> tuple[Agent, threading.Lock]:
    if "session_id" not in session:
        session["session_id"] = secrets.token_hex(16)
    session_id = session["session_id"]

    with _agents_lock:
        if session_id not in _agents:
            _agents[session_id] = (Agent(), threading.Lock())
        return _agents[session_id]


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")

    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "'message' is required and must be a non-empty string"}), 400
    if len(message) > _MAX_MESSAGE_LENGTH:
        return jsonify({"error": f"'message' must be at most {_MAX_MESSAGE_LENGTH} characters"}), 400

    agent, lock = _get_agent()

    try:
        with lock:
            response = agent.chat(message)
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Something went wrong processing your message"}), 500

    return jsonify({"response": response})


if __name__ == "__main__":
    # Bind to localhost only, and never run with debug=True: Flask's debugger
    # allows arbitrary code execution if the server is ever reachable by
    # anyone untrusted.
    app.run(host="127.0.0.1", port=5000)
