# Security policy

## Reporting

Use GitHub's private
[Report a vulnerability](https://github.com/Burgess-Software/unofficial-matic-sdk/security/advisories/new)
flow. Do not open a public issue containing a BotToken, robot identifier,
household map, recording, network address, certificate pin, or packet capture.

Private vulnerability reporting must be enabled in the repository Security
settings before the repository is made public.

## Secret handling

This repository must contain only synthetic protocol fixtures. Before opening
a pull request, verify that no token, APK, native library, real protobuf
capture, media, map, device identifier, or absolute research path is present.

If a credential is committed, rotate/re-enroll that client credential and
remove it from Git history; deleting only the working-tree file is insufficient.
