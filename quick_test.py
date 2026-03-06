import asyncio
from ollama_client import generate_email
result = asyncio.run(generate_email("hr@testcorp.com"))
print(result)
assert "subject" in result and "body" in result
print("✅ Ollama OK")