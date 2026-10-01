# Contributing

Share an AI agent, an API, or an MCP project by adding a folder under your Discord username and opening a pull request.

## Where your project lives

```text
members/<discord-username>/<project-id>/
```

Example: Alice shares an agent and an API, and Bob shares an MCP server.

```text
members/
  alice/
    README.md
    flow-sweep-agent/
    heat-proxy/
  bob/
    README.md
    tempest-tools/
```

Everything under `members/alice/` is Alice's. Everything under `members/bob/` is Bob's.

### Discord username

The member folder name is your current Discord username, the handle people use to find you in the Skylit Discord.

- Lowercase
- 2–32 characters
- Only `a-z`, `0-9`, `_`, and `.`
- One folder per person

A numeric Discord user id, an email address, and a webhook are not a username. If you change your Discord username, rename your member folder in the same pull request and update the member index.

### Project identifier

The project folder name is the project identifier. It is unique among your own projects. Another member may use the same identifier under their own folder. The full identity of a project is `members/<discord-username>/<project-id>`.

- Lowercase kebab-case
- 2–48 characters
- Starts with a letter
- Example: `flow-sweep-agent`

The kind of project is a label, recorded in the README: `agent`, `api`, or `mcp`.

## Add a project

1. Fork the repository and create a branch.
2. Copy [templates/project/](templates/project/) to `members/<discord-username>/<project-id>/`.
3. Edit the project README. Set the Discord username, project identifier, and kind, then describe what it does, which Skylit surface it uses, how to run it, and which environment variables it needs.
4. Replace `LICENSE` in the project folder with the license you choose. The root [LICENSE](LICENSE) is CC0 for the hub and the blank template. It does not license your project.
5. Copy `.env.example` values into a local `.env` when you run the project. Commit `.env.example` with an empty `SKYLIT_API_KEY`. Do not commit `.env` or a real key.
6. If this is your first project, add `members/<discord-username>/README.md` using the member page below, and add a link to it from [members/README.md](members/README.md), in alphabetical order.
7. If you already have a member page, add one line to that page for the new project.
8. Open a pull request. Fill in the Discord username and project identifier in the pull request template.

### Member page

Create `members/<discord-username>/README.md` with your first project:

```markdown
# discord_username

- Discord username: discord_username

## Projects

- [flow-sweep-agent](flow-sweep-agent/README.md) — agent — Flags unusual call sweeps on a watchlist.
```

Each line is the project identifier, the kind (`agent`, `api`, or `mcp`), and a one-line description.

## Update or remove a project

Open a pull request against the same folder. A rename of the project identifier renames the folder and updates your member page in that same pull request. Removing a project deletes that project folder and its line on your member page. If it was your last project, remove your member folder and your line in the member index in the same pull request.

## Secrets

Keep `SKYLIT_API_KEY` and every other credential out of the repository. Name required variables in `.env.example` and leave the values empty. See [SECURITY.md](SECURITY.md).

## Conduct

Participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
