"""T2: Gemini function calling smoke test."""
import os, sys
sys.path.insert(0, os.path.expanduser("~/gemini-agent"))

from google import genai
from google.genai import types

api_key = os.environ.get("GOOGLE_API_KEY")
assert api_key, "GOOGLE_API_KEY not set"

client = genai.Client(api_key=api_key)

tools = types.Tool(function_declarations=[
    types.FunctionDeclaration(
        name="echo_tool",
        description="Echoes text back.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"text": types.Schema(type=types.Type.STRING, description="text to echo")},
            required=["text"],
        ),
    )
])

history = [types.Content(role="user", parts=[types.Part(text="Call echo_tool with text 'hello world'")])]

resp = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=history,
    config=types.GenerateContentConfig(tools=[tools], temperature=0),
)

assert resp.candidates, f"No candidates in response: {resp}"
model_content = resp.candidates[0].content
assert model_content and model_content.parts, f"Empty content: {model_content}"

function_calls = [p.function_call for p in model_content.parts if hasattr(p, "function_call") and p.function_call]
assert function_calls, f"Expected function call, got parts: {model_content.parts}"
assert function_calls[0].name == "echo_tool", f"Wrong tool: {function_calls[0].name}"
assert function_calls[0].args["text"] == "hello world", f"Wrong args: {function_calls[0].args}"
print(f"  Tool call: echo_tool(text='hello world') ✓")

# Feed result back
history.append(model_content)
history.append(types.Content(role="user", parts=[types.Part(
    function_response=types.FunctionResponse(
        name="echo_tool",
        response={"result": "hello world"},
    )
)]))

resp2 = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=history,
    config=types.GenerateContentConfig(tools=[tools], temperature=0),
)

assert resp2.candidates, "No candidates in second response"
final_parts = resp2.candidates[0].content.parts
has_text = any(hasattr(p, "text") and p.text for p in final_parts)
has_fn = any(hasattr(p, "function_call") and p.function_call for p in final_parts)
assert has_text and not has_fn, f"Expected text response, got: {final_parts}"
print(f"  Final text response (no more tool calls) ✓")
print("T2 PASSED")
