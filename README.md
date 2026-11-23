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

When setup receives a classifier, it saves the read-only credential and Markdown
with user-only permissions. If a cached classifier is available, the hook returns
its path immediately and refreshes it in the background. The first session
without a cache fetches the classifier directly. The same account-level
classifier is used by Vantage's Codex and Cursor integrations. Classifiers may
define custom tag dimensions, which are sent under the protected
`vantage.classification.*` namespace.

Cursor teams can use an optional, read-only classifier token to apply their
Vantage account’s tagging strategy to team usage. Team usage still sends without
custom tags when no classifier token is configured.

## Development

Run the tests with `bin/test`.

## Support

- **Vantage Documentation:** https://docs.vantage.sh
- **MCP Documentation:** https://docs.vantage.sh/vantage_mcp
- **Report Issues:** Contact Vantage support at [support@vantage.sh](mailto:support@vantage.sh)
