# Task: t9-new-project

## Project
path: ~/t9-test-project
worker: claude
new_project: true
env_file: /tmp/t9-test.env
context:
  - global:machines

## Objective
Create a new Python project at ~/t9-test-project.

1. Initialize the project directory with git (mkdir -p, git init, initial commit)
2. Create a file called hello.py that reads the TEST_SECRET env var and prints it:
   ```python
   import os
   print(os.environ.get("TEST_SECRET", "NOT SET"))
   ```
3. Run: python3 hello.py and confirm it prints "hello-from-env-file"
   (the env var is injected from env_file)
4. Commit the hello.py file

## Done When
- test -d ~/t9-test-project/.git
- test -f ~/t9-test-project/hello.py
- python3 ~/t9-test-project/hello.py shows "hello-from-env-file"
- cd ~/t9-test-project && git log --oneline -1 shows a commit
