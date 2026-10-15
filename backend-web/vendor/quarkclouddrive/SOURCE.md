# Quark Drive Skill CLI

- Source: [quark-clouddrive/quarkclouddrive_offical](https://github.com/quark-clouddrive/quarkclouddrive_offical), commit `509e3ada82c1ac4a251e13bac8ca9b858d125cfb`
- Version: `1.0.22` (official Skill package verified 2026-10-01)
- Package metadata: `https://open-api-drive.quark.cn/agent/v1/skill_config?req_id=<UUID>`
- Package URL: `https://pdds.quark.cn/download/stfile/yy6637362yz4y282n/quarkclouddrive-1.0.22.zip`
- Downloaded ZIP SHA-256: `4593015b0b0a2ce5ce4de8003c81671ea154aa3d9c5c12e62214c411cd5f1db4`
- Bundled `quark-drive.cjs` SHA-256: `0e132196bea28114cffd248b055f2e1bc64a521cb16c5cb5aba5e786199e572e`
- Bundled `hash-worker.cjs` SHA-256: `c9e76fa0fd418e731980636c13bfe1379bdb60e117f1d6ed7dcf83ff2187a690`
- `references/` contains the business-operation documentation from this package.
- License: Apache-2.0; the upstream license is included here.

The application calls this CLI for OAuth authorization, account checks,
file browsing/search, transfers, folders, move, rename/revert, batch file
organization/check/apply/resume/undo, share details/search/update/save,
file summary/questions and photo organization. Share records created through
the application are stored privately per site user because the CLI does not
provide a command for listing all historical shares created in Quark.
It does not run the upstream install script because that script can change the
global Node.js installation and remove a global command. The bundle is copied
into a separate private directory for each site user before invocation.

For web QR authorization, the application's `quark_login_bridge.cjs` is loaded
only during interactive login. It delivers the real official authorization URL
to the frontend instead of launching a browser on the server. The pinned CLI
remains unmodified and owns OAuth polling, token exchange and persistence.
Application share/job history is additionally bound to the current Quark
account. This does not change Quark's device-wide revocation scope.

The official package currently has no cloud-file deletion command or command
to enumerate all historical outgoing shares. Transfer `delete` removes a
local task record only. The application reports these limits explicitly.
Runtime install/update/uninstall and `resolve-agent` diagnostics are not exposed
as end-user business operations. The vendored package is reviewed and pinned;
the web server never runs the upstream global installer.
