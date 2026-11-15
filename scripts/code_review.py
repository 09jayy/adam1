import argparse
import json
import os
import re
import shlex
import time
import sys
from string import Template

import requests

DEFAULT_ARGS = {
    "model": "deepseek-flash",
    "note": None
}


MODELS = {
    "deepseek-flash": "deepseek/deepseek-v4.1-flash",
    "kimi-k3": "moonshotai/kimi-k3",
    "qwen3.8-27B": "qwen/qwen3.8-27b",
}


def fallback_to_default_args(args_dict):
    if args_dict.get("model") not in MODELS:
        print(
            f"::warning::model argument: {args_dict.get('model')} - falling back to default model: {DEFAULT_ARGS.get('model')}"
        )
        args_dict["model"] = DEFAULT_ARGS["model"]

    return args_dict


API_KEY = os.environ.get("CODE_REVIEW_KEY")
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

def user_prompt(pr_title, pr_body, diffs, note):
    pr = f"Title: {pr_title}\nDescription: {pr_body}\ndiffs:\n{diffs}"
    return pr if note is None else pr + f"Additionally, the coder has also left this note to consider during your review: {note}"

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


def get_ai_review(diffs, pr_title, pr_body, args_dict, API_KEY):
    diffs = annotate_diff(diffs)
    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "model": MODELS.get(args_dict["model"]),
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": user_prompt(
                            pr_title=pr_title, pr_body=pr_body, diffs=diffs, note=args_dict["note"]
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
        pr["body"] or "",
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


def main(args_dict):
    if not API_KEY or not GITHUB_TOKEN or not REPO or not PR_NUMBER:
        raise SystemExit("Missing environment variables - failed to set one or more")

    commit_sha, base_ref, repo_name, pr_title, pr_body = get_pr_context()
    diffs = get_pull_request_diffs(base_ref)

    if diffs is None:
        raise SystemExit("Failed to generate pull request diffs")

    response = get_ai_review(diffs, pr_title, pr_body, args_dict, API_KEY)

    print(json.dumps(response, indent=2))

    try:
        comments = json.loads(response["content"]).get("comments", [])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Bad model output: {e}")

    post_all(comments, commit_sha, repo_name)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Run AI code review")

    parser.add_argument(
        "-m",
        "--model",
        help="model that will run the ai code review",
        default=DEFAULT_ARGS["model"]
    )
    parser.add_argument(
        "-n",
        "--note",
        help="note to be appended to the review prompt - adds extra context to the LLM",
        default=DEFAULT_ARGS["note"],
        type=str
    )

    args, unknown = parser.parse_known_args()

    print(f"::debug:: unknown args: {unknown}")
    print(f"::debug:: args: {args}")

    args_dict = fallback_to_default_args(args_dict)

    print(f"::debug:: args_dict: {args_dict}")

    print(f"::notice title=Code Review Model::{args_dict.get('model')}")
    main(args_dict)
