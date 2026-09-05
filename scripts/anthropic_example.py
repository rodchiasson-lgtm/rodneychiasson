import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Say hello in one sentence."}],
)

for block in response.content:
    if block.type == "text":
        print(block.text)
