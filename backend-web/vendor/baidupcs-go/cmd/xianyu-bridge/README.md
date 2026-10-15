# Xianyu private JSON bridge

Single stdin request, single stdout response. This adapter directly calls the
pinned BaiduPCS-Go library, never the interactive CLI or `pcsconfig`. It keeps no
account state and opens no HTTP listener. Python owns authentication, per-user
locking, credential persistence, transport staging and the public HTTP API.

Credentials are supplied only in stdin:

```json
{"operation":"list","credentials":{"bduss":"<private>","stoken":"<private>","cookies":"<private>","uid":"123"},"args":{"dir":"/","page":1,"page_size":100}}
```

Success: `{"success":true,"data":{...}}`.
Failure: `{"success":false,"error_code":"network_error","kind":"network","message":"安全的固定说明","provider_code":123}`.
`provider_code` is optional. Error details, upstream response bodies, URLs and
credentials are never copied into stdout. Upstream console output goes to stderr;
the Python caller discards stderr. Debug logging is disabled irrespective of env.

## Operations

| Operation | Arguments | Data |
| --- | --- | --- |
| `profile` | `{}` | `uid` string, optional `nickname`, `capacity_bytes`, `used_bytes`; actual UK > 0 and root directory read are required |
| `list` | `dir`, `page`, `page_size` <= 1000 | `files`, `has_more`; exactly one provider page |
| `search` | `dir`, `keyword`, optional `recursive`, `page`, `page_size` <= 10000, `max_items` <= 10000 | `files`, `has_more`, `total`; fails when the result reaches the configured limit |
| `meta` | `paths` | `files` |
| `mkdir` | `path` | `status`, `files` |
| `copy`, `move` | `source`, `destination` (complete paths) | `status`, `files`; existing destination rejected; source/target metadata fingerprints verified, move source confirmed absent |
| `delete` | `paths` | `status`; metadata checked before deletion, each specified path confirmed absent afterward |
| `upload` | `local_path`, `path` | `status`, `files`; 4 MiB blocks, creation with rtype=0, then path/size/MD5 verification |
| `download` | `path`, `local_path`, `max_bytes` | `status`, `size`; no overwrite, bounded transfer, size/MD5 verification |
| `share-create` | `paths`, `password`, `period` (0, 1, 7, 30 days) | `id`, `share_url`, `passcode`, `period` |
| `share-list` | `page`, optional `include_details` | `shares`, `has_more` |
| `share-detail` | `id` (decimal string) | `id`, `pwd`, `passcode`, `shorturl`, `share_url`, `details_available`; one native lookup for existing shares |
| `share-cancel` | `ids` (decimal strings) | `status` |
| `share-transfer` | any | explicit `unsupported_operation`; no cloud mutation |

Each file has `fs_id` string, `path`, `filename`, `size`, `isdir` bool,
`server_mtime` seconds and optional `md5`.

Share rows retain provider fields: `shareId` string, `fsIds` string array,
`shortlink`, `status`, `public`, `typicalCategory`, `typicalPath`, `expiredType`,
`expiredTime` **remaining seconds**, `vCnt`, `pwd`, `details_available`. Creation
dates are not inferred. Native sharing passwords are not login credentials.
Default listing performs no detail lookup. `share-detail` retrieves any existing
share on demand without loading a whole page of passwords. `include_details=true` only looks up
the first 5 active private shares, within a 30-second total budget; unavailable
details remain explicitly marked false. Python can merge its own share cache.

## Boundaries

- Input <= 1 MiB, provider JSON and stdout <= 8 MiB.
- At most 100 paths or share IDs per mutation; root and relative path components
  are rejected for mutations.
- Upload and download <= 4 GiB. Downloads require explicit `max_bytes`; the public
  project API can impose a smaller limit.
- Local files must be absolute paths, regular files and free of symlinks. A
  downloads target must not exist. `os.Root` confines the actual file access to its
  opened directory. Optional `BAIDUPCS_BRIDGE_FILE_ROOT` confines paths further.
- All requests and redirects must use HTTPS and Baidu hostnames, no userinfo or
  nonstandard port. Certificate validation and TLS 1.2 minimum stay enabled.
- PCS requests use the maintained upstream default application ID `266719`;
  web-pan endpoints retain the upstream web application ID `250528`. They are
  separate API families. Actual authenticated read-only comparison confirmed the
  former can read PCS metadata while `250528` is rejected with error `31030`.
- Native successful PCS resource responses omit `errno`/`error_code`.
  mkdir/metadata require positive `fs_id`, exact requested paths and valid
  `isdir`/resource status. Creation is confirmed by a subsequent metadata read.
  copy/move require positive `request_id` and exactly one `extra.list` entry with
  exact `from`/`to` paths (optional positive native file IDs). Source metadata is
  captured first; target size/type and any provided MD5 must match afterward.
  Move additionally requires the source to return explicit not-found; copy
  confirms the source remains unchanged. Delete only accepts the native
  `request_id` acknowledgement after a metadata snapshot, then requires explicit
  not-found for every requested path. Network/auth failures never mean absence.
  These shapes were verified on an authorized test-file copy, never other user
  files. Unknown response fields and unrelated operations remain rejected.
- Search uses the upstream PCS search API; it cannot remove upstream platform
  limits. Responses reaching `max_items` are rejected, never silently sliced.

Run `sh cmd/xianyu-bridge/build.sh --test` from the vendor module to build and test
with the project's private Go toolchain and existing caches, without changing
global settings. Build output: `data/local-dev/runtime/baidupcs-go/xianyu-bridge`.

Tests use in-memory provider response fixtures and local temporary files, not real
accounts or cloud mutations. Live account acceptance is performed through the
project's local authenticated API after the user scans the QR code.

The adapter is part of the vendored Apache-2.0 BaiduPCS-Go project; preserve the
upstream LICENSE and attribution. See the vendor UPSTREAM.json for the pinned
commit and the separate certificate-validation patch.
