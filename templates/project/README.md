# Project name

- Discord username: your_discord_username
- Project identifier: your-project-id
- Kind: agent

Kind is one of `agent`, `api`, or `mcp`. The folder path for this project is `members/your_discord_username/your-project-id/`.

## What it does

Describe the project in a few sentences.

## Skylit surface

- Access: MCP
- Products: Flowseeker

Set Access to `REST`, `MCP`, or `REST and MCP`. Name the products this project calls: Heatseeker, Flowseeker, Tempest, or a combination.

Docs: [REST API](https://docs.skylit.ai/api-reference/introduction) and [MCP server](https://docs.skylit.ai/mcp/overview) (`https://mcp.skylit.ai/mcp`).

## How to run

1. Copy `.env.example` to `.env`.
2. Set `SKYLIT_API_KEY` in that local file. Create the key in the Developer tab of the Skylit app.
3. Replace this list with the commands a member runs to install and start the project.

## Environment variables

- `SKYLIT_API_KEY` — Skylit API key. Set it in `.env` on your machine. Leave it out of git.
