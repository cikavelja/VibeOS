# Start here

1. Extract the repository, initialize Git, and open it in VS Code.
2. Run the preview using README.md, or inspect it before installing dependencies.
3. In Copilot Plan Mode: read .github/copilot-instructions.md, docs/ARCHITECTURE.md, docs/REQUIREMENTS.md, docs/tasks.json, docs/contracts/preview-api.md and docs/verification/STARTER_PREVIEW.md. Audit existing code against the 81 tasks. Treat the preview as provisional, not as verified implementation. Identify contract differences and propose remediation before starting M00-T01.
4. Use .github/prompts/implement-task.prompt.md for exactly one approved task. Use a separate session and verify-task.prompt.md for independent review. CI and human review are mandatory before marking VERIFIED.
5. Run python scripts/progress.py --write to regenerate the progress and traceability reports.
