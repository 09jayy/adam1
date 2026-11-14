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

def main():
    diffs = get_pull_request_diffs()

    if diffs is None:
        raise SystemExit("Failed to generate pull request diffs")

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
            "reasoning": {"enabled": ENABLE_REASONING}
        })
    )

    response = response.json()
    
    if "error" in response:
        raise SystemExit(f"API Error: {response['error']['message']}")

    response = response['choices'][0]['message']
    
    # TODO: post response as a comment under Pull Request rather than log
    print(json.dumps(response, indent=2))

if __name__ == "__main__":
    main()
