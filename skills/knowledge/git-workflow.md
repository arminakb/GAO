---
description: Git workflow — conventional commits, branching strategy table, merge-vs-rebase decision, PR discipline, and safe history practices
tags: git,workflow,commits,branching,code-review
origin: ECC (enriched)
---

# Git Workflow & Commit Discipline

Git hygiene rules for collaborative development: conventional commits, atomic
changes, clean branches, disciplined PR review, and history that stays safe to
rewrite only when it's private. `main` is always deployable; history tells the
truth about *why*, not just *what*.

## When to Reference

- Writing commit messages or structuring a change into commits
- Creating branches, opening PRs, or reviewing pull requests
- Deciding merge vs rebase, or resolving conflicts
- Cleaning up stale branches, tagging releases, undoing mistakes
- Configuring hooks to block secrets and broken commits

## Commit Conventions (Conventional Commits)

```
<type>(<scope>): <subject in imperative, <=50 chars, no period>

[body: why, not what]

[footer: Breaking changes, Closes #123]
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`,
`style`, `revert`.

```bash
# BAD: vague                        # GOOD: specific, why, links issue
git commit -m "fixed stuff"         git commit -m "fix(api): retry on 503

The external API returns 503 during peak hours. Added exponential
backoff with max 3 attempts. Closes #123"
```

Set `git config commit.template .gitmessage` to enforce the format.

## Atomic Commits

Each commit is one logical change that builds and passes tests on its own.

- Don't mix a refactor with a feature; don't mix fixes with formatting.
- Use `git add -p` to stage only related hunks.
- Squash noisy WIP commits before merge so review history is meaningful.

```python
# Pre-commit self-check (conceptual hook logic)
changed: list[str] = get_staged_files()
if any(f in GENERATED_PATHS for f in changed) and has_manual_edits(changed):
    reject("generated files must not be committed")
if scan_for_secrets(changed):
    reject("possible secret in staged diff — commit aborted")
```

## Branch Hygiene

- Name by type + ticket: `feature/JIRA-123-payment`, `fix/456-null-pointer`,
  `hotfix/critical-security-patch`, `release/1.2.0`.
- Branch from an up-to-date `main`; keep branches short-lived (days, not weeks).
- Rebase frequently onto `main` while private; delete merged branches
  (`git branch -d <name>`, `git fetch -p`).
- Stash with a message when context-switching: `git stash push -m "WIP: auth"`.

Strategies: **GitHub Flow** (feature → PR → main; default for most teams),
**trunk-based** (short branches + feature flags, strong CI), **GitFlow**
(develop/release/hotfix branches for scheduled enterprise releases).

| Strategy | Team size | Release cadence | Best for |
|----------|-----------|-----------------|----------|
| GitHub Flow | Any | Continuous | SaaS, web apps, startups |
| Trunk-based | 5+ experienced | Multiple/day | High-velocity teams, feature flags |
| GitFlow | 10+ | Scheduled | Enterprise, regulated industries |

## Merge vs Rebase

```bash
# Merge: preserves history; use for shared/pushed branches and merging to main
git checkout main && git merge feature/user-auth

# Rebase: linear history; only for LOCAL, unshared branches
git checkout feature/user-auth
git fetch origin
git rebase origin/main
git push --force-with-lease origin feature/user-auth
```

| Situation | Use |
|---|---|
| Merging feature → main | Merge (or squash-merge) |
| Updating local branch with latest main | Rebase |
| Branch pushed, others based work on it | Merge — never rebase |
| Only you, local-only, want linear history | Rebase |

**Never rebase** branches that are pushed and shared, based on by others,
protected (main/develop), or already merged. Prefer `--force-with-lease` over
`--force` — it refuses if the remote moved.

## PR Review Flow

PR title mirrors the commit convention: `feat(auth): add SSO support`.

Description covers **What / Why / How**, a testing checklist, and linked issues
(`Closes #123`). Keep PRs small and single-purpose (ideally <500 lines).
Authors: self-review first, CI green, docs updated. Reviewers: problem solved,
edge cases handled, tests sufficient, no security concerns, clean history.

## Safe History Practices

```bash
git reset --soft HEAD~1      # undo last commit, keep changes staged
git commit --amend --no-edit # add forgotten files to last commit
git revert HEAD              # undo a PUSHED commit — never rewrite public history
git push origin --delete feature/x  # remove a merged remote branch
```

- Public/shared history is immutable: use `revert`, never `reset` + force-push.
- `reset --hard` discards work — confirm before running.
- Tag releases semver (`MAJOR.MINOR.PATCH`) with annotated tags and push them:
  `git tag -a v1.2.0 -m "Release v1.2.0" && git push origin v1.2.0`.

## Never Commit Secrets

- `.env`, tokens, API keys, credentials, and private keys never enter a commit.
- Maintain a `.gitignore` that covers `.env`, `.env.*`, `node_modules/`, `dist/`,
  `coverage/`, logs, IDE dirs, and OS junk — commit it as the first change.
- Guard with a pre-commit hook:

```bash
# .git/hooks/pre-commit
if git diff --cached | grep -E '(password|api_key|secret|token)'; then
    echo "Possible secret detected. Commit aborted."; exit 1
fi
npm run lint || exit 1
```

- If a secret is committed: rotate it immediately, then scrub history
  (e.g. `git filter-repo`). Deleting the file in a later commit is not enough.

## Conflict Resolution

```bash
git status                          # list conflicted files
git mergetool                       # or edit <<<<<<< ======= >>>>>>> markers
git checkout --ours path            # or --theirs to take one side
git add path && git commit          # finish the merge
```

Prevention: small short-lived branches, frequent rebases, prompt PR merges,
and coordinating before touching shared files.

## Quick Reference

| Task | Command |
|------|---------|
| Feature branch | `git checkout -b feature/name` |
| Stage hunks | `git add -p` |
| Undo last commit (keep) | `git reset --soft HEAD~1` |
| Undo pushed commit | `git revert HEAD` |
| View history | `git log --oneline --graph --all` |
