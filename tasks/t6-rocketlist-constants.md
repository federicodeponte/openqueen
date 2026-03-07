# Task: t6-rocketlist-constants

## Project
path: ~/rocketlist-minimal
worker: claude
context:
  - global:machines
  - global:logins
  - project

## Objective
Clean up two files in the Rocketlist frontend:

1. Remove the leftover comment "// Queen test comment" from:
   - frontend/lib/utils.ts (first line)
   - frontend/lib/constants.ts (entire file is just this comment)

2. Populate frontend/lib/constants.ts with real constants extracted from the codebase.
   Look at frontend/lib/utils.ts and frontend/app/ to find magic values that should be constants.
   At minimum extract:
   - Score thresholds (90 = green, 80 = amber) from utils.ts getScoreColor
   - The hex color values for scores

3. Update frontend/lib/utils.ts to import and use those constants instead of hardcoded values.

4. Run the tests to make sure nothing broke:
   cd ~/rocketlist-minimal/frontend && npm run test:unit 2>&1 | tail -20

## Done When
- frontend/lib/constants.ts exists and has at least 3 exported constants
- frontend/lib/utils.ts does not contain "Queen test comment"
- frontend/lib/constants.ts does not contain "Queen test comment"
- npm run test:unit passes (exit code 0)
