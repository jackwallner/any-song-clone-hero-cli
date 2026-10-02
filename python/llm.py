"""LLM access shared by the guitar analysis and the drum planner.

gemini uses the REST API with GEMINI_API_KEY. claude and codex shell out to the
locally logged-in Claude Code / Codex CLI, so they run on the user's
subscription instead of an API key.
"""

import os, sys, json, re, glob, shutil, subprocess, tempfile, time

AI_PROVIDERS = ("gemini", "claude", "codex")
# Opus at high effort thinks for a while; the drum plan prompt is the longest
CLI_TIMEOUT = int(os.environ.get("SONGHERO_AI_TIMEOUT", "300"))
CLAUDE_MODEL = os.environ.get("SONGHERO_CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.environ.get("SONGHERO_CLAUDE_EFFORT", "high")
GEMINI_MODELS = ("gemini-3.1-flash-lite-preview", "gemini-2.5-flash-lite")


def _call_gemini(model, api_key, prompt, max_tokens=2048):
    """Send the prompt to the Gemini REST API and return the reply text."""
    import urllib.request

    req_data = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(url, data=req_data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())
    return result["candidates"][0]["content"]["parts"][0]["text"]


def _find_codex():
    """Locate the codex binary: $SONGHERO_CODEX_BIN, PATH, then the copies the
    Codex desktop app and the VS Code extension ship with."""
    explicit = os.environ.get("SONGHERO_CODEX_BIN")
    if explicit:
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    candidates = ["/Applications/Codex.app/Contents/Resources/codex"]
    candidates += sorted(glob.glob(os.path.expanduser(
        "~/.vscode/extensions/openai.chatgpt-*/bin/*/codex")), reverse=True)
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError("codex CLI not found (install it or set SONGHERO_CODEX_BIN)")


def _call_cli(provider, prompt):
    """Run the prompt through a locally logged-in agent CLI (Claude Code or Codex),
    so the user's subscription is used instead of an API key. Runs in an empty
    temp dir with tools disabled/read-only so the agent only answers the prompt."""
    with tempfile.TemporaryDirectory(prefix="songhero-ai-") as tmp:
        if provider == "claude":
            cmd = [os.environ.get("SONGHERO_CLAUDE_BIN", "claude"), "-p",
                   "--output-format", "text", "--tools", "",
                   "--no-session-persistence", "--strict-mcp-config",
                   "--model", CLAUDE_MODEL, "--effort", CLAUDE_EFFORT]
            # Without an API key in the environment Claude Code falls back to
            # the logged-in subscription, which is the point of this provider.
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  cwd=tmp, env=env, timeout=CLI_TIMEOUT)
            if proc.returncode != 0:
                raise RuntimeError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
            return proc.stdout

        if provider == "codex":
            out_file = os.path.join(tmp, "reply.txt")
            cmd = [_find_codex(), "exec", "--skip-git-repo-check", "--ephemeral",
                   "--sandbox", "read-only", "--color", "never",
                   "-C", tmp, "-o", out_file,
                   # Short structured answer: high reasoning effort only adds minutes
                   "-c", f'model_reasoning_effort="{os.environ.get("SONGHERO_CODEX_EFFORT", "low")}"']
            if os.environ.get("SONGHERO_CODEX_MODEL"):
                cmd += ["-m", os.environ["SONGHERO_CODEX_MODEL"]]
            cmd.append("-")
            # Same idea as above: drop the API key so codex uses the ChatGPT login.
            env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  cwd=tmp, env=env, timeout=CLI_TIMEOUT)
            if proc.returncode != 0 or not os.path.exists(out_file):
                raise RuntimeError(f"codex exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-300:]}")
            with open(out_file) as f:
                return f.read()

    raise ValueError(f"Unknown AI provider: {provider}")


def extract_json(text):
    """Parse the JSON object in an LLM reply, tolerating fences and prose."""
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find('\n')
        text = text[first_nl+1:] if first_nl > 0 else text[3:]
    if text.rstrip().endswith("```"):
        text = text.rstrip()[:-3].strip()
    start_brace = text.find('{')
    end_brace = text.rfind('}')
    if start_brace >= 0 and end_brace > start_brace:
        text = text[start_brace:end_brace+1]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fixed = re.sub(r"'(\w+)':", r'"\1":', text)
        fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
        return json.loads(fixed)


def ask_json(provider, prompt, api_key="", validate=None, max_tokens=2048):
    """Ask the provider for a JSON object. `validate(data)` returns the cleaned
    data, or None to reject it and try the next model. Returns None when every
    model/retry fails."""
    import urllib.error

    if provider == "gemini":
        if not api_key:
            return None
        # Try primary model, fall back to backup
        callers = [lambda m=model: _call_gemini(m, api_key, prompt, max_tokens)
                   for model in GEMINI_MODELS]
    else:
        callers = [lambda: _call_cli(provider, prompt)]

    for call in callers:
        for attempt in range(2):
            try:
                data = extract_json(call())
                if validate is not None:
                    data = validate(data)
                if data is None:
                    break  # Try next model
                return data
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 1:
                    time.sleep(5)
                else:
                    break  # Try next model
            except Exception as e:
                print(f"  {provider} call failed: {str(e)[:200]}", file=sys.stderr)
                if attempt < 1:
                    time.sleep(2)
                else:
                    break  # Try next model
    return None
