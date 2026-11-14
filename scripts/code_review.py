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
industry standard best practices where possible. The code is deployed to a small home-lab server, so keep this
environment in mind when reviewing.

## Input Format
You will receive a git diff. Added lines (starting with "+") and unchanged context lines are prefixed with their
line number in the new file. Removed lines (starting with "-") have no number; never comment on them.
Use the number printed on the exact line the problem is on. Do not infer or count.

## Output Format
Return a JSON object with a single key `comments`, containing an array. Each element is ONE distinct comment about
ONE issue. Never combine multiple issues into one comment, and do not write a summary or overview.

Each comment has these fields:

- `path`: the file path, as shown in the diff
- `start_line`: first line of the range the comment is about
- `line`: last line of the range the comment is about
- `body`: concise markdown comment about this specific issue only

For a single-line comment, set `start_line` equal to `line`. Only use a range when the issue genuinely spans
multiple lines. Attach each comment to the line where the problem occurs, not to where a variable is declared or
a block ends.

## Review Guidelines
- Only comment on problems or concrete, actionable improvements. Do not leave praise or restate what the code does.
- Keep suggestions simple and proportionate. Do not recommend architectural changes unless there is a concrete bug.
- Only make claims you are certain are correct about the language, standard library and the code shown. Do not
  assume things about code outside the diff.
- If several includes are missing in a file, report them in a single comment on the first affected line.

## Example Output
{
  "comments": [
    {
      "path": "src/core/ConfigParser.cpp",
      "start_line": 17,
      "line": 17,
      "body": "**Update header**: Make sure to update `validate() const` in `ConfigParser.h` as well."
    },
    {
      "path": "src/core/ConfigParser.cpp",
      "start_line": 18,
      "line": 18,
      "body": "**Exception safety**: Ensure this constructor isn't marked `noexcept` since it now throws `std::invalid_argument`."
    }
  ]
}
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
    
    try:
        response = response.json()
    except requests.exceptions.JSONDecodeError:
        response.raise_for_status()

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
