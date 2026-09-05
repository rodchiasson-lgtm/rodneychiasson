import traceback

from flask import Flask, jsonify, request

from stock_agent import run_agent

app = Flask(__name__)

_MAX_QUERY_LENGTH = 2000


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    query = data.get("query")

    if not isinstance(query, str) or not query.strip():
        return jsonify({"error": "'query' is required and must be a non-empty string"}), 400
    if len(query) > _MAX_QUERY_LENGTH:
        return jsonify({"error": f"'query' must be at most {_MAX_QUERY_LENGTH} characters"}), 400

    try:
        response = run_agent(query)
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Something went wrong analyzing that query"}), 500

    return jsonify({"response": response})


if __name__ == "__main__":
    # Bind to localhost only, and never run with debug=True: Flask's debugger
    # allows arbitrary code execution if the server is ever reachable by
    # anyone untrusted.
    app.run(host="127.0.0.1", port=5001)
