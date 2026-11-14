import os
import json
import subprocess
import requests
import re

# Setup Environment variables
API_KEY = os.environ.get("CODE_REVIEW_KEY")
MODEL = os.environ.get("CODE_REVIEW_MODEL")

# Setup constants
ENABLE_REASONING = False
SYSTEM_PROMPT = """
You are a senior software engineer reviewing code. Your priorities are readability, maintainability and following 
industry standard best practices where possible. The code is written is deployed to a small home-lab server, keep
this in mind when reviewing code as this is the environment the code will be deployed to

## Strict Output Format
You must output a valid **JSON Array** (`[...]`). Do not wrap the review in a single monolithic block or a wrapper object. 

Each object in the array must represent ONE distinct comment or issue, mapped precisely to its line number in the diff. 
For every line of code you have a comment about that will be a SEPERATE json object schema, strictly follow this do not include multiple
comments in one body.
Attach the comment to the line where the bug occurs, not the line where a variable is declared or a block ends.
Added and unchanged lines in the diff are prefixed with their line number in the new file.
Removed lines (starting with "-") have no number; never comment on them.
Use the number printed on the exact line the problem is on. Do not infer or count.

### Required JSON Schema per Object:
```json
{
  "path": "string (the file path)",
  "line": "integer (the exact line number the comment targets)",
  "body": "string (the concise markdown comment for this specific line only)"
}### Example Output:
[
  {
    "path": "src/core/ConfigParser.cpp",
    "line": 17,
    "body": "**Update header**: Make sure to update `validate() const` in `ConfigParser.h` as well."
  },
  {
    "path": "src/core/ConfigParser.cpp",
    "line": 18,
    "body": "**Exception safety**: Ensure this constructor isn't marked `noexcept` since it now throws `std::invalid_argument`."
  }
]
"""

USER_PROMPT = "Review these diffs from the Pull Request:"

AI_RESPONSE_JSON_SCHEMA = {
    "name": "code_review",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "body": {
                            "type": "string",
                            "description": "Concise markdown comment for this specific line range only.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "First line the comment is about, must be <= 'line'.",
                        },
                        "line": {
                            "type": "integer",
                            "description": "Last line the comment is about, must be >= 'start_line'.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Relative path of the file the comment goes on.",
                        },
                    },
                    "required": ["body", "start_line", "line", "path"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["comments"],
        "additionalProperties": False,
    },
}

def annotate_diff(diff):
    out = []
    new_line = 0
    for raw in diff.splitlines():
        m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if m:
            new_line = int(m.group(1))
            out.append(raw)
        elif raw.startswith(("diff ", "index ", "--- ", "+++ ")):
            out.append(raw)
        elif raw.startswith("-"):
            out.append(f"     {raw}")  # removed line, no new-file number
        elif raw.startswith("+") or raw.startswith(" "):
            out.append(f"{new_line:>4} {raw}")
            new_line += 1
        else:
            out.append(raw)
    return "\n".join(out)

def get_pull_request_diffs():
    try:
        result = subprocess.run(
            ["git","diff","origin/main...HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running git diff: {e}")
        return None

def get_ai_review(diffs, MODEL, API_KEY):
    diffs = annotate_diff(diffs)
    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps({
            "model": MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": f"{USER_PROMPT}\n{diffs}"
                }
            ],
            "reasoning": {"enabled": ENABLE_REASONING},
            "response_format": {
                "type": "json_schema",
                "json_schema": AI_RESPONSE_JSON_SCHEMA,
            },
            "provider": { "require_parameters": True }
        }),
        timeout=60
    )
    
    if response.status_code >= 400:
        print(f"Status Code: {response.status_code}\nAPI Error: {response}")
        response.raise_for_status()
        raise
    
    try:
        response = response.json()
    except requests.exceptions.JSONDecodeError:
        response.raise_for_status()
        raise

    return response['choices'][0]['message']
 

def main():
    if not API_KEY or not MODEL:
        raise SystemExit("CODE_REVIEW_KEY and CODE_REVIEW_MODEL must be set in environment variables")

    diffs = get_pull_request_diffs()

    if diffs is None:
        raise SystemExit("Failed to generate pull request diffs")

    response = get_ai_review(diffs, MODEL, API_KEY)

    # TODO: post response as a comment under Pull Request rather than log
    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    main()
