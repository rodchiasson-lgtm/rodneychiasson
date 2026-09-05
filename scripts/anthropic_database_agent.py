import re
import sqlite3

import anthropic
from dotenv import load_dotenv

from anthropic_agent_example import calculate

load_dotenv()

client = anthropic.Anthropic()

DB_PATH = "example.db"

tools = [
    {
        "name": "calculate",
        "description": "Perform mathematical calculations",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression to evaluate (e.g., '2 + 2 * 3')"
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "query_database",
        "description": "Run a read-only SQL SELECT query against the example database. "
                        "Available table: people(id, name, city).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A single SELECT statement, e.g. 'SELECT * FROM people LIMIT 10'"
                }
            },
            "required": ["query"]
        }
    }
]

_ROW_LIMIT = 100

# Blocks statement types SQLite could still execute even if the string starts
# with SELECT (e.g. a CTE feeding an ATTACH), since the string is a full SQL
# statement chosen by the model rather than a parameterized value.
_DISALLOWED_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|into)\b",
    re.IGNORECASE,
)


def _ensure_example_db() -> None:
    """Create the demo database with sample data if it doesn't exist yet."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, name TEXT, city TEXT)"
        )
        if conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO people (name, city) VALUES (?, ?)",
                [("Alice", "New York"), ("Bob", "London"), ("Carol", "Tokyo")],
            )


def query_database(query: str) -> str:
    """Run a read-only SELECT query. Rejects anything else, defense in depth."""
    statement = query.strip().rstrip(";")

    if ";" in statement:
        return "Error: only a single statement is allowed"
    if not statement.lower().startswith("select"):
        return "Error: only SELECT queries are allowed"
    if _DISALLOWED_KEYWORDS.search(statement):
        return "Error: query contains a disallowed keyword"

    try:
        # mode=ro opens the file read-only at the OS level, so even a
        # statement that slips past the checks above can't write.
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
            rows = conn.execute(statement).fetchmany(_ROW_LIMIT)
        return str(rows)
    except sqlite3.Error as e:
        return f"Error: {e}"


tool_functions = {
    "calculate": calculate,
    "query_database": query_database,
}


def run_agent(user_message: str):
    """Run agent with agentic loop"""
    print(f"\nUser: {user_message}")

    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            tools=tools,
            messages=messages
        )

        if response.stop_reason == "tool_use":
            tool_calls = [block for block in response.content if block.type == "tool_use"]

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_call in tool_calls:
                print(f"  Using tool: {tool_call.name}")
                try:
                    result = tool_functions[tool_call.name](**tool_call.input)
                    is_error = False
                except Exception as e:
                    result = f"Error: {e}"
                    is_error = True
                print(f"     Result: {result}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": result,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})
        else:
            final_response = next(
                (block.text for block in response.content if block.type == "text"),
                "No response"
            )
            print(f"Agent: {final_response}\n")
            break


if __name__ == "__main__":
    _ensure_example_db()
    run_agent("List everyone in the people table and calculate 15 * 8")
