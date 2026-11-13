## Conventions
- use uv for python package manager. make virtual env using uv, use pyproject.toml for dependency management.
- when fixing the codes, add the change log(what/why/changed files/verify function) to `CHANGELOG.md`. Commit the changes with a commit message (subject + body) that groups the changes by purpose, then push to `origin`(https://github.com/hymn-fly/recruit-posting-agent.git). Never commit the secret files such as `.env`.
- Use descriptive function names that state what the function does (e.g., `get_job_posting_by_id`, `transform_posting_to_summary_string`). variable name also should be descriptive.
- When a function is longer than 10-15 lines, add comments and empty lines between its logical steps.
