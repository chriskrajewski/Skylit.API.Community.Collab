# pckt-bench: instructions for AI agents

If the user asks you to run, set up, test or evaluate an agent on **pckt-bench**, do this and nothing else first:

1. Spawn a helper agent (a sub-agent, task agent, or whatever your framework calls it) with the prompt:
   **"Read `agents/pckt-bench-orchestrator.md` in this folder and follow it exactly."**
   If you cannot spawn helpers, read that file yourself and follow it as your instructions.
2. Relay the orchestrator's questions to the user, pass their answers back, and continue it until it reports results.

Do not run the benchmark yourself without it, do not read `slices/*/signals.json` into the context of the agent being evaluated, and do not spend Skylit credits without the user's explicit approval. The user does not need to install or copy anything.
