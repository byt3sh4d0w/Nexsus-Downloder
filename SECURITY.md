# Security Policy

## Supported versions

Nexus Downloader is distributed as source. Only the latest code on the default branch (documented as **v10**) is considered for security fixes. There are no published stable releases and no backport branches.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

1. Use GitHub's **private vulnerability reporting** (repository **Security → Report a vulnerability**), if the repository owner has enabled it.
2. If it is not enabled, open a minimal public issue that says only "I would like to report a security issue privately" (no details) so the owner can arrange a private channel.

No security e-mail address is configured for this project, and none is invented here. *(Owner action: enable private reporting in the repository settings, or add a contact address.)*

### What to include

- Affected version or commit, operating system, Python version.
- What you did, what you expected, what happened (steps a maintainer can repeat, ideally against a local test server).
- Impact as you understand it. Proof of concept files only if harmless.
- Please do **not** include real credentials, tokens, private URLs or other people's data.

### What to expect

This is a volunteer project without a security team. Reports are handled on a best-effort basis: reproduce, fix with a regression test, then disclose. Please allow a reasonable time before publishing details, and the maintainers will credit reporters who wish to be named.

## If you find a leaked credential

If you discover a secret (API key, token, password) in the repository, its history or an attachment: do not use it, report it privately as above, and **rotate or revoke it immediately** at the issuer. Deleting it from the repository does not make it safe.

## Security limitations users should know

- Downloads are verified only by size and, when the server sends one, `Content-MD5`. A successful download is not a safe download.
- Plain `http://` links are unencrypted by nature.
- Torrents expose your IP address to peers, listen on all interfaces and use libtorrent defaults (DHT, local discovery, UPnP/NAT-PMP); a verified torrent can still contain malware.
- The Networks page contacts `www.gstatic.com:443` periodically to test each adapter.
- Local history and settings are stored unencrypted in your user profile.

Details, evidence and the full list: [SECURITY_AUDIT.md](SECURITY_AUDIT.md), [SECURITY_MODEL.md](SECURITY_MODEL.md), [SECURITY_TEST_RESULTS.md](SECURITY_TEST_RESULTS.md).
