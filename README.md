# Skylit community collaboration hub

This repository is a shared shelf for the Skylit community. Members share AI agents, APIs, and MCP projects built on Skylit data. The account that hosts the repository holds the files. Each project belongs to the member who shared it.

Skylit exposes options-flow and dealer-positioning data through two surfaces that share one API key:

- [REST API](https://docs.skylit.ai/api-reference/introduction)
- [MCP server](https://docs.skylit.ai/mcp/overview) at `https://mcp.skylit.ai/mcp`

Projects may use Heatseeker, Flowseeker, Tempest, or any combination of them.

## Members

The member list lives in [members/README.md](members/README.md). It is empty until the first project is merged.

A member's folder holds only that person's projects:

```text
members/
  alice/
    flow-sweep-agent/     kind: agent
    heat-proxy/           kind: api
  bob/
    gamma-wall-bot/       kind: agent
    tempest-tools/        kind: mcp
```

The folder name under `members/` is the Discord username. The folder under that is the project identifier.

## Share a project

1. Fork this repository and copy [templates/project/](templates/project/) to `members/<discord-username>/<project-id>/`.
2. Fill in the project README with your Discord username, project identifier, and kind (`agent`, `api`, or `mcp`).
3. Replace the project `LICENSE` file with the license you choose for your work.
4. On your first project, add `members/<discord-username>/README.md` and a link in the member index.
5. Open a pull request. Leave API keys and `.env` files out of the commit.

The full steps, folder rules, and checklist are in [CONTRIBUTING.md](CONTRIBUTING.md).

## How sharing works

Anyone can fork this public repository and open a pull request. That is the path for adding a project. Write access is for people who merge other members' work. A collaborator who can merge is still a collaborator. Shared ownership of the repository means a GitHub Organization with at least two Owners. Until this repository is transferred there, the personal account that hosts it is the current host.

Hub documentation and the blank template are dedicated under [CC0](LICENSE). A project under `members/` is covered only by the license file inside that project.

## Community files

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md)
- [LICENSE](LICENSE)

## Repository settings

These are changed in the GitHub settings for this repository:

- Enable Discussions.
- Set the topics `skylit`, `mcp`, and `ai-agents`.
- Replace the description "Repo for the Skylit community to colab on" with "Community hub for Skylit agents, APIs, and MCP projects".
- Enable private vulnerability reporting so [SECURITY.md](SECURITY.md) has a private path.
- When the community is ready to share administration, transfer the repository to a GitHub Organization and add at least one other Organization Owner.
