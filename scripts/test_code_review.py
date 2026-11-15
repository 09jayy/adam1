# testing llm response

import os
import requests
import json
from dotenv import load_dotenv

from code_review import get_ai_review

load_dotenv()

API_KEY = os.getenv("CODE_REVIEW_KEY")
MODEL = os.getenv("CODE_REVIEW_MODEL")

fixture = """\
diff --git a/src/core/ConfigParser.cpp b/src/core/ConfigParser.cpp
index 4a2b8e1..8f3c110 100644
--- a/src/core/ConfigParser.cpp
+++ b/src/core/ConfigParser.cpp
@@ -14,10 +14,14 @@ namespace Core {
     constexpr uint32_t DEFAULT_BUFFER_SIZE = 4096;
+    constexpr std::chrono::seconds DEFAULT_TIMEOUT(30);
 
     ConfigParser::ConfigParser(const std::string& filepath)
-        : m_filepath(filepath), m_timeoutSecs(30) {
+        : m_filepath(filepath), m_timeoutSecs(DEFAULT_TIMEOUT.count()) {
+        if (filepath.empty()) {
+            throw std::invalid_argument("Config file path cannot be empty.");
+        }
     }
 
-    bool ConfigParser::validate() {
+    bool ConfigParser::validate() const {
         std::ifstream file(m_filepath);
         return file.is_open();
     }
diff --git a/src/util/FileUtils.cpp b/src/util/FileUtils.cpp
new file mode 100644
index 0000000..3c9d2f7
--- /dev/null
+++ b/src/util/FileUtils.cpp
@@ -0,0 +1,21 @@
+#include "FileUtils.h"
+#include <fstream>
+#include <sstream>
+
+namespace Util {
+
+    std::string readFile(const std::string& path) {
+        std::ifstream file(path);
+        std::stringstream buffer;
+        buffer << file.rdbuf();
+        return buffer.str();
+    }
+
+    void writeLog(const char* msg) {
+        char buf[64];
+        strcpy(buf, msg);
+        FILE* f = fopen("/tmp/app.log", "a");
+        fprintf(f, "%s\\n", buf);
+    }
+
+}
"""


def parse_comments(content: str):
    try:
        return json.loads(content)["comments"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Bad model output: {e}\n{content}")
        return None


def test_get_ai_review():
    # 1. Fetch raw response from the AI wrapper
    response = get_ai_review(fixture, MODEL, API_KEY)

    # 2. Guard against missing or malformed response shapes
    content = response.get("content") if isinstance(response, dict) else None
    if not content:
        raise AssertionError(f"Expected a message dict with 'content', got: {response}")

    # 3. Parse and validate the comments structure
    comments = parse_comments(content)
    print(comments)
    assert comments is not None, (
        "parse_comments failed; check stdout for raw model output."
    )
    assert isinstance(comments, list), (
        f"Expected list of comments, got {type(comments)}"
    )

    print(f"Successfully parsed {len(comments)} comments.")
    assert len(comments) > 0
