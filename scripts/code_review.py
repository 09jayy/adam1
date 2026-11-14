import os
import json
import subprocess
import requests

# Setup Environment variables
API_KEY = os.environ.get("CODE_REVIEW_KEY")
MODEL = os.environ.get("CODE_REVIEW_MODEL")

# Setup constants
ENABLE_REASONING = True
SYSTEM_PROMPT = """
You are a senior software engineer reviewing code. Your priorities are readability, maintainability and following 
industry standard best practices where possible. The code is written is deployed to a small home-lab server, keep
this in mind when reviewing code as this is the environment the code will be deployed to"""
USER_PROMPT = "Review these diffs from the Pull Request:"

AI_RESPONSE_JSON_SCHEMA = {
    "name": "code_review_comment",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "body": {
                "type": "string",
                "description": "Review comment content. Format is markdown.",
            },
            "start_line": {
                "type": "integer",
                "description": "Start line the comment is about, must be <= 'line'",
            },
            "line": {
                "type": "integer",
                "description": "End line the comment is about, must be >= 'start_line'",
            },
            "path": {
                "type": "string",
                "description": "Relative path of the file the comment goes on",
            },
        },
        "required": ["body", "start_line", "line", "path"],
        "additionalProperties": False,
    },
}

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

def get_ai_review(diffs):
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
                    "content": ""
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
 
    try:
        response = response.json()
    except requests.exceptions.JSONDecodeError:
        response.raise_for_status()
        raise

    if response.status_code >= 400:
        print(f"Status Code: {response.status_code}\nAPI Error: {response}")
        response.raise_for_status()
        raise

    return response['choices'][0]['message']
 

def main():
    if not API_KEY or not MODEL:
        raise SystemExit("CODE_REVIEW_KEY and CODE_REVIEW_MODEL must be set in environment variables")

    diffs = get_pull_request_diffs()

    if diffs is None:
        raise SystemExit("Failed to generate pull request diffs")

    response = get_ai_review(diffs)

    # TODO: post response as a comment under Pull Request rather than log
    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    main()
