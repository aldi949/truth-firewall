# Your coding agent says “DONE”. That doesn’t mean the job is done.

**Truth Firewall won’t accept&#x20;****`DONE`****&#x20;until the work is independently verified.**

Your coding agent says:

> “Done. Tests pass.”

Maybe it is.

Maybe it skipped a requirement.\
Maybe an edge case is broken.\
Maybe it changed something it shouldn’t have.

And then **you** have to check everything anyway.

That defeats the whole point of having an agent.

# Truth Firewall stops that.

**The agent does the work.**\
**Truth Firewall decides whether&#x20;****`DONE`****&#x20;is actually earned.**

Give Codex the task.

Walk away.
```text
Codex works
    ↓
Codex says DONE
    ↓
Truth Firewall verifies the required work
    ↓
┌──────────────────┬──────────────────┬──────────────────┐
↓                  ↓                  ↓
VERIFIED_DONE    REJECT_DONE       HUMAN_REQUIRED
                     ↓
              Codex keeps working
```
