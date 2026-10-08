# Projects

What The Hell is MCP? has no separately released projects yet. The four builds the posts call "Project 1" to "Project 4" are taught inside the posts, and their code lives under [code/](../code/) beside the other runnable companions:

| Build | Posts | Code |
|---|---|---|
| Project 1: A secure database analyst | [13](../posts/13-database-analyst/index.md), [14](../posts/14-database-writes/index.md) | [code/13-postgres-analyst/](../code/13-postgres-analyst/) |
| Project 2: A DevOps first responder | [15](../posts/15-devops-responder/index.md), [16](../posts/16-devops-remediation/index.md) | [code/15-devops-responder/](../code/15-devops-responder/) |
| Project 3: A deep research browser | [17](../posts/17-research-browser/index.md), [18](../posts/18-server-side-models/index.md) | [code/17-research-browser/](../code/17-research-browser/) |
| Project 4: One server, every client | [23](../posts/23-multi-client/index.md) | [code/23-knowledge-base/](../code/23-knowledge-base/) |

Each is a self-contained `uv` project with its own README and an in-memory test suite (`uv run --extra dev pytest` in its directory). A build that is later released as a packaged service gets its own directory here, with its own README and release notes.
