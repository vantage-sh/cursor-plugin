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

## Tokenomics

The plugin can send Cursor agent usage to [Vantage Tokenomics](https://docs.vantage.sh/vantage_tokenomics), which attributes token spend to the person, the purpose, and the work that shipped. Telemetry stays off until a Vantage telemetry token is configured. A configured token sends one record per local agent reply, with the model, token counts, repository, pull request, and ticket. It does not send prompt or response text. The Vantage MCP is separate and still uses each developer's own Vantage sign-in.

Setup also saves a read-only classifier credential and the account's current
classifier Markdown with user-only permissions. At each local agent session,
the hook refreshes the Markdown with an ETag conditional request and falls back
to the last valid copy if Vantage is unavailable. The same account-level
classifier is used by Vantage's Codex and Cursor integrations. Classifiers may
define custom tag dimensions, which are sent under the protected
`vantage.classification.*` namespace.

On a Cursor team marketplace, an admin can create a standalone classifier token
under **Tokenomics → Classifier → Manage Classifier Tokens** in Vantage. In the
Cursor dashboard, open **Plugins → Configure** on the Vantage plugin and set
**Vantage classifier token**. The team token takes precedence over a developer’s
locally saved classifier token. Keep the separate **Vantage telemetry token**
configured for usage reporting. Start a new Cursor session to load the strategy.

## Development

Run the tests with `bin/test`.

## Support

- **Vantage Documentation:** https://docs.vantage.sh
- **MCP Documentation:** https://docs.vantage.sh/vantage_mcp
- **Report Issues:** Contact Vantage support at [support@vantage.sh](mailto:support@vantage.sh)
