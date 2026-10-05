import re

REDACTION_PATTERNS = [
    # SSH Private Keys
    (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    # Anthropic API Keys
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{32,}\b"), "[REDACTED_ANTHROPIC_KEY]"),
    # OpenAI API Keys
    (re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{32,}\b"), "[REDACTED_OPENAI_KEY]"),
    # GitHub Tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    # Slack tokens
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9_\-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    # Generic Authorization Bearer
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{24,}\b"), "Bearer [REDACTED_TOKEN]"),
    # Passwords in URLs (e.g. postgres://user:password@host)
    (re.compile(r"://([^:]+):([^@]+)@"), r"://\1:[REDACTED_PASSWORD]@"),
]

def redact_sensitive_text(text: str) -> str:
    if not text:
        return ""
    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
