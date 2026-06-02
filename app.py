"""
PyRunner Backend — Flask server for running and checking Python code.

Endpoints:
  GET  /ping   -> health check
  POST /run    -> execute Python code (supports stdin input)
  POST /check  -> analyze code for problems WITHOUT fixing it
"""

import subprocess
import sys
import os
import tempfile
import traceback

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins="*")

# ── Config ────────────────────────────────────────────────────────
TIMEOUT_SECONDS = 10
MAX_CODE_LENGTH = 50_000
MAX_OUTPUT_LENGTH = 100_000


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


# ── Health check ──────────────────────────────────────────────────
@app.route("/ping", methods=["GET", "OPTIONS"])
def ping():
    return jsonify({"status": "ok", "message": "PyRunner backend is alive"})


# ── Run code (with optional stdin input) ──────────────────────────
@app.route("/run", methods=["POST", "OPTIONS"])
def run_code():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})

    data = request.get_json(force=True)
    code = (data.get("code") or "").strip()
    user_input = data.get("input", "")  # text fed to the program's stdin

    if not code:
        return jsonify({"error": "No code provided"}), 400
    if len(code) > MAX_CODE_LENGTH:
        return jsonify({"error": f"Code too large (max {MAX_CODE_LENGTH} chars)"}), 400

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            input=user_input,          # <-- feeds the input box into stdin
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        stdout = result.stdout
        if len(stdout) > MAX_OUTPUT_LENGTH:
            stdout = stdout[:MAX_OUTPUT_LENGTH] + "\n\n[...output truncated]"

        if result.returncode != 0:
            return jsonify({"error": result.stderr or "Runtime error"})
        return jsonify({"output": stdout})

    except subprocess.TimeoutExpired:
        return jsonify({"error": f"Execution timed out after {TIMEOUT_SECONDS}s"})
    except Exception:
        return jsonify({"error": "Server error: " + traceback.format_exc()}), 500
    finally:
        os.unlink(tmp_path)


# ── Check code (report problems, do NOT fix) ──────────────────────
# Suggestion map: maps common pyflakes messages to human advice.
SUGGESTIONS = [
    ("undefined name", "You're using a variable or function that was never defined. "
                        "Check for a typo, or define/import it before this line."),
    ("imported but unused", "You imported something you never use. "
                            "Either use it or remove the import line."),
    ("redefinition of unused", "You defined the same name twice before using it. "
                               "Remove or rename one of them."),
    ("local variable", "You're using a local variable before assigning it a value. "
                       "Make sure it gets a value before this point."),
    ("invalid syntax", "Python can't parse this line. Check for missing colons (:), "
                       "unbalanced brackets, or quotes that aren't closed."),
    ("expected ':'", "You're missing a colon (:) at the end of this line. "
                     "Lines starting with if/for/while/def/class/else need a ':'."),
    ("expected", "Python expected a specific symbol here that's missing. "
                 "Check for a missing colon, bracket, or comma on this line."),
    ("unexpected indent", "This line is indented when it shouldn't be. "
                          "Align it with the surrounding code."),
    ("expected an indented block", "Python expected indented code here (after a "
                                   "':' line like if/for/def). Indent the next line."),
    ("unexpected EOF", "The file ended too early — you likely have an unclosed "
                       "bracket, parenthesis, or quote."),
    ("f-string", "There's a problem inside an f-string. Check the {expressions} for "
                 "typos or unbalanced braces."),
]


def suggest_for(message):
    low = message.lower()
    for needle, advice in SUGGESTIONS:
        if needle in low:
            return advice
    return "Review this line carefully against Python syntax rules."


@app.route("/check", methods=["POST", "OPTIONS"])
def check_code():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"})

    data = request.get_json(force=True)
    code = (data.get("code") or "")
    if not code.strip():
        return jsonify({"problems": []})

    problems = []

    # 1) Syntax check via ast.parse (gives exact line number + message)
    import ast
    try:
        ast.parse(code)
    except SyntaxError as e:
        msg = f"{type(e).__name__}: {e.msg}"
        problems.append({
            "line": e.lineno,
            "type": "Syntax Error",
            "message": msg,
            "suggestion": suggest_for(e.msg or ""),
        })
    except Exception as e:
        problems.append({
            "line": None,
            "type": "Syntax Error",
            "message": str(e),
            "suggestion": suggest_for(str(e)),
        })

    # 2) Logic/style check via pyflakes (only if syntax was OK)
    if not problems:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pyflakes", tmp_path],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                # Format: path:line:col: message
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    line_no = int(parts[1]) if parts[1].isdigit() else None
                    msg = parts[3].strip()
                    problems.append({
                        "line": line_no,
                        "type": "Warning",
                        "message": msg,
                        "suggestion": suggest_for(msg),
                    })
        except Exception:
            pass
        finally:
            os.unlink(tmp_path)

    return jsonify({"problems": problems})


if __name__ == "__main__":
    # Render provides the port via the PORT env variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
