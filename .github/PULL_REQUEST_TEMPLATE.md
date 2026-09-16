## Summary

-

## Cost And Safety

- [ ] No paid/cloud tool is enabled by default.
- [ ] No full prompts, secrets, or private logs are stored.
- [ ] New commands are local-first or confirmation-gated.

## Verification

```sh
python -m ruff check .
python -m unittest discover -s tests
python -m vestahub validate
```
