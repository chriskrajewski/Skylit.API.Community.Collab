# Security

## API keys

A Skylit API key belongs in a local environment, never in this repository.

- Do not commit `SKYLIT_API_KEY`, `.env`, private keys, or credential files.
- `.env.example` lists the variable name and leaves the value empty.
- If a key is committed, revoke it in the Developer tab of the Skylit app and create a new key. Remove the secret from the git history before the pull request is merged.

## Report a vulnerability in this repository

Report a security issue in this repository privately through GitHub:

https://github.com/chriskrajewski/Skylit.API.Community.Collab/security/advisories/new

Private vulnerability reporting has to be enabled in the repository settings before that page accepts a report. Until it is enabled, contact a repository administrator directly and mark the report private.

Please include the project path (`members/<discord-username>/<project-id>/`) when the issue is inside a shared project, and avoid posting the secret itself in the report.
