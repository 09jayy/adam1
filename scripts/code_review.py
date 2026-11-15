import json
import os
import re
import time
from string import Template

import requests

API_KEY = os.environ.get("CODE_REVIEW_KEY")
MODEL = os.environ.get("CODE_REVIEW_MODEL")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
REPO = os.environ.get("GITHUB_REPOSITORY")
PR_NUMBER = os.environ.get("PR_NUMBER")

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

USER_PROMPT = Template("Title: $pr_title\nDescription: $pr_body\ndiffs:\n$diffs")

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
        elif raw.startswith(("+", " ")):
            out.append(f"{new_line:>4} {raw}")
            new_line += 1
        else:
            out.append(raw)
    return "\n".join(out)


def get_pull_request_diffs(base_ref):
    response = requests.get(
        url=f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.diff",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise SystemExit(
            f"Failed to fetch PR diffs: {response.status_code} - {response.json()}"
        )
    return response.text


def get_ai_review(diffs, pr_title, pr_body, MODEL, API_KEY):
    diffs = annotate_diff(diffs)
    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": USER_PROMPT.substitute(
                            pr_title=pr_title, pr_body=pr_body, diffs=diffs
                        ),
                    },
                ],
                "reasoning": {"enabled": ENABLE_REASONING},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": AI_RESPONSE_JSON_SCHEMA,
                },
                "provider": {"require_parameters": True},
            }
        ),
        timeout=60,
    )

    if response.status_code >= 400:
        print(f"Status Code: {response.status_code}\nAPI Error: {response}")
        response.raise_for_status()

    try:
        response = response.json()
    except requests.exceptions.JSONDecodeError:
        response.raise_for_status()

    return response["choices"][0]["message"]


def get_pr_context():
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30,
    )
    r.raise_for_status()
    pr = r.json()
    return (
        pr["head"]["sha"],
        pr["base"]["ref"],
        pr["head"]["repo"]["full_name"],
        pr["title"],
        pr["body"] if pr["body"] != None or pr["body"] == "" else "",
    )


def post_line_comment(c, commit_sha, repo_name):
    payload = {
        "body": c["body"],
        "commit_id": commit_sha,
        "path": c["path"],
        "line": c["line"],
        "side": "RIGHT",
    }
    if c["start_line"] < c["line"]:
        payload["start_line"] = c["start_line"]
        payload["start_side"] = "RIGHT"

    r = requests.post(
        f"https://api.github.com/repos/{repo_name}/pulls/{PR_NUMBER}/comments",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
        json=payload,
        timeout=30,
    )
    if r.status_code >= 400:
        print(
            f"::warning::Could not post comment - {r.json().get('message') if r.json().get('message') else r.json()}"
        )
        return None
    return r.json()


def post_all(comments, commit_sha, repo_name):
    for c in comments:
        post_line_comment(c, commit_sha, repo_name)
        time.sleep(1)  # avoid GitHub's secondary rate limits


def main():
    if not API_KEY or not MODEL or not GITHUB_TOKEN or not REPO or not PR_NUMBER:
        raise SystemExit("Missing environment variables - failed to set one or more")

    commit_sha, base_ref, repo_name, pr_title, pr_body = get_pr_context()
    diffs = get_pull_request_diffs(base_ref)

    if diffs is None:
        raise SystemExit("Failed to generate pull request diffs")

    response = get_ai_review(diffs, pr_title, pr_body, MODEL, API_KEY)

    print(json.dumps(response, indent=2))

    try:
        comments = json.loads(response["content"]).get("comments", [])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Bad model output: {e}")

    post_all(comments, commit_sha, repo_name)


if __name__ == "__main__":
    main()
