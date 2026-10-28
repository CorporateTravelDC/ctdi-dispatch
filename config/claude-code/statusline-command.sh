#!/usr/bin/env bash
# Claude Code statusLine script
# Shows: [model] dir (git-branch*) | output-style | ctx:remaining% | 5h:x% 7d:y%

input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name')
dir=$(echo "$input" | jq -r '.workspace.current_dir')
style=$(echo "$input" | jq -r '.output_style.name // empty')

# Shorten home dir to ~
display_dir="${dir/#$HOME/~}"

# Git branch/status (skip optional locks so we never contend with other git ops)
git_info=""
if git -C "$dir" --no-optional-locks rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    branch=$(git -C "$dir" --no-optional-locks rev-parse --abbrev-ref HEAD 2>/dev/null)
    if [ -n "$branch" ]; then
        dirty=""
        if [ -n "$(git -C "$dir" --no-optional-locks status --porcelain 2>/dev/null)" ]; then
            dirty="*"
        fi
        git_info=" ($branch$dirty)"
    fi
fi

# Context window remaining percentage
remaining=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')
ctx_info=""
if [ -n "$remaining" ]; then
    ctx_info=" | ctx:$(printf '%.0f' "$remaining")%"
fi

# Claude.ai subscription rate limits (5h / 7d), when available
five=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
week=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
limit_info=""
if [ -n "$five" ]; then
    limit_info=" | 5h:$(printf '%.0f' "$five")%"
fi
if [ -n "$week" ]; then
    limit_info="$limit_info 7d:$(printf '%.0f' "$week")%"
fi

# Output style, only shown when non-default
style_info=""
if [ -n "$style" ] && [ "$style" != "default" ]; then
    style_info=" | $style"
fi

printf '\033[2;36m[%s]\033[0m \033[2;34m%s\033[0m\033[2;33m%s\033[0m\033[2m%s%s%s\033[0m\n' \
    "$model" "$display_dir" "$git_info" "$style_info" "$ctx_info" "$limit_info"
