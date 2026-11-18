# Vantage Cursor Plugin

A [Cursor](https://cursor.com) plugin that connects to [Vantage](https://vantage.sh), a cloud cost management platform that helps teams understand and optimize their infrastructure spending. You can install the plugin directly from the [Cursor Marketplace](https://cursor.com/marketplace/vantage).

## Overview

This plugin gives your AI assistant direct access to your Vantage workspace through the [Vantage MCP server](https://docs.vantage.sh/vantage_mcp). With it, you can ask your assistant to query costs across cloud providers, create and manage Cost Reports, set up budgets and alerts, review savings recommendations, and explore resource-level cost data, all without leaving your editor.

## Installation

1. Open Cursor settings.
2. Select **Plugins**.
3. Click **Browse Marketplace**.
4. Search for "Vantage".
5. Click **Add to Cursor**.

## AI Reports telemetry

The plugin can send Cursor agent usage to [Vantage AI Reports](https://docs.vantage.sh). After setup, each local agent reply sends one record with the model, token counts, repository, pull request, and ticket. It never sends prompt or response text. Cloud Agent replies are skipped.

Telemetry is off until this computer has a Vantage telemetry token. To set it up, ask the agent:

```text
Set up Vantage telemetry
```

The agent runs the `setup-vantage-telemetry` skill, which opens Vantage in the browser. After you approve, the token is saved to `~/.config/vantage/cursor-telemetry.json` (`%APPDATA%\vantage\cursor-telemetry.json` on Windows), readable only by you. Ask the agent to check Vantage telemetry to confirm it works, or to remove Vantage telemetry to stop sending.

You need permission to manage integrations in Vantage. In Vantage, go to **Settings > Integrations > Cursor Telemetry** to see connected senders and revoke tokens.

### Rolling out to a team

On Cursor Teams and Enterprise, an admin can add this plugin to the team marketplace and set it to **Required** so it installs for everyone. Each developer then runs the setup prompt once.

To skip the per-developer sign-in, create a token on the Cursor Telemetry page in Vantage and set these environment variables through your device management tool. They take precedence over the saved file:

```sh
VANTAGE_CURSOR_INGEST_TOKEN=<token>
VANTAGE_CURSOR_INGEST_ENDPOINT=https://ingest.vantage.sh
```

### Requirements and options

- The hook and setup skill need Python 3. Without it, the hook does nothing.
- If the GitHub CLI (`gh`) is installed and signed in, the hook adds the open pull request for the current branch and the ticket in its title.
- Tickets are matched as `ABC-123`. To match only certain prefixes, set `VANTAGE_TICKET_PREFIXES=ENG,OPS` or add `"ticket_prefixes": ["ENG", "OPS"]` to the saved file.

## Development

Run the tests with `bin/test`.

## Support

- **Vantage Documentation:** https://docs.vantage.sh
- **MCP Documentation:** https://docs.vantage.sh/vantage_mcp
- **Report Issues:** Contact Vantage support at [support@vantage.sh](mailto:support@vantage.sh)
