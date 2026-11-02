#!/bin/sh
# LLM relevance cross-validation delta, then the AI fix loop on the
# SmartCrew small-contractors blog for "dirt work contractor software".
# Prints the predicted results and the estimated cost of this run.
# Needs OPENROUTER_API_KEY, an OPENAI_API_KEY that starts with sk-or-,
# a normal OPENAI_API_KEY, or ANTHROPIC_API_KEY. Without a key the judge
# is not run and the cost is $0.

set -eu
cd "$(dirname "$0")/.."

if command -v python >/dev/null 2>&1; then
  PY=python
else
  PY=python3
fi

exec "$PY" -m aeo.ranksim ai-eval \
  --query "dirt work contractor software" \
  --url "https://www.smartcrewhq.com/blog/best-software-small-dirt-work-contractors/" \
  --html "smartcrew/pr8_v3/blog-best-software-small-dirt-work-contractors.html"
