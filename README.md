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

Telemetry is off until a Vantage telemetry token is configured. The Vantage MCP is separate: each developer signs in to it with their own Vantage account, whichever way telemetry is set up.

### Cursor Teams and Enterprise

1. In Vantage, go to **Settings > Integrations > Cursor Telemetry** and create a token. You need permission to manage integrations.
2. In the Cursor Marketplace, choose **Add to Team** on the Vantage plugin and set it to **Required** so it installs for everyone.
3. In the Cursor dashboard, open **Plugins**, choose **Configure** on the Vantage plugin, and paste the token as the **Vantage telemetry token**.

Every developer with the plugin then sends usage under that token, and each record carries the developer's Cursor email. Developers can ask the agent to check Vantage telemetry; after their next agent reply it confirms whether Vantage accepted it. To rotate the token, create a new one, update the plugin configuration, then revoke the old one in Vantage.

### Individual setup

Without a team token, each developer asks the agent:

```text
Set up Vantage telemetry
```

The agent runs the `setup-vantage-telemetry` skill, which opens Vantage in the browser. After you approve, the token is saved to `~/.config/vantage/cursor-telemetry.json` (`%APPDATA%\vantage\cursor-telemetry.json` on Windows), readable only by you. Ask the agent to check Vantage telemetry to confirm it works, or to remove Vantage telemetry to stop sending. You need permission to manage integrations in Vantage.

### Device management

You can also create a token on the Cursor Telemetry page and set these environment variables through your device management tool:

```sh
VANTAGE_CURSOR_INGEST_TOKEN=<token>
VANTAGE_CURSOR_INGEST_ENDPOINT=https://ingest.vantage.sh
```

When more than one is set, the hook uses the team plugin token first, then these environment variables, then a token saved by the setup skill.

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
