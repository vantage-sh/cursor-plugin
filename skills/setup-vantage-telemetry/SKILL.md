---
name: setup-vantage-telemetry
description: Connect this computer's Cursor agent usage to Vantage AI Reports, check that the connection works, or remove it. Use when the user asks to set up, check, verify, or remove Vantage telemetry.
---

# Set up Vantage telemetry

The Vantage plugin includes a hook that sends one record per local agent reply to Vantage AI Reports: the model, token counts, repository, pull request, and ticket. It never sends prompt or response text. The hook stays idle until it has a Vantage telemetry token. On Cursor Teams and Enterprise, an admin usually sets one for everyone in the plugin's configuration, and no sign-in is needed. The Vantage MCP is separate: it always uses the developer's own Vantage sign-in.

All commands below run `scripts/configure_vantage_telemetry.sh`, in this skill's directory.

## Set up

Run:

```sh
sh scripts/configure_vantage_telemetry.sh
```

- The script opens Vantage in the browser and prints the approval link. Tell the user to approve the connection there, and repeat the link in case the browser did not open.
- It waits up to 10 minutes for approval. Run it with a long timeout, or in the background, and wait for it to finish.
- The user needs permission to manage integrations in Vantage. If the script reports that sign-in was denied, tell them to ask a Vantage admin.
- When it finishes, relay its summary. Never print or ask for the token.
- If telemetry is already set up, including by a team admin, the script reports that instead of signing in. Pass `--force` only if the user asks to sign in with their own account anyway.

## Check

When the user asks whether telemetry works, run:

```sh
sh scripts/configure_vantage_telemetry.sh --status
```

It reports where the token comes from and whether Vantage accepts it. A team admin's token is only visible after the hook has run, so if the result says it is not set up, ask the user to send one agent message and check again. Records appear in AI Reports after the next local agent reply. If the Vantage MCP server is connected, the user can also look for Cursor in their AI Reports there.

## Remove

Only when the user asks to stop sending telemetry, run:

```sh
sh scripts/configure_vantage_telemetry.sh --remove
```

If the output says the token comes from the environment or a team admin, an administrator manages it, and removing the saved file does not stop it.
