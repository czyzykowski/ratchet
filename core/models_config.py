"""Model configuration: central registry of Claude model IDs used by each subsystem."""

# Interactive chat sessions (spec designer, feature designer, compiler)
# — use Opus for highest reasoning quality in human-facing flows
CHAT_MODEL = "claude-opus-4-6"

# Autonomous task execution (worker invoker, QA review, retrospective analysis)
# — use Sonnet for cost-effective high-throughput automation
WORKER_MODEL = "claude-sonnet-4-6"
