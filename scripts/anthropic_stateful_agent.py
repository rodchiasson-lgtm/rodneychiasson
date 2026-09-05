import anthropic
from dotenv import load_dotenv

from anthropic_agent_example import calculate

load_dotenv()

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
    }
]

tool_functions = {
    "calculate": calculate,
}


class Agent:
    def __init__(self):
        self.client = anthropic.Anthropic()
        self.messages = []  # Conversation history
        self.system_prompt = (
            "You are a helpful assistant with access to tools. "
            "Use them to help answer user questions. Be concise."
        )

    def chat(self, user_input: str) -> str:
        self.messages.append({
            "role": "user",
            "content": user_input
        })

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            system=self.system_prompt,
            tools=tools,
            messages=self.messages
        )

        # Handle tool use + build memory
        while response.stop_reason == "tool_use":
            self.messages.append({
                "role": "assistant",
                "content": response.content
            })

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        result = tool_functions[block.name](**block.input)
                        is_error = False
                    except Exception as e:
                        result = f"Error: {e}"
                        is_error = True

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                        "is_error": is_error,
                    })

            self.messages.append({
                "role": "user",
                "content": tool_results
            })

            response = self.client.messages.create(
                model="claude-opus-5",
                max_tokens=1024,
                system=self.system_prompt,
                tools=tools,
                messages=self.messages
            )

        final_response = next(
            (block.text for block in response.content if block.type == "text"),
            "No response"
        )

        self.messages.append({
            "role": "assistant",
            "content": final_response
        })

        return final_response


if __name__ == "__main__":
    agent = Agent()
    print(agent.chat("What's 2+2?"))
    print(agent.chat("And what's that times 5?"))  # Agent remembers context
