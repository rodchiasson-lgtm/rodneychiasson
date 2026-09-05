import ast
import operator

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()

# Define tools your agent can use
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
        "name": "get_weather",
        "description": "Get current weather for a city",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name"
                }
            },
            "required": ["city"]
        }
    }
]

# Only these operators are allowed in `calculate` expressions.
_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


def safe_eval_math(expression: str) -> float:
    """Evaluate a numeric expression without running arbitrary code (no eval())."""
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree.body)


# Tool handlers (replace with real APIs as needed)
def calculate(expression: str) -> str:
    try:
        result = safe_eval_math(expression)
        return f"Result: {result}"
    except Exception as e:
        return f"Error: {e}"


def get_weather(city: str) -> str:
    # Mock weather data - replace with real API (OpenWeatherMap, etc.)
    weather_data = {
        "New York": "72°F, Sunny",
        "London": "55°F, Rainy",
        "Tokyo": "68°F, Cloudy"
    }
    return weather_data.get(city, f"Weather data not available for {city}")


# Map tool names to functions
tool_functions = {
    "calculate": calculate,
    "get_weather": get_weather
}


def run_agent(user_message: str):
    """Run agent with agentic loop"""
    print(f"\nUser: {user_message}")

    messages = [{"role": "user", "content": user_message}]

    # Agent loop - keep going until it stops calling tools
    while True:
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            tools=tools,
            messages=messages
        )

        # Check if agent wants to use tools
        if response.stop_reason == "tool_use":
            tool_calls = [block for block in response.content if block.type == "tool_use"]

            # Add assistant response to messages
            messages.append({"role": "assistant", "content": response.content})

            # Execute tools and collect results
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

            # Add tool results back to messages
            messages.append({"role": "user", "content": tool_results})
        else:
            # Agent is done - extract final response
            final_response = next(
                (block.text for block in response.content if block.type == "text"),
                "No response"
            )
            print(f"Agent: {final_response}\n")
            break


if __name__ == "__main__":
    run_agent("What's the weather in London and what's 15 * 8?")
    run_agent("Calculate (100 + 50) / 3 and tell me the weather in Tokyo")
