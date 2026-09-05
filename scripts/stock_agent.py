import anthropic
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException

from anthropic_agent_example import calculate

load_dotenv()

client = anthropic.Anthropic()

SYSTEM_PROMPT = (
    "You are a stock sentiment research assistant. Use web_search to find "
    "recent news, analyst commentary, and discussion for the ticker(s) in "
    "the user's query, then summarize the sentiment (positive/negative/mixed) "
    "and cite what you found. If comparing multiple tickers, research each "
    "one separately before comparing. This is informational research only, "
    "not financial advice, and you have no real-time price or market data - "
    "say so if the query needs data you can't get from search."
)

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
        "name": "web_search",
        "description": "Search the web for current information",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query"
                }
            },
            "required": ["query"]
        }
    }
]


def web_search(query: str, max_results: int = 5) -> str:
    try:
        results = DDGS().text(query, max_results=max_results)
    except DuckDuckGoSearchException as e:
        return f"Error: {e}"

    if not results:
        return "No results found."

    return "\n\n".join(
        f"{r['title']}\n{r['href']}\n{r['body']}" for r in results
    )


tool_functions = {
    "calculate": calculate,
    "web_search": web_search,
}


def run_agent(user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages
        )

        if response.stop_reason == "tool_use":
            tool_calls = [block for block in response.content if block.type == "tool_use"]

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_call in tool_calls:
                print(f"  Using tool: {tool_call.name}({tool_call.input})")
                try:
                    result = tool_functions[tool_call.name](**tool_call.input)
                    is_error = False
                except Exception as e:
                    result = f"Error: {e}"
                    is_error = True

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": result,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})
        else:
            return next(
                (block.text for block in response.content if block.type == "text"),
                "No response"
            )


if __name__ == "__main__":
    while True:
        try:
            query = input("\n📝 Enter query: ")
        except (EOFError, KeyboardInterrupt):
            break
        if query.lower() in ("exit", "quit"):
            break
        if not query.strip():
            continue
        print(f"\n{run_agent(query)}\n")
