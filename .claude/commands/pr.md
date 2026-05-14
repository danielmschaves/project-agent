Open a GitHub pull request for the current branch with a generated summary of changes.

1. Run `git diff main...HEAD --stat` to understand what changed.
2. Read the latest `data/runs/<run_id>.json` for stage summaries.
3. Call `gh pr create` with a title and body that includes: new signals detected, new actions/blockers, status delta, and a link to the ops log.

If the branch is already up on remote, just open the PR. If not, push first:

```bash
git push -u origin $(git branch --show-current)
gh pr create --title "..." --body "..."
```
