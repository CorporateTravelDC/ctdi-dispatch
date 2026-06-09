# scripts/pre-commit

Pre-commit hook that scans staged diffs for known credential patterns and
rejects the commit if any are found.

## Install

    cp scripts/pre-commit .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit

## Patterns checked

| Pattern | Credential type |
|---|---|
| `sk_adjs_` | acarsdrama Jumpseat token |
| `sk-ant-api` | Anthropic API key |
| `github_pat_` | GitHub fine-grained PAT |
| `ghp_` | GitHub classic PAT |
| `ghs_` | GitHub app token |
| `Bearer <20+ chars>` | bare Bearer token in source |
| `VARNAME=<28+ char value>` | raw credential in env assignment |

## False positives

If a match is a genuine false positive (e.g. a test fixture with a
placeholder value), either:

1. Use `CHANGE_ME` or `YOUR_TOKEN_HERE` as the value -- the hook skips these
2. Use `git commit --no-verify` to bypass (use sparingly, document why)

## Secrets workflow

Values never go in source files. The correct path:

    echo "sk_adjs_..." > ~/.secrets/acarsdrama.token
    chmod 600 ~/.secrets/acarsdrama.token
    bash scripts/populate-secrets.sh
    systemctl --user restart corporatetraveldc-runner.service
