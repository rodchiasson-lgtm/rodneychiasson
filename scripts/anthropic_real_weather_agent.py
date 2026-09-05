import anthropic
import requests
from dotenv import load_dotenv

from anthropic_agent_example import calculate

load_dotenv()

client = anthropic.Anthropic()

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


def get_weather(city: str) -> str:
    """Look up current weather via Open-Meteo (no API key required)."""
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
            timeout=10,
        )
        geo.raise_for_status()
        results = geo.json().get("results")
        if not results:
            return f"City {city} not found"
        lat, lon = results[0]["latitude"], results[0]["longitude"]

        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon, "current": "temperature_2m"},
            timeout=10,
        )
        weather.raise_for_status()
        temp = weather.json()["current"]["temperature_2m"]
    except requests.exceptions.RequestException as e:
        return f"Error fetching weather: {e}"
    except (KeyError, IndexError, ValueError):
        return f"Weather data not available for {city}"

    return f"{city}: {temp}°C"


tool_functions = {
    "calculate": calculate,
    "get_weather": get_weather,
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
    run_agent("What's the weather in London and what's 15 * 8?")
    run_agent("Calculate (100 + 50) / 3 and tell me the weather in Tokyo")
